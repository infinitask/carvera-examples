"""Simulate a 3-axis CNC milling job on a rectangular stock blank and
write the remaining-stock surface as a binary STL + an optional
grayscale heightmap PNG.

Runs under `freecadcmd` because FreeCAD's bundled Python already has
numpy + Pillow (the same setup `render_stl.py` uses). The
script doesn't actually need FreeCAD itself — pure numpy for the
heightmap and a hand-rolled binary STL writer.

The simulation is 2.5D: a Z-buffer heightmap with one column per grid
cell. Exact for 3-axis milling (X, Y, Z + tool rotation) — and that's
all the Carvera Air does, so this covers every job the repo ships.

Coordinate convention (matches the Carvera Air / `cam/brass_holes/` /
`cam/relief_engrave/`):
  - X,Y stock origin = bottom-left corner viewed from above.
  - Z=0 = top of stock; negative Z = cut below the top surface.

Usage:
    freecadcmd python/gcode_simulate.py -- \\
        --stock 50x35x2 \\
        --resolution 0.15 \\
        --out path/to/remaining.stl \\
        [--heightmap path/to/heightmap.png] \\
        [--tool 5:flat:2] [--tool 6:vbit:30:0.2] \\
        01_pass.gcode 02_pass.gcode ...

Default tool table (override per-job with `--tool`):
    T1  flat endmill, 1.0 mm diameter
    T5  flat endmill, 2.0 mm diameter
    T6  V-bit, 30 deg included angle, 0.2 mm tip diameter
    T7  flat endmill, 6.0 mm diameter

`--tool` spec syntax:
    --tool <T>:flat:<diameter_mm>
    --tool <T>:vbit:<included_angle_deg>:<tip_diameter_mm>
"""
import math
import re
import sys
from pathlib import Path

import numpy as np

try:
    from PIL import Image
except ImportError:
    Image = None


def _split_args(argv):
    return argv[argv.index("--") + 1:] if "--" in argv else argv[1:]


def _die(msg):
    sys.stderr.write("gcode_simulate: " + msg + "\n")
    sys.exit(1)


def _parse_args(argv):
    args = _split_args(argv)
    opts = {
        "stock": None,
        "resolution": 0.15,
        "out": None,
        "heightmap": None,
        "tool_specs": [],
        "gcode": [],
    }
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--stock":
            i += 1
            parts = args[i].lower().split("x")
            if len(parts) != 3:
                _die("--stock expects WxHxD, got " + args[i])
            opts["stock"] = tuple(float(p) for p in parts)
        elif a == "--resolution":
            i += 1
            opts["resolution"] = float(args[i])
        elif a == "--out":
            i += 1
            opts["out"] = args[i]
        elif a == "--heightmap":
            i += 1
            opts["heightmap"] = args[i]
        elif a == "--tool":
            i += 1
            opts["tool_specs"].append(args[i])
        elif a.startswith("--"):
            _die("unknown flag " + a)
        else:
            opts["gcode"].append(a)
        i += 1
    if opts["stock"] is None:
        _die("--stock is required (WxHxD in mm)")
    if opts["out"] is None:
        _die("--out is required (path to .stl)")
    if not opts["gcode"]:
        _die("at least one .gcode file is required")
    return opts


class Tool:
    """Pre-computed tool footprint kernel for stamping into a heightmap.

    kernel[i, j] = vertical offset above the tool tip at cell offset (i, j)
    mask[i, j]   = True if (i, j) is inside the tool's swept radius
    """
    def __init__(self, kernel, mask, name, swept_radius_mm):
        self.kernel = kernel
        self.mask = mask
        self.name = name
        self.swept_radius_mm = swept_radius_mm
        self.half = kernel.shape[0] // 2  # kernel is (2H+1) x (2H+1)


def _flat_endmill_tool(diameter_mm, res_mm, name):
    R = diameter_mm / 2.0
    h = int(math.ceil(R / res_mm))
    size = 2 * h + 1
    yy, xx = np.mgrid[-h:h+1, -h:h+1] * res_mm
    r2 = xx * xx + yy * yy
    mask = r2 <= R * R
    kernel = np.zeros((size, size), dtype=np.float32)
    return Tool(kernel, mask, name, R)


def _vbit_tool(angle_deg, tip_dia_mm, max_depth_mm, res_mm, name):
    half_angle = math.radians(angle_deg / 2.0)
    tan_h = math.tan(half_angle)
    r_tip = tip_dia_mm / 2.0
    r_max = r_tip + max_depth_mm * tan_h
    h = int(math.ceil(r_max / res_mm))
    size = 2 * h + 1
    yy, xx = np.mgrid[-h:h+1, -h:h+1] * res_mm
    r = np.sqrt(xx * xx + yy * yy)
    kernel = np.where(r <= r_tip, 0.0, (r - r_tip) / tan_h).astype(np.float32)
    mask = r <= r_max
    return Tool(kernel, mask, name, r_max)


def _build_tool_table(user_specs, max_depth_mm, res_mm):
    table = {
        1: _flat_endmill_tool(1.0, res_mm, "flat 1.0 mm endmill"),
        5: _flat_endmill_tool(2.0, res_mm, "flat 2.0 mm endmill"),
        6: _vbit_tool(30.0, 0.2, max_depth_mm, res_mm,
                      "30 deg V-bit, 0.2 mm tip"),
        7: _flat_endmill_tool(6.0, res_mm, "flat 6.0 mm endmill"),
    }
    for spec in user_specs:
        parts = spec.split(":")
        if len(parts) < 3:
            _die(f"--tool spec needs T:type:..., got {spec}")
        try:
            t_num = int(parts[0])
        except ValueError:
            _die(f"--tool T must be int, got {parts[0]}")
        kind = parts[1].lower()
        if kind == "flat":
            if len(parts) != 3:
                _die("--tool T:flat:diameter_mm")
            d = float(parts[2])
            table[t_num] = _flat_endmill_tool(d, res_mm, f"flat {d} mm endmill")
        elif kind == "vbit":
            if len(parts) != 4:
                _die("--tool T:vbit:included_angle_deg:tip_diameter_mm")
            a = float(parts[2])
            tip = float(parts[3])
            table[t_num] = _vbit_tool(a, tip, max_depth_mm, res_mm,
                                      f"{a} deg V-bit, {tip} mm tip")
        else:
            _die(f"--tool unknown kind {kind} (use 'flat' or 'vbit')")
    return table


# G-code parser — minimal, just enough for the Carvera-style output of
# our pipelines (relief_engrave, bicolour_engrave, brass_holes). Handles:
# - G0/G1 absolute moves (G90 is assumed; we don't honour G91 — the
#   pipelines never emit it)
# - Tool changes (T<n> M6)
# Anything else is ignored, which is what we want — the sim doesn't
# care about feed rates, spindle, coolant.
WORD_RE = re.compile(
    r"([A-Za-z])\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)"
)


def _strip_comments(line):
    out = []
    depth = 0
    for ch in line:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == ";":
            break
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def _iter_moves(paths):
    for p in paths:
        for raw in Path(p).read_text().splitlines():
            line = _strip_comments(raw).strip()
            if not line:
                continue
            words = {}
            for letter, num in WORD_RE.findall(line):
                words[letter.upper()] = num
            if "T" in words and words.get("M") == "6":
                yield ("T", None, None, None, int(float(words["T"])))
                continue
            if "G" in words:
                g = words["G"]
                if g in ("0", "00"):
                    _iter_moves.last_motion = "G0"
                elif g in ("1", "01"):
                    _iter_moves.last_motion = "G1"
            if not any(k in words for k in ("X", "Y", "Z")):
                continue
            x = float(words["X"]) if "X" in words else None
            y = float(words["Y"]) if "Y" in words else None
            z = float(words["Z"]) if "Z" in words else None
            yield (_iter_moves.last_motion, x, y, z, None)
_iter_moves.last_motion = "G0"


def _stamp(heightmap, ix, iy, z, tool):
    h = tool.half
    nx, ny = heightmap.shape
    x0 = ix - h; x1 = ix + h + 1
    y0 = iy - h; y1 = iy + h + 1
    sx0 = max(0, x0); sx1 = min(nx, x1)
    sy0 = max(0, y0); sy1 = min(ny, y1)
    if sx0 >= sx1 or sy0 >= sy1:
        return
    kx0 = sx0 - x0; kx1 = kx0 + (sx1 - sx0)
    ky0 = sy0 - y0; ky1 = ky0 + (sy1 - sy0)
    sub_kernel = tool.kernel[kx0:kx1, ky0:ky1]
    sub_mask = tool.mask[kx0:kx1, ky0:ky1]
    target = heightmap[sx0:sx1, sy0:sy1]
    new_z = z + sub_kernel
    np.minimum(target, new_z, out=target, where=sub_mask)


def _sweep(heightmap, p0, p1, tool, res, max_step_mm):
    dx = p1[0] - p0[0]; dy = p1[1] - p0[1]; dz = p1[2] - p0[2]
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    if length == 0:
        ix = int(round(p1[0] / res))
        iy = int(round(p1[1] / res))
        _stamp(heightmap, ix, iy, p1[2], tool)
        return
    n = max(1, int(math.ceil(length / max_step_mm)))
    for k in range(n + 1):
        t = k / n
        x = p0[0] + t * dx
        y = p0[1] + t * dy
        z = p0[2] + t * dz
        ix = int(round(x / res))
        iy = int(round(y / res))
        _stamp(heightmap, ix, iy, z, tool)


def _build_surface_mesh(heightmap, res, stock_w, stock_h, stock_d):
    nx, ny = heightmap.shape
    xs = np.arange(nx, dtype=np.float32) * res
    ys = np.arange(ny, dtype=np.float32) * res
    bottom_z = -float(stock_d)
    H = np.clip(heightmap, bottom_z, 0.0)

    nx_q = nx - 1
    ny_q = ny - 1
    n_top = nx_q * ny_q * 2
    X0, Y0 = np.meshgrid(xs[:-1], ys[:-1], indexing="ij")
    X1, Y1 = np.meshgrid(xs[1:],  ys[1:],  indexing="ij")
    Z00 = H[:-1, :-1]; Z10 = H[1:, :-1]
    Z01 = H[:-1, 1:];  Z11 = H[1:,  1:]
    tri_top = np.empty((n_top, 3, 3), dtype=np.float32)
    # CCW from above so the +Z normal is correct
    tri_top[0::2, 0, 0] = X0.ravel(); tri_top[0::2, 0, 1] = Y0.ravel(); tri_top[0::2, 0, 2] = Z00.ravel()
    tri_top[0::2, 1, 0] = X1.ravel(); tri_top[0::2, 1, 1] = Y0.ravel(); tri_top[0::2, 1, 2] = Z10.ravel()
    tri_top[0::2, 2, 0] = X1.ravel(); tri_top[0::2, 2, 1] = Y1.ravel(); tri_top[0::2, 2, 2] = Z11.ravel()
    tri_top[1::2, 0, 0] = X0.ravel(); tri_top[1::2, 0, 1] = Y0.ravel(); tri_top[1::2, 0, 2] = Z00.ravel()
    tri_top[1::2, 1, 0] = X1.ravel(); tri_top[1::2, 1, 1] = Y1.ravel(); tri_top[1::2, 1, 2] = Z11.ravel()
    tri_top[1::2, 2, 0] = X0.ravel(); tri_top[1::2, 2, 1] = Y1.ravel(); tri_top[1::2, 2, 2] = Z01.ravel()

    walls = []
    # y=0 edge, outward normal -Y
    for i in range(nx - 1):
        za = float(H[i, 0]); zb = float(H[i+1, 0])
        xa = float(xs[i]);   xb = float(xs[i+1])
        walls.append(((xa, 0.0, za), (xb, 0.0, bottom_z), (xb, 0.0, zb)))
        walls.append(((xa, 0.0, za), (xa, 0.0, bottom_z), (xb, 0.0, bottom_z)))
    # y=max edge, outward normal +Y
    y_top = float(ys[-1])
    for i in range(nx - 1):
        za = float(H[i, -1]); zb = float(H[i+1, -1])
        xa = float(xs[i]);    xb = float(xs[i+1])
        walls.append(((xa, y_top, za), (xb, y_top, zb), (xb, y_top, bottom_z)))
        walls.append(((xa, y_top, za), (xb, y_top, bottom_z), (xa, y_top, bottom_z)))
    # x=0 edge, outward normal -X
    for j in range(ny - 1):
        za = float(H[0, j]); zb = float(H[0, j+1])
        ya = float(ys[j]);    yb = float(ys[j+1])
        walls.append(((0.0, ya, za), (0.0, yb, zb), (0.0, yb, bottom_z)))
        walls.append(((0.0, ya, za), (0.0, yb, bottom_z), (0.0, ya, bottom_z)))
    # x=max edge, outward normal +X
    x_top = float(xs[-1])
    for j in range(ny - 1):
        za = float(H[-1, j]); zb = float(H[-1, j+1])
        ya = float(ys[j]);     yb = float(ys[j+1])
        walls.append(((x_top, ya, za), (x_top, yb, bottom_z), (x_top, yb, zb)))
        walls.append(((x_top, ya, za), (x_top, ya, bottom_z), (x_top, yb, bottom_z)))
    tri_walls = (np.array(walls, dtype=np.float32)
                 if walls else np.zeros((0, 3, 3), dtype=np.float32))

    # Bottom face (Z = -stock_d), outward normal -Z
    x0 = 0.0; x1 = float(xs[-1]); y0 = 0.0; y1 = float(ys[-1])
    tri_bot = np.array([
        ((x0, y0, bottom_z), (x1, y1, bottom_z), (x1, y0, bottom_z)),
        ((x0, y0, bottom_z), (x0, y1, bottom_z), (x1, y1, bottom_z)),
    ], dtype=np.float32)

    return np.concatenate([tri_top, tri_walls, tri_bot], axis=0)


def _write_binary_stl(path, tris):
    n = len(tris)
    e1 = tris[:, 1] - tris[:, 0]
    e2 = tris[:, 2] - tris[:, 0]
    nrm = np.cross(e1, e2)
    L = np.linalg.norm(nrm, axis=1, keepdims=True)
    L[L == 0] = 1
    nrm = (nrm / L).astype(np.float32)
    rec_dtype = np.dtype([
        ("normal", np.float32, 3),
        ("v0", np.float32, 3),
        ("v1", np.float32, 3),
        ("v2", np.float32, 3),
        ("attr", np.uint16),
    ])
    rec = np.zeros(n, dtype=rec_dtype)
    rec["normal"] = nrm
    rec["v0"] = tris[:, 0]
    rec["v1"] = tris[:, 1]
    rec["v2"] = tris[:, 2]
    with open(path, "wb") as f:
        f.write(b"\0" * 80)
        f.write(np.uint32(n).tobytes())
        f.write(rec.tobytes())


def _write_heightmap_png(path, heightmap, stock_d):
    if Image is None:
        sys.stderr.write("gcode_simulate: Pillow not available, skipping PNG\n")
        return
    H = np.clip(heightmap, -stock_d, 0.0)
    norm = ((H + stock_d) / stock_d * 255.0).astype(np.uint8)
    # heightmap is indexed [x, y]; PIL image is (height_rows, width_cols)
    # with row 0 at the top. Transpose and flip Y so the image reads
    # left→right = +X, top→bottom = -Y.
    img = Image.fromarray(norm.T[::-1].copy(), mode="L")
    img.save(path)


def main():
    opts = _parse_args(sys.argv)
    sw, sh, sd = opts["stock"]
    res = opts["resolution"]
    nx = int(math.ceil(sw / res)) + 1
    ny = int(math.ceil(sh / res)) + 1
    heightmap = np.zeros((nx, ny), dtype=np.float32)
    max_depth = float(sd)
    tool_table = _build_tool_table(opts["tool_specs"], max_depth, res)

    moves = list(_iter_moves(opts["gcode"]))
    n_motion = sum(1 for m in moves if m[0] in ("G0", "G1"))
    n_tools = sum(1 for m in moves if m[0] == "T")
    sys.stderr.write(
        f"simulating {n_motion} moves, {n_tools} tool changes on "
        f"{sw}x{sh}x{sd} mm stock at {res} mm/cell ({nx}x{ny} cells)\n"
    )

    active = None
    pos = [0.0, 0.0, 5.0]
    n_progress = 0
    for kind, x, y, z, t in moves:
        if kind == "T":
            if t not in tool_table:
                _die(
                    f"unknown tool T{t} — override with --tool {t}:flat:<dia> "
                    f"or --tool {t}:vbit:<deg>:<tip>"
                )
            active = tool_table[t]
            sys.stderr.write(f"  T{t}: {active.name}\n")
            continue
        new_pos = [
            x if x is not None else pos[0],
            y if y is not None else pos[1],
            z if z is not None else pos[2],
        ]
        if active is None:
            pos = new_pos
            continue
        # Max step along a segment so the swept disc has no gaps. For a
        # flat tool, half the cutter radius is comfortably gap-free
        # while keeping numpy stamps reasonably sparse. For a V-bit,
        # the relevant radius is at the deepest Z along the segment;
        # we just use a small fixed step there because most V-bit
        # segments in our gcode are already ~res long.
        if active.kernel.max() == 0:
            max_step = max(res, 0.5 * active.swept_radius_mm)
        else:
            max_step = max(res, 0.25)
        _sweep(heightmap, pos, new_pos, active, res, max_step)
        pos = new_pos
        n_progress += 1
        if n_progress % 20000 == 0:
            sys.stderr.write(f"  ...{n_progress}/{n_motion} moves\n")

    np.clip(heightmap, -sd, 0.0, out=heightmap)
    sys.stderr.write(
        f"heightmap Z range [{heightmap.min():.3f}, {heightmap.max():.3f}] mm\n"
    )

    tris = _build_surface_mesh(heightmap, res, sw, sh, sd)
    _write_binary_stl(opts["out"], tris)
    sys.stderr.write(f"wrote {opts['out']} ({len(tris)} triangles)\n")
    if opts["heightmap"]:
        _write_heightmap_png(opts["heightmap"], heightmap, sd)
        sys.stderr.write(f"wrote {opts['heightmap']}\n")


main()
