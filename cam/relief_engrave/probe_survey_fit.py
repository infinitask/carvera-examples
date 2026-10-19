#!/usr/bin/env python3
"""Fit a 3D-probe survey of a box/boss workpiece into machinable geometry.

Input: the survey log written while driving the Makera 3D probe
(docs/carvera-probe-uneven-stock.md), one touch per line::

    Z x=5 y=5 M466 Z-12 [PRB:-284.2172,-196.7188,-62.5391:1]
    Y- x=8 d2.5 M466 Y8 [PRB:-281.2172,-202.6641,-65.0203:1]
    X+ y=8 d6.5 M466 X-8 [PRB:-187.4109,-193.7188,-65.6703:1]

The label's first token names the face (``Z`` for a top touch, ``X-``,
``X+``, ``Y-``, ``Y+`` for a side face); the ``[PRB:...]`` is the RAW
machine-coordinate trigger point read from ``$#`` after the cycle (raw
= NOT tip-compensated; the cycle's printed "Final Position" IS
compensated - don't mix them).

Output (JSON on stdout, or --out): the top plane ``z = a + b*x + c*y``
in work coords, the four top-edge lines, their intersections (the true
top-face corners, CCW from X-/Y-), and the least-squares residuals so a
warped face is visible. Everything is in the WCS whose origin/TL0 you
pass (``$#`` -> ``[G54:...]`` and ``[TL0:...]``):

    work_X = PRB_X - G54_X          work_Z = PRB_Z - (G54_Z + TL0)

Side faces are probed at >= 2 depths per station so a drafted (cast /
band-sawn) face can be extrapolated up to the LOCAL top plane: the
relief/chamfer meets the top edge, not the face at probing depth.
"""
import argparse
import json
import re
import sys

import numpy as np

PRB_RE = re.compile(r"\[PRB:(-?[\d.]+),(-?[\d.]+),(-?[\d.]+):1\]")


def parse(lines):
    touches = []
    for ln in lines:
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        m = PRB_RE.search(ln)
        if not m:
            raise SystemExit(f"no PRB in: {ln!r}")
        face = ln.split()[0]
        if face not in ("Z", "X-", "X+", "Y-", "Y+"):
            raise SystemExit(f"bad face label {face!r} in: {ln!r}")
        touches.append((face, tuple(float(v) for v in m.groups())))
    return touches


def fit(touches, g54, tl0, tip_r):
    def to_work(p):
        return np.array([p[0] - g54[0], p[1] - g54[1], p[2] - (g54[2] + tl0)])

    z_pts = np.array([to_work(p) for f, p in touches if f == "Z"])
    if len(z_pts) < 3:
        raise SystemExit("need >= 3 Z touches for the top plane")
    A = np.c_[np.ones(len(z_pts)), z_pts[:, 0], z_pts[:, 1]]
    (a, b, c), *_ = np.linalg.lstsq(A, z_pts[:, 2], rcond=None)
    z_res = z_pts[:, 2] - A @ np.array([a, b, c])

    def top(x, y):
        return a + b * x + c * y

    # per face: face-position (tip-compensated) as a fn of (along, z),
    # extrapolated to the local top -> top-edge points -> line fit
    faces = {}
    for face, sign, axis, along in (("X-", +1, 0, 1), ("X+", -1, 0, 1),
                                    ("Y-", +1, 1, 0), ("Y+", -1, 1, 0)):
        pts = [to_work(p) for f, p in touches if f == face]
        if len(pts) < 2:
            raise SystemExit(f"need >= 2 touches on face {face}")
        # group by station (the along-coordinate)
        stations = {}
        for p in pts:
            stations.setdefault(round(p[along], 2), []).append(p)
        edge_pts = []
        drafts = []
        for s, ps in sorted(stations.items()):
            ps = np.array(ps)
            pos = ps[:, axis] + sign * tip_r          # face position at each z
            if len(ps) >= 2:
                k, m0 = np.polyfit(ps[:, 2], pos, 1)  # pos = k*z + m0
                drafts.append(k)
            else:
                k, m0 = 0.0, pos[0]                     # no draft info: vertical
            # local top at this station's top-edge (x,y)
            x_e = pos.mean() if axis == 0 else s
            y_e = s if axis == 0 else pos.mean()
            zt = top(x_e, y_e)
            face_at_top = k * zt + m0
            edge_pts.append((face_at_top, s) if axis == 0 else (s, face_at_top))
        edge_pts = np.array(edge_pts)
        # line through the stations: for X faces x = f(y), for Y faces y = f(x)
        if axis == 0:
            slope, icpt = np.polyfit(edge_pts[:, 1], edge_pts[:, 0], 1)
            line = ("x=", slope, icpt)     # x = slope*y + icpt
        else:
            slope, icpt = np.polyfit(edge_pts[:, 0], edge_pts[:, 1], 1)
            line = ("y=", slope, icpt)     # y = slope*x + icpt
        faces[face] = dict(edge_points=edge_pts.tolist(), line=line,
                           draft_mm_per_mm=[float(d) for d in drafts])

    def isect(xf, yf):
        # x = sx*y + ix ; y = sy*x + iy
        _, sx, ix = faces[xf]["line"]
        _, sy, iy = faces[yf]["line"]
        x = (sx * iy + ix) / (1 - sx * sy)
        y = sy * x + iy
        return [float(x), float(y)]

    corners = [isect("X-", "Y-"), isect("X+", "Y-"), isect("X+", "Y+"), isect("X-", "Y+")]
    cn = np.array(corners)
    return dict(
        g54=list(g54), tl0=tl0, tip_r=tip_r,
        plane=dict(a=float(a), b=float(b), c=float(c),
                   max_residual_mm=float(np.abs(z_res).max())),
        faces={k: dict(edge_points=v["edge_points"], line=list(v["line"]),
                       draft_mm_per_mm=v["draft_mm_per_mm"]) for k, v in faces.items()},
        corners_ccw_from_xminus_yminus=corners,
        extents=dict(x_len_bottom=float(np.linalg.norm(cn[1] - cn[0])),
                     x_len_top=float(np.linalg.norm(cn[2] - cn[3])),
                     y_len_left=float(np.linalg.norm(cn[3] - cn[0])),
                     y_len_right=float(np.linalg.norm(cn[2] - cn[1])),
                     rotation_deg=float(np.degrees(np.arctan2(cn[1][1] - cn[0][1], cn[1][0] - cn[0][0])))),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("survey")
    ap.add_argument("--g54", nargs=3, type=float, required=True, metavar=("X", "Y", "Z"))
    ap.add_argument("--tl0", type=float, required=True)
    ap.add_argument("--tip-r", type=float, default=0.975)
    ap.add_argument("--out")
    a = ap.parse_args()
    with open(a.survey) as fh:
        touches = parse(fh)
    res = fit(touches, a.g54, a.tl0, a.tip_r)
    js = json.dumps(res, indent=2)
    if a.out:
        with open(a.out, "w") as fh:
            fh.write(js + "\n")
    print(js)


if __name__ == "__main__":
    main()
