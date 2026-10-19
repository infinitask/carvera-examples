#!/usr/bin/env python3
"""
Part-off slot for a relief job: a straight slot across the block (along
Y) that separates the profiled region from the rest, cut in Z layers
from the top with a small endmill, entering from one Y face in free air
and stopping short of the other face to leave a bridge (so neither half
can break loose and nothing hits the anchor on that side).

Frame/tilt come from the same geometry.json as relief_gcode.py, so the
slot lines up with the relief footprint on the block after a re-zero
(`scripts/cnc_measure_box_stock.sh --origin-only`).

    python3 cam/relief_engrave/part_off_gcode.py <job>/geometry.json \
        --out <job>/<name>_03_part_off.gcode \
        --slot-u-max 55.0 --width 6.4 --depth 27 --bridge 3 --from plus

--depth is below the LOCAL top; keep it <= flute length + a little (the
slot is wider than the tool so the shank is free, but chips still have to
climb past the flute end) and well short of the stick-out (collet nut).
Cut what you can, flip the block about Y, re-zero, run again from the
other side (`--from minus` if the bridge must stay on the same physical
face).  Emits: tool legend, T M6, M851 (extractor), safety asserts.
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from relief_gcode import Frame, load_tool  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

ap = argparse.ArgumentParser()
ap.add_argument("geometry")
ap.add_argument("--out", required=True)
ap.add_argument("--tool", type=int, default=2)
ap.add_argument("--tool-file", default=str(REPO / "tools/endmill_3.175mm_1F_25L_oflute_plastic.fctb"))
ap.add_argument("--slot-u-max", type=float, required=True, help="u (X along the block) of the slot's X+ wall")
ap.add_argument("--width", type=float, default=6.4, help="slot width (mm) - >= 2x tool dia so the shank never rubs")
ap.add_argument("--depth", type=float, default=27.0, help="depth below the LOCAL top (mm)")
ap.add_argument("--bridge", type=float, default=0.0, help="material left uncut at the far Y face (mm); 0 = cut through, run out --lead beyond it")
ap.add_argument("--obstacle-z", type=float, default=None, help="work Z of the highest fixture/bracket under the run-out path (e.g. bracket top); floor must stay >= 2 mm above it")
ap.add_argument("--from", dest="side", choices=("plus", "minus"), default="plus", help="Y face the tool enters from")
ap.add_argument("--stepdown", type=float, default=None)
ap.add_argument("--stepover", type=float, default=None)
ap.add_argument("--feed", type=float, default=None)
ap.add_argument("--rpm", type=int, default=None)
ap.add_argument("--extractor-pct", type=int, default=100)
ap.add_argument("--safe-z", type=float, default=5.0)
ap.add_argument("--lead", type=float, default=2.0, help="air gap outside the entry face where each pass starts")
a = ap.parse_args()

t = load_tool(a.tool_file)
r = t["dia"] / 2
stepdown = a.stepdown or t["stepdown"]
stepover = a.stepover or min(t["stepover"], t["dia"] / 2)
feed = a.feed or t["feed"]
rpm = a.rpm or t["rpm"]
fr = Frame(json.load(open(a.geometry)))

if a.width < t["dia"] * 2 - 1e-9:
    print(f"WARNING: slot {a.width} < 2x tool dia {t['dia']} - the plain shank may rub the walls below the flute")
if a.depth > t["flute"] + 3:
    raise SystemExit(f"depth {a.depth} is more than 3 mm past the {t['flute']} mm flute - chips can't climb that; flip instead")

# passes across the slot width, from the X+ wall toward X-
n_pass = max(1, math.ceil((a.width - t["dia"]) / stepover) + 1)
step = (a.width - t["dia"]) / (n_pass - 1) if n_pass > 1 else 0.0
u_centres = [a.slot_u_max - r - i * step for i in range(n_pass)]
u_min_wall = u_centres[-1] - r
if u_min_wall < 0:
    raise SystemExit("slot runs off the X- face")

# along-slot extent in v: entry side in free air, exit side leaves the bridge
through = a.bridge <= 0
if a.side == "plus":
    v_start, v_end = fr.v_len + r + a.lead, (-r - a.lead) if through else (a.bridge + r)
else:
    v_start, v_end = -r - a.lead, (fr.v_len + r + a.lead) if through else (fr.v_len - a.bridge - r)
assert min(v_start, v_end) >= -r - a.lead - 1e-6 and max(v_start, v_end) <= fr.v_len + r + a.lead + 1e-6

# local top along the slot (tilt across 35 mm is ~0.02 mm; take the worst = highest)
tops = [fr.top(*fr.to_work(u, v)) for u in u_centres for v in (max(0.0, min(v_start, v_end)), min(fr.v_len, max(v_start, v_end)))]
top_hi, top_lo = max(tops), min(tops)
z_floor = top_lo - a.depth
if a.obstacle_z is not None and z_floor < a.obstacle_z + 2.0:
    raise SystemExit(f"floor {z_floor:.2f} is within 2 mm of the obstacle at Z{a.obstacle_z} - reduce --depth")
n_lvl = math.ceil(a.depth / stepdown)
levels = [max(z_floor, top_hi - (i + 1) * stepdown) for i in range(n_lvl)]
if levels[-1] > z_floor + 1e-9:
    levels.append(z_floor)

req_tlo = a.depth + 3.0
lines = [
    f"(Part-off slot - {t['name']})",
    "(Origin: M464 S2 probed corner; frame + tilt plane from probe_survey_fit.py)",
    "(=== Tool table - load these bits before running ===)",
    f"(T{a.tool} = {t['name']})",
    f"(REQUIRED stick-out: measured TLO must be >= {req_tlo:.1f} mm; check $# TL0 after the M6 before cutting)",
    f"(slot u {u_min_wall:.2f}..{a.slot_u_max:.2f}, {a.width} wide, {a.depth} deep below local top, "
    f"enter from Y{'+' if a.side == 'plus' else '-'}, " + ("through - runs out past the far face" if through else f"{a.bridge} mm bridge left at the far face") + ")",
    "G90 G94", "G17", "G54", "G21",
    f"T{a.tool} M6",
    f"M3 S{rpm}",
    f"M851 S{a.extractor_pct}",
    f"G0 Z{a.safe_z:.3f}",
]
xy = lambda u, v: "X%.3f Y%.3f" % fr.to_work(u, v)
cut_len = 0.0
for li, z in enumerate(levels):
    z_ret = (top_hi + 0.5) if li == 0 else levels[li - 1] + 0.5   # inside the previous layer's cut, 0.5 above its floor
    for u in u_centres:
        lines.append(f"G0 {xy(u, v_start)}")
        lines.append(f"G0 Z{z_ret:.3f}")
        lines.append(f"G1 Z{z:.3f} F{t['plunge']:.0f}")            # in free air outside the entry face
        lines.append(f"G1 {xy(u, v_end)} F{feed:.0f}")
        lines.append(f"G0 Z{z_ret:.3f}")
        cut_len += abs(v_end - v_start)
    lines.append(f"G0 Z{a.safe_z:.3f}") if li == len(levels) - 1 else None
lines = [l for l in lines if l]
lines += ["M05", "M852", "G17 G90", "G28", "M30"]

# ---- asserts on every emitted move
for l in lines:
    if l.startswith(("G0", "G1")) and "Z" in l:
        zv = float(l.split("Z")[1].split()[0])
        assert z_floor - 2e-3 <= zv <= a.safe_z + 2e-3, l
    if l.startswith(("G0", "G1")) and "X" in l:
        xv = float(l.split("X")[1].split()[0]); yv = float(l.split("Y")[1].split()[0])
        # back to (u, v) and check the bridge + block bounds
        d = (xv - fr.o[0], yv - fr.o[1])
        u = d[0] * fr.ex[0] + d[1] * fr.ex[1]; v = d[0] * fr.ey[0] + d[1] * fr.ey[1]
        assert -2e-3 <= u - r and u + r <= fr.u_max + 2e-3, f"slot outside block in u: {l}"
        if through:
            assert -r - a.lead - 2e-3 <= v <= fr.v_len + r + a.lead + 2e-3, f"run-out too far: {l}"
        elif a.side == "plus":
            assert v - r >= a.bridge - 2e-3, f"bridge violated: {l}"
        else:
            assert v + r <= fr.v_len - a.bridge + 2e-3, f"bridge violated: {l}"

Path(a.out).write_text("\n".join(lines) + "\n")
mins = cut_len / feed + len(levels) * n_pass * 0.1
print(f"tool: {t['name']} d{t['dia']} flute {t['flute']} F{feed:.0f} S{rpm} stepdown {stepdown} stepover {stepover}")
print(f"slot: u {u_min_wall:.2f}..{a.slot_u_max:.2f} ({n_pass} passes at u {', '.join(f'{u:.2f}' for u in u_centres)}), "
      f"v {min(v_start, v_end):.2f}..{max(v_start, v_end):.2f} (block v 0..{fr.v_len:.2f}), " + ("through" if through else f"bridge {a.bridge}"))
print(f"z: local top {top_hi:.3f}/{top_lo:.3f}, floor {z_floor:.3f}, {len(levels)} levels; required TLO >= {req_tlo:.1f}"
      + (f"; obstacle at Z{a.obstacle_z} -> {z_floor - a.obstacle_z:.1f} mm clear" if a.obstacle_z is not None else ""))
print(f"wrote {a.out} ({len(lines)} lines, ~{mins:.0f} min)")
