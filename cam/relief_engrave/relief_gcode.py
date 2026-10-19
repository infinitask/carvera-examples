#!/usr/bin/env python3
"""Heightmap -> probe-compensated relief G-code for the Carvera Air.

Turns a grayscale depth map (white = original top surface, black = full
relief depth) into raster toolpaths on a box workpiece whose real
geometry came from the 3D-probe survey (``probe_survey_fit.py`` ->
``geometry.json``): the image is laid onto the *measured* top-face quad
(rotation and true corners honoured) and every Z rides the *measured*
tilt plane, exactly like the chamfer jobs in
docs/carvera-probe-uneven-stock.md.

Two tools, three files:

1. ``<stem>_01_rough_oflute.gcode``  - flat single-flute O-flute (Tn):
   Z-level raster roughing (``--stepdown``) leaving ``--stock``, then a
   0-stock raster finish of the WHOLE region (the flat tool is what
   finishes the -depth floor and every wall the ball can't reach), then
   a cross-direction pass over the ball-excluded (steep-wall) areas.
2. ``<stem>_02_finish_ball.gcode``   - 2 mm ball nose (Tm): fine raster
   finish over everything the ball can reach WITHOUT its Ø3.175 shank
   touching a wall (2 mm ball, 12 mm flute, shank wider than the cutter
   - see tools/ballnose_2mm_2F_12L_plastic.fctb).

Tool-offset surfaces are computed properly (not just "image minus
radius"): flat tip = max of the terrain under the tool disc; ball tip =
max over the disc of terrain + sqrt(r^2 - d^2) - r. So the cutter can
never dip into a neighbouring rise, and neither tool ever cuts the
untouched top outside the image footprint (that region is modelled as
terrain = top plane, so the offsets keep the tool up).

Image placement: ``--orient`` picks which way the image's top points
(``head-xplus``: portrait along the X axis, un-mirrored for a viewer at
the front with the head to their right; ``head-yplus``: top toward Y+,
un-mirrored looking down from the front), ``--fit width|height`` (or
``--image-width-mm``) sets the scale, ``--anchor plus|minus|centre``
slides the footprint along the free axis, ``--margin`` keeps off the
edges.  Whatever isn't under the image footprint is left untouched.
Without a probe survey, ``--nominal-stock X Y`` assumes a square block
with a flat top at Z0 and its X-/Y- top corner on the WCS origin.

Safety asserts before anything is written: every cutting Z inside
[-depth-0.5, +0.5] relative to the local top; every XY endpoint inside
the block outline + one tool radius; ball tip never inside a shank
collision.  Refuses to write on failure.
"""
import argparse
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image


# ---------------------------------------------------------------- tools
REPO_ROOT = Path(__file__).resolve().parents[2]


def _mm(v):
    """'3.175 mm' / '700 mm/min' / '12000' / 3.175 -> float."""
    return float(str(v).split()[0])


def load_tool(path):
    """Read a tools/*.fctb (FreeCAD PathBit JSON, our translated schema) into
    the numbers this emitter needs.  The .fctb is the single source of truth
    for geometry + feeds; CLI flags only override."""
    d = json.load(open(path))
    par, att = d["parameter"], d["attribute"]
    return dict(
        name=d["name"],
        dia=_mm(par["Diameter"]),
        flute=_mm(par.get("CuttingEdgeLength", par.get("CuttingEdgeHeight"))),
        shank=_mm(par.get("ShankDiameter", par["Diameter"])),
        feed=_mm(att["HorizFeed"]),
        plunge=_mm(att["VertFeed"]),
        rpm=int(_mm(att["SpindleSpeed"])),
        stepdown=_mm(att["Stepdown"]),
        stepover=_mm(att["Stepover"]),
    )


# ---------------------------------------------------------------- geometry
class Frame:
    """Work-coordinate frame of the measured top face: origin = corner
    C1 (X-/Y-), u along C1->C2 (the Y- edge), v = u rotated +90 deg."""

    def __init__(self, geometry):
        c = np.array(geometry["corners_ccw_from_xminus_yminus"], float)
        self.c = c
        self.o = c[0]
        ex = c[1] - c[0]
        self.u_len = float(np.linalg.norm(ex))
        self.ex = ex / self.u_len
        self.ey = np.array([-self.ex[1], self.ex[0]])
        # width across v: mean distance of C3, C4 from the u axis
        self.v_len = float(np.mean([np.dot(c[2] - c[0], self.ey), np.dot(c[3] - c[0], self.ey)]))
        # X+ edge: the smaller u of C2/C3 keeps us on the block
        self.u_max = float(min(np.dot(c[1] - c[0], self.ex), np.dot(c[2] - c[0], self.ex)))
        p = geometry["plane"]
        self.pa, self.pb, self.pc = p["a"], p["b"], p["c"]

    def to_work(self, u, v):
        x = self.o[0] + u * self.ex[0] + v * self.ey[0]
        y = self.o[1] + u * self.ex[1] + v * self.ey[1]
        return x, y

    def top(self, x, y):
        return self.pa + self.pb * x + self.pc * y


# ---------------------------------------------------------------- offsets
def disc_offsets(r_px):
    r = int(math.ceil(r_px))
    out = []
    for di in range(-r, r + 1):
        for dj in range(-r, r + 1):
            d = math.hypot(di, dj)
            if d <= r_px:
                out.append((di, dj, d))
    return out


def shifted_max(z, di, dj, fill):
    """z shifted so out[i,j] = z[i+di, j+dj] (fill outside)."""
    n, m = z.shape
    out = np.full_like(z, fill)
    i0, i1 = max(0, -di), min(n, n - di)
    j0, j1 = max(0, -dj), min(m, m - dj)
    if i1 > i0 and j1 > j0:
        out[i0:i1, j0:j1] = z[i0 + di:i1 + di, j0 + dj:j1 + dj]
    return out


def flat_tip(terrain, r_mm, res, fill):
    tip = np.full_like(terrain, -np.inf)
    for di, dj, _ in disc_offsets(r_mm / res):
        np.maximum(tip, shifted_max(terrain, di, dj, fill), out=tip)
    return tip


def ball_tip(terrain, r_mm, res, fill):
    tip = np.full_like(terrain, -np.inf)
    for di, dj, d in disc_offsets(r_mm / res):
        lift = math.sqrt(max(r_mm ** 2 - (d * res) ** 2, 0.0)) - r_mm
        np.maximum(tip, shifted_max(terrain, di, dj, fill) + lift, out=tip)
    return tip


# ---------------------------------------------------------------- paths
def decimate(s, z, tol):
    """Douglas-Peucker on a (s, z) polyline; returns index list."""
    keep = np.zeros(len(s), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(s) - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        sa, za, sb, zb = s[a], z[a], s[b], z[b]
        t = (s[a + 1:b] - sa) / (sb - sa)
        dev = np.abs(z[a + 1:b] - (za + t * (zb - za)))
        k = int(np.argmax(dev))
        if dev[k] > tol:
            k += a + 1
            keep[k] = True
            stack.append((a, k))
            stack.append((k, b))
    return np.nonzero(keep)[0]


class Emitter:
    def __init__(self, frame, tool_no, tool_name, rpm, feed, plunge, safe_z, depth, r_tool, extractor_pct=100):
        self.f = frame
        self.lines = []
        self.feed, self.plunge, self.safe_z = feed, plunge, safe_z
        self.depth, self.r_tool = depth, r_tool
        self.dist_cut = 0.0
        self.dist_rapid = 0.0
        self.pos = None
        self.zs = []
        self.xys = []
        o = self.lines.append
        o(f"(Relief engrave - {tool_name})")
        o("(Origin: M464 S2 probed corner; frame + tilt plane from probe_survey_fit.py)")
        o("(=== Tool table - load these bits before running ===)")
        o(f"(T{tool_no} = {tool_name})")
        o("G90 G94")
        o("G17")
        o("G54")
        o("G21")
        o(f"T{tool_no} M6")
        o(f"M3 S{rpm}")
        o(f"M851 S{extractor_pct}")   # ext-out dust extractor (docs/carvera-air.md); epoxy dust wants 100%
        o(f"G0 Z{safe_z:.4f}")   # safe height first - play starts from wherever the tip is

    def _xy(self, u, v):
        x, y = self.f.to_work(u, v)
        return x, y

    def rapid_to(self, u, v):
        x, y = self._xy(u, v)
        if self.pos is not None and abs(self.pos[2] - self.safe_z) > 1e-6:
            self.lines.append(f"G0 Z{self.safe_z:.4f}")
            self.dist_rapid += abs(self.safe_z - self.pos[2])
        if self.pos is not None:
            self.dist_rapid += math.hypot(x - self.pos[0], y - self.pos[1])
        self.lines.append(f"G0 X{x:.4f} Y{y:.4f}")
        self.xys.append((u, v))
        self.pos = (x, y, self.safe_z)

    def plunge_to(self, z):
        # rapid to 1 mm above, then feed
        zr = z + 1.0
        if zr < self.safe_z:
            self.lines.append(f"G0 Z{zr:.4f}")
            self.dist_rapid += self.safe_z - zr
        self.lines.append(f"G1 Z{z:.4f} F{self.plunge:.0f}")
        self.dist_cut += max(0.0, min(zr, self.safe_z) - z)
        self.zs.append(z)
        self.pos = (self.pos[0], self.pos[1], z)

    def cut_polyline(self, us, vs, zs):
        first = True
        for u, v, z in zip(us, vs, zs):
            x, y = self._xy(u, v)
            self.lines.append(f"G1 X{x:.4f} Y{y:.4f} Z{z:.4f}" + (f" F{self.feed:.0f}" if first else ""))
            first = False
            self.dist_cut += math.dist(self.pos, (x, y, z))
            self.pos = (x, y, z)
            self.zs.append(z)
            self.xys.append((u, v))

    def finish(self):
        o = self.lines.append
        o(f"G0 Z{self.safe_z:.4f}")
        o("M05")
        o("M852")
        o("G17 G90")
        o("G28")
        o("M30")

    def minutes(self, rapid_feed=3000.0):
        return self.dist_cut / self.feed + self.dist_rapid / rapid_feed


def raster(em, U, V, Z, valid, along_u, tol, step_px, ztop_of):
    """Raster the height field Z (index [i=u, j=v]) where valid; lines run
    along u (rows over j) or along v.  step_px = line spacing in grid px."""
    n, m = Z.shape
    if along_u:
        idx_lines = range(0, m, step_px)
    else:
        idx_lines = range(0, n, step_px)
    flip = False
    for k in idx_lines:
        if along_u:
            us, vs, zs, ok = U[:, k], V[:, k], Z[:, k], valid[:, k]
        else:
            us, vs, zs, ok = U[k, :], V[k, :], Z[k, :], valid[k, :]
        if not ok.any():
            continue
        # split into runs of valid samples
        edges = np.flatnonzero(np.diff(np.r_[0, ok.astype(int), 0]))
        runs = list(zip(edges[::2], edges[1::2]))
        if flip:
            runs = runs[::-1]
        for a, b in runs:
            if b - a < 2:
                continue
            sl = slice(a, b)
            uu, vv, zz = us[sl], vs[sl], zs[sl]
            if flip:
                uu, vv, zz = uu[::-1], vv[::-1], zz[::-1]
            s = np.hypot(uu - uu[0], vv - vv[0])
            keep = decimate(s, zz, tol)
            em.rapid_to(uu[0], vv[0])
            em.plunge_to(float(zz[0]))
            em.cut_polyline(uu[keep[1:]], vv[keep[1:]], zz[keep[1:]])
        flip = not flip


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("heightmap", help="8-bit grayscale PNG: white = top surface, black = -depth, black background")
    ap.add_argument("geometry", nargs="?", default=None,
                    help="geometry.json from probe_survey_fit.py (measured corners + tilt plane); omit with --nominal-stock")
    ap.add_argument("--nominal-stock", nargs=2, type=float, metavar=("X", "Y"), default=None,
                    help="no probe survey: assume a square block of this size with a flat top at Z0 and its X-/Y- top corner at the WCS origin")
    ap.add_argument("--out-stem", required=True)
    ap.add_argument("--orient", choices=("head-xplus", "head-yplus"), default="head-xplus",
                    help="image 'up' direction on the block: head-xplus = image top toward X+ (portrait along the X axis, "
                         "un-mirrored for a viewer at the front with the head to their right); head-yplus = image top toward Y+ "
                         "(un-mirrored looking down from the front)")
    ap.add_argument("--fit", choices=("width", "height"), default="width",
                    help="which image dimension spans the block: width -> the axis the image's width lies along, height -> the other")
    ap.add_argument("--anchor", choices=("plus", "minus", "centre"), default="plus",
                    help="where the footprint sits along the free axis (plus = against the +X/+Y face, minus = against the -X/-Y face)")
    ap.add_argument("--margin", type=float, default=0.0, help="keep-out from every block edge (mm) - e.g. an edge chamfer")
    ap.add_argument("--image-width-mm", type=float, default=None,
                    help="explicit image width in mm instead of --fit (height follows the aspect)")
    ap.add_argument("--depth", type=float, default=20.0, help="black = this far below the local top")
    ap.add_argument("--res", type=float, default=0.05, help="grid mm/px")
    ap.add_argument("--rough-tool", type=int, default=2, help="tool number for the flat rough/wall tool")
    ap.add_argument("--rough-tool-file", default="tools/endmill_3.175mm_1F_25L_oflute_plastic.fctb")
    ap.add_argument("--rough-feed", type=float, default=None, help="override the .fctb HorizFeed")
    ap.add_argument("--rough-plunge", type=float, default=None)
    ap.add_argument("--rough-rpm", type=int, default=None)
    ap.add_argument("--stepdown", type=float, default=None, help="override the .fctb Stepdown")
    ap.add_argument("--rough-stepover", type=float, default=None, help="override the .fctb Stepover")
    ap.add_argument("--stock", type=float, default=0.3)
    ap.add_argument("--flat-finish-stepover", type=float, default=0.5)
    ap.add_argument("--wall-stepover", type=float, default=0.3)
    ap.add_argument("--ball-tool", type=int, default=3, help="tool number for the ball finish tool")
    ap.add_argument("--ball-tool-file", default="tools/ballnose_2mm_2F_12L_plastic.fctb")
    ap.add_argument("--ball-feed", type=float, default=None)
    ap.add_argument("--ball-plunge", type=float, default=None)
    ap.add_argument("--ball-rpm", type=int, default=None)
    ap.add_argument("--ball-stepover", type=float, default=None, help="override the .fctb Stepover")
    ap.add_argument("--ball-collar-dia", type=float, default=0.0,
                    help="diameter of a depth-stop collar / nut on the ball shank (0 = none)")
    ap.add_argument("--ball-collar-from", type=float, default=0.0,
                    help="height above the tip where that collar starts")
    ap.add_argument("--floor-snap", type=float, default=0.02,
                    help="image values below this (0-1) are snapped to the floor")
    ap.add_argument("--tol", type=float, default=0.005, help="polyline decimation tolerance (mm)")
    ap.add_argument("--extractor-pct", type=int, default=100,
                    help="M851 S<pct> dust-extractor power (100 for epoxy: fine abrasive dust)")
    ap.add_argument("--safe-z", type=float, default=5.0, help="rapid height above work Z0")
    a = ap.parse_args()

    def _resolve(rel):
        p = Path(rel)
        return p if p.is_absolute() or p.exists() else REPO_ROOT / rel
    rt = load_tool(_resolve(a.rough_tool_file))
    bt = load_tool(_resolve(a.ball_tool_file))
    if bt["shank"] > bt["dia"] + 1e-6 and bt["flute"] <= 0:
        raise SystemExit("ball tool file needs CuttingEdgeLength (shank is wider than the cutter)")
    # tool numbers/geometry/feeds: .fctb unless overridden
    a.rough_dia, a.rough_flute = rt["dia"], rt["flute"]
    a.rough_feed = a.rough_feed if a.rough_feed is not None else rt["feed"]
    a.rough_plunge = a.rough_plunge if a.rough_plunge is not None else rt["plunge"]
    a.rough_rpm = a.rough_rpm if a.rough_rpm is not None else rt["rpm"]
    a.stepdown = a.stepdown if a.stepdown is not None else rt["stepdown"]
    a.rough_stepover = a.rough_stepover if a.rough_stepover is not None else rt["stepover"]
    a.rough_tool_name = rt["name"]
    a.ball_dia, a.ball_flute, a.ball_shank = bt["dia"], bt["flute"], bt["shank"]
    a.ball_feed = a.ball_feed if a.ball_feed is not None else bt["feed"]
    a.ball_plunge = a.ball_plunge if a.ball_plunge is not None else bt["plunge"]
    a.ball_rpm = a.ball_rpm if a.ball_rpm is not None else bt["rpm"]
    a.ball_stepover = a.ball_stepover if a.ball_stepover is not None else bt["stepover"]
    a.ball_tool_name = bt["name"]
    print(f"rough tool: {rt['name']} Ø{a.rough_dia} flute {a.rough_flute} F{a.rough_feed} S{a.rough_rpm} "
          f"stepdown {a.stepdown} stepover {a.rough_stepover}")
    print(f"ball tool:  {bt['name']} Ø{a.ball_dia} flute {a.ball_flute} shank {a.ball_shank} F{a.ball_feed} "
          f"S{a.ball_rpm} stepover {a.ball_stepover}")

    if a.geometry:
        geo = json.load(open(a.geometry))
    elif a.nominal_stock:
        sx, sy = a.nominal_stock
        geo = dict(corners_ccw_from_xminus_yminus=[[0, 0], [sx, 0], [sx, sy], [0, sy]],
                   plane=dict(a=0.0, b=0.0, c=0.0))
        print(f"NO PROBE SURVEY: assuming a square {sx} x {sy} block, flat top at Z0, corner at the WCS origin")
    else:
        raise SystemExit("give geometry.json (probe survey) or --nominal-stock X Y")
    fr = Frame(geo)
    img = np.asarray(Image.open(a.heightmap).convert("L")).astype(float) / 255.0
    img[img < a.floor_snap] = 0.0      # near-black background -> exactly the floor
    ih, iw = img.shape
    # ---- placement: image axes on the (u, v) frame -----------------------
    # R = image 'right' direction, D = image 'down' direction, in (u, v).
    if a.orient == "head-xplus":
        R, D = np.array([0.0, -1.0]), np.array([-1.0, 0.0])   # width along v, height along u
        w_axis, h_axis = 1, 0
    else:
        R, D = np.array([1.0, 0.0]), np.array([0.0, -1.0])    # width along u, height along v
        w_axis, h_axis = 0, 1
    block = [fr.u_max, fr.v_len]                              # block extent along u, v
    avail = [block[0] - 2 * a.margin, block[1] - 2 * a.margin]
    if a.image_width_mm:
        W = a.image_width_mm
        H = W * ih / iw
    elif a.fit == "width":
        W = avail[w_axis]
        H = W * ih / iw
    else:
        H = avail[h_axis]
        W = H * iw / ih
    ext = [0.0, 0.0]
    ext[w_axis], ext[h_axis] = W, H                            # footprint extent along u, v
    if ext[0] > avail[0] + 1e-6 or ext[1] > avail[1] + 1e-6:
        raise SystemExit(f"image footprint {ext[0]:.1f} x {ext[1]:.1f} (u x v) does not fit the block "
                         f"{avail[0]:.1f} x {avail[1]:.1f} inside the margin")
    # footprint origin (min u, min v): the fitted axis is centred/against margin, the free axis by --anchor
    f0 = [0.0, 0.0]
    for ax in (0, 1):
        slack = avail[ax] - ext[ax]
        if ax == (h_axis if (a.fit == "width" and not a.image_width_mm) else w_axis) or a.image_width_mm:
            pos = {"plus": slack, "minus": 0.0, "centre": slack / 2}[a.anchor] if slack > 1e-9 else 0.0
        else:
            pos = slack / 2
        f0[ax] = a.margin + pos
    fu0, fv0 = f0
    fu1, fv1 = fu0 + ext[0], fv0 + ext[1]
    # image top-left corner in (u, v): the corner from which R and D both point into the footprint
    TL = np.array([fu0 if R[0] > 0 or D[0] > 0 else fu1, fv0 if R[1] > 0 or D[1] > 0 else fv1])
    print(f"placement: {a.orient}, image {W:.2f} x {H:.2f} mm, footprint u {fu0:.2f}..{fu1:.2f} v {fv0:.2f}..{fv1:.2f}"
          + (f" (margin {a.margin})" if a.margin else ""))
    u_max = fr.u_max
    r_flat = a.rough_dia / 2
    r_ball = a.ball_dia / 2
    r_shank = a.ball_shank / 2
    res = a.res
    marg = max(r_flat, r_shank) + 0.6
    # grid: footprint + a tool margin all round; any untouched top inside
    # the block is modelled as top plane so the offsets keep the tools out
    u_lo, u_hi = fu0 - marg, fu1 + marg
    v_lo, v_hi = fv0 - marg, fv1 + marg
    n = int(round((u_hi - u_lo) / res)) + 1
    m = int(round((v_hi - v_lo) / res)) + 1
    us = u_lo + np.arange(n) * res
    vs = v_lo + np.arange(m) * res
    U, V = np.meshgrid(us, vs, indexing="ij")
    X, Y = fr.to_work(U, V)
    TOP = fr.top(X, Y)
    # relief height (0 = top, -depth = black) sampled from the image via
    # the placement axes
    du, dv = U - TL[0], V - TL[1]
    col_mm = du * R[0] + dv * R[1]
    row_mm = du * D[0] + dv * D[1]
    col = col_mm / W * (iw - 1)
    row = row_mm / H * (ih - 1)
    in_img = (col_mm >= -1e-9) & (col_mm <= W + 1e-9) & (row_mm >= -1e-9) & (row_mm <= H + 1e-9)
    ci = np.clip(np.rint(col).astype(int), 0, iw - 1)
    ri = np.clip(np.rint(row).astype(int), 0, ih - 1)
    relief = -a.depth * (1.0 - img[ri, ci])
    Lv = fr.v_len
    on_block = (U >= 0) & (U <= u_max) & (V >= 0) & (V <= Lv)
    floor = TOP - a.depth
    terrain = np.where(in_img, TOP + relief, np.where(on_block, TOP, floor))
    untouched = on_block & ~in_img

    print(f"frame: u_len {fr.u_len:.3f} v_len {Lv:.3f} u_max {u_max:.3f}; grid {n}x{m} @ {res} mm")

    # ---- offset surfaces
    fill = float(floor.min()) - 1.0
    print("computing flat-tool offset ...")
    tip_flat = flat_tip(terrain, r_flat, res, fill)
    print("computing ball-tool offset ...")
    tip_ball = ball_tip(terrain, r_ball, res, fill)
    print("computing shank clearance ...")
    shank_top = flat_tip(terrain, r_shank + 0.15, res, fill)   # highest terrain under the shank disc
    ball_ok = shank_top <= tip_ball + a.ball_flute - 0.5
    if a.ball_collar_dia > 0:
        print("computing collar clearance ...")
        # generous margins on the collar: +0.5 mm radial, 1.0 mm vertical
        collar_top = flat_tip(terrain, a.ball_collar_dia / 2 + 0.5, res, fill)
        ball_ok &= collar_top <= tip_ball + a.ball_collar_from - 1.0

    # never let a tool centre sit where its disc would touch the untouched top
    flat_center_ok = flat_tip(untouched.astype(float), r_flat + 0.05, res, 0.0) < 0.5
    ball_center_ok = flat_tip(untouched.astype(float), r_shank + 0.05, res, 0.0) < 0.5

    # ---- outline of where anything is to be cut (tool centre domain)
    # flat tool: anywhere its offset tip is below the local top by > 0.02
    flat_cut = (tip_flat < TOP - 0.02) & flat_center_ok
    # ball only where it reaches lower than the flat tool did (i.e. there is
    # something left to finish: curved surface, slope, small detail) - not
    # the flat floor the O-flute already finished
    ball_cut = (tip_ball < TOP - 0.02) & ball_center_ok & ball_ok & (tip_ball < tip_flat - 0.03)
    # keep tool centres inside the footprint + one radius (the offsets +
    # *_center_ok already keep them off any untouched top; this just stops
    # them wandering into free air beyond the open faces)
    flat_cut &= (U >= fu0 - r_flat) & (U <= fu1 + r_flat) & (V >= fv0 - r_flat) & (V <= fv1 + r_flat)
    ball_cut &= (U >= fu0 - r_ball) & (U <= fu1 + r_ball) & (V >= fv0 - r_ball) & (V <= fv1 + r_ball)

    step_r = max(1, int(round(a.rough_stepover / res)))
    step_ff = max(1, int(round(a.flat_finish_stepover / res)))
    step_w = max(1, int(round(a.wall_stepover / res)))
    step_b = max(1, int(round(a.ball_stepover / res)))
    safe = a.safe_z

    # ================= file 1: O-flute
    em = Emitter(fr, a.rough_tool, a.rough_tool_name, a.rough_rpm, a.rough_feed, a.rough_plunge,
                 safe, a.depth, r_flat, a.extractor_pct)
    em.lines.append(f"(Stage 1: Z-level roughing, stepdown {a.stepdown}, stepover {a.rough_stepover}, stock {a.stock})")
    target = tip_flat + a.stock
    top_ref = float(TOP.max())
    level = top_ref
    prev = top_ref + 1.0
    zmin = float(target[flat_cut].min())
    k = 0
    while level > zmin:
        level = max(top_ref - (k + 1) * a.stepdown, zmin)
        Z = np.maximum(target, level)
        valid = flat_cut & (target < prev - 0.01)
        raster(em, U, V, Z, valid, along_u=True, tol=a.tol, step_px=step_r, ztop_of=TOP)
        prev = level
        k += 1
        if level <= zmin:
            break
    em.lines.append(f"(Stage 2: 0-stock finish with the flat tool, stepover {a.flat_finish_stepover})")
    raster(em, U, V, tip_flat, flat_cut, along_u=True, tol=a.tol, step_px=step_ff, ztop_of=TOP)
    em.lines.append(f"(Stage 3: cross pass over ball-excluded walls, stepover {a.wall_stepover})")
    wall = flat_cut & ~(ball_ok)      # areas the ball can't visit
    # dilate the wall region a little so its skirt gets the cross pass too
    wall = flat_tip(wall.astype(float), 0.6, res, 0.0) > 0.5
    wall &= flat_cut
    raster(em, U, V, tip_flat, wall, along_u=False, tol=a.tol, step_px=step_w, ztop_of=TOP)
    em.finish()
    rough_lines, rough_min = em.lines, em.minutes()
    rough_zs, rough_xys = np.array(em.zs), np.array(em.xys)

    # ================= file 2: ball
    em2 = Emitter(fr, a.ball_tool, a.ball_tool_name, a.ball_rpm, a.ball_feed, a.ball_plunge,
                  safe, a.depth, r_ball, a.extractor_pct)
    em2.lines.append(f"(Stage 4: ball finish, stepover {a.ball_stepover}; shank-safe areas only)")
    raster(em2, U, V, tip_ball, ball_cut, along_u=True, tol=a.tol, step_px=step_b, ztop_of=TOP)
    em2.finish()
    ball_lines, ball_min = em2.lines, em2.minutes()
    ball_zs, ball_xys = np.array(em2.zs), np.array(em2.xys)

    # ---- safety asserts
    for name, zs, xys, r in (("rough", rough_zs, rough_xys, r_flat), ("ball", ball_zs, ball_xys, r_ball)):
        assert len(zs), f"{name}: no cutting moves"
        assert zs.min() > float(TOP.min()) - a.depth - 0.5, f"{name}: Z too deep {zs.min()}"
        assert zs.max() < float(TOP.max()) + 0.5, f"{name}: Z above top {zs.max()}"
        assert xys[:, 0].min() >= fu0 - r - 0.01 and xys[:, 0].max() <= fu1 + r + 0.01, f"{name}: outside footprint (u)"
        assert xys[:, 1].min() >= fv0 - r - 0.01 and xys[:, 1].max() <= fv1 + r + 0.01, f"{name}: outside footprint (v)"
        # and never over the untouched top: sample the centre-ok mask at every endpoint
        ok = flat_center_ok if name == "rough" else ball_center_ok
        ii = np.clip(np.rint((xys[:, 0] - u_lo) / res).astype(int), 0, n - 1)
        jj = np.clip(np.rint((xys[:, 1] - v_lo) / res).astype(int), 0, m - 1)
        assert ok[ii, jj].all(), f"{name}: tool centre over untouched top"

    # spindle-nose clearance: the collet nut sits one stick-out (= measured
    # TLO on 2.1.0c) above the tip, so a short bit pushed deep into the
    # collet lands the nut on the top face at the deep points.  Say what
    # each file needs, and stamp it in the header so it's checked at the M6.
    need_rough = -float(rough_zs.min()) + 3.0
    need_ball = -float(ball_zs.min()) + 3.0
    if a.ball_collar_dia > 0:
        # with a modelled collar the clearance is enforced per point; the nut
        # itself sits above the collar, so the header rule is the collar start
        need_ball = a.ball_collar_from
    for lines, need in ((rough_lines, need_rough), (ball_lines, need_ball)):
        lines.insert(4, f"(REQUIRED stick-out: measured TLO must be >= {need:.1f} mm; check $# TL0 after the M6 before cutting)")
    if a.ball_collar_dia > 0:
        ball_lines.insert(5, f"(ball shank modelled with a {a.ball_collar_dia:g} mm collar from {a.ball_collar_from:g} mm above the tip"
                             f" - the mask keeps it clear of the terrain)")

    stem = Path(a.out_stem)
    f1 = stem.with_name(stem.name + "_01_rough_oflute.gcode")
    f2 = stem.with_name(stem.name + "_02_finish_ball.gcode")
    f1.write_text("\n".join(rough_lines) + "\n")
    f2.write_text("\n".join(ball_lines) + "\n")

    # ---- previews (always committed alongside the G-code)
    def save_png(arr, path, lo, hi):
        g = np.clip((arr - lo) / (hi - lo), 0, 1)
        Image.fromarray((g * 255).astype(np.uint8)).transpose(Image.TRANSPOSE).save(path)
    save_png(terrain - TOP, stem.with_name(stem.name + "_preview_terrain.png"), -a.depth, 0)
    save_png(np.where(ball_cut, 1.0, np.where(flat_cut, 0.5, 0.0)),
             stem.with_name(stem.name + "_preview_coverage.png"), 0, 1)

    print(f"wrote {f1} ({len(rough_lines)} lines, ~{rough_min:.0f} min cutting+rapids, {k} rough levels)")
    print(f"wrote {f2} ({len(ball_lines)} lines, ~{ball_min:.0f} min)")
    print(f"ball covers {ball_cut.sum() / max(1, (tip_flat < TOP - 0.02).sum()) * 100:.0f}% of the cut area; "
          f"flat-only (walls/floor) {(flat_cut & ~ball_cut).sum() * res * res:.0f} mm^2")
    print(f"REQUIRED stick-out (measured TLO): rough tool >= {need_rough:.1f} mm, ball >= {need_ball:.1f} mm "
          f"- verify $# TL0 after each M6 before playing")
    print(f"Z range rough {rough_zs.min():.3f}..{rough_zs.max():.3f}  ball {ball_zs.min():.3f}..{ball_zs.max():.3f}")


if __name__ == "__main__":
    main()
