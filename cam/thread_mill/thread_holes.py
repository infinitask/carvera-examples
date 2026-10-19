#!/usr/bin/env python3
"""
Threaded holes on a probed box workpiece: helical-bore the pilot with an
endmill / ball nose, then thread-mill it with a Makera multi-tooth thread
mill (teeth over 2 mm at the tip, pitch = thread pitch, so one continuous
helix at 1 pitch per turn cuts the whole thread; climb = G3 rising).

    python3 cam/thread_mill/thread_holes.py <job>/geometry.json --out <file>.gcode \
        --hole M3 8 10 --hole M3 8 24 --hole M5 40 10 --hole M5 40 24

Per-size recipe (pilot dia/depth, thread depth, tools) lives in RECIPES;
tools are read from tools/*.fctb.  Frame + tilt come from geometry.json
(scripts/cnc_measure_box_stock.sh) so Z is relative to the LOCAL top and
XY follow the measured X- edge.  One file, one Tn M6 per tool section.
"""
import argparse
import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "cam/relief_engrave"))
from relief_gcode import Frame, load_tool  # noqa: E402

TOOLS = {
    "oflute": (2, "tools/endmill_3.175mm_1F_25L_oflute_plastic.fctb"),
    "ball2":  (3, "tools/ballnose_2mm_2F_12L_plastic.fctb"),
    "tm_m3":  (4, "tools/threadmill_m3_2.42mm_3F_plastic.fctb"),
    "tm_m5":  (5, "tools/threadmill_m5_4mm_3F_plastic.fctb"),
}
# major, pitch, pilot dia, pilot depth, thread depth, pilot tool, thread tool
RECIPES = {
    "M3": dict(major=3.0, pitch=0.5, pilot_dia=2.6, pilot_depth=7.5, thread_depth=6.0, pilot="ball2", tm="tm_m3",
               pilot_pitch=0.25, pilot_feed=300),
    "M5": dict(major=5.0, pitch=0.8, pilot_dia=4.3, pilot_depth=12.0, thread_depth=10.5, pilot="oflute", tm="tm_m5",
               pilot_pitch=0.5, pilot_feed=500),
}
SEG = 36   # linear segments per turn (no G2/G3 - keeps the sim + firmware paths boring)

ap = argparse.ArgumentParser()
ap.add_argument("geometry")
ap.add_argument("--out", required=True)
ap.add_argument("--hole", nargs=3, action="append", metavar=("SIZE", "U", "V"), required=True,
                help="thread size + centre in block u,v (mm from the X-/Y- top corner)")
ap.add_argument("--safe-z", type=float, default=5.0)
ap.add_argument("--extractor-pct", type=int, default=100)
a = ap.parse_args()

fr = Frame(json.load(open(a.geometry)))
holes = [(s.upper(), float(u), float(v)) for s, u, v in a.hole]
for s, u, v in holes:
    if s not in RECIPES:
        raise SystemExit(f"no recipe for {s}")
    r = RECIPES[s]["major"] / 2 + 1.0
    assert r <= u <= fr.u_max - r and r <= v <= fr.v_len - r, f"{s} at ({u},{v}) too close to an edge"

tools = {k: (n, load_tool(REPO / p)) for k, (n, p) in TOOLS.items()}
lines = []
L = lines.append


def xy(u, v):
    x, y = fr.to_work(u, v)
    return x, y


def helix(cx, cy, r, z0, z1, pitch, feed, ccw=True, close=True):
    """linearised helix about (cx,cy) at radius r from z0 to z1 (either
    direction), 1 pitch per turn; optional closing full circle at z1."""
    turns = abs(z1 - z0) / pitch
    n = max(1, int(math.ceil(turns * SEG)))
    sgn = 1 if ccw else -1
    for i in range(1, n + 1):
        t = i / n
        ang = sgn * 2 * math.pi * turns * t
        L(f"G1 X{cx + r * math.cos(ang):.3f} Y{cy + r * math.sin(ang):.3f} Z{z0 + (z1 - z0) * t:.3f} F{feed:.0f}")
    if close:
        ang0 = sgn * 2 * math.pi * turns
        for i in range(1, SEG + 1):
            ang = ang0 + sgn * 2 * math.pi * i / SEG
            L(f"G1 X{cx + r * math.cos(ang):.3f} Y{cy + r * math.sin(ang):.3f} Z{z1:.3f} F{feed:.0f}")


def section(key, body):
    n, t = tools[key]
    L(f"(--- T{n} {t['name']} ---)")
    L(f"T{n} M6")
    L(f"M3 S{t['rpm']}")
    L(f"M851 S{a.extractor_pct}")
    L(f"G0 Z{a.safe_z:.3f}")
    body(t)
    L(f"G0 Z{a.safe_z:.3f}")
    L("M05")


def pilots(key):
    def body(t):
        rt = t["dia"] / 2
        for s, u, v in holes:
            rc = RECIPES[s]
            if rc["pilot"] != key:
                continue
            cx, cy = xy(u, v)
            top = fr.top(cx, cy)
            r = rc["pilot_dia"] / 2 - rt
            assert r > 0.05, f"{s}: pilot {rc['pilot_dia']} needs a tool smaller than {t['dia']}"
            zb = top - rc["pilot_depth"]
            L(f"(pilot {s} d{rc['pilot_dia']} x {rc['pilot_depth']} at u{u} v{v})")
            L(f"G0 X{cx:.3f} Y{cy:.3f}")
            L(f"G0 Z{top + 1.0:.3f}")
            L(f"G1 X{cx + r:.3f} Y{cy:.3f} F{rc['pilot_feed']:.0f}")
            helix(cx, cy, r, top + 1.0, zb, rc["pilot_pitch"], rc["pilot_feed"], ccw=True, close=True)
            L(f"G1 X{cx:.3f} Y{cy:.3f} F{rc['pilot_feed']:.0f}")
            L(f"G0 Z{a.safe_z:.3f}")
    return body


def threads(key):
    def body(t):
        rt = t["dia"] / 2
        for s, u, v in holes:
            rc = RECIPES[s]
            if rc["tm"] != key:
                continue
            assert abs(t["pitch"] - rc["pitch"]) < 1e-6, f"{s}: tool pitch {t['pitch']} != {rc['pitch']}"
            cx, cy = xy(u, v)
            top = fr.top(cx, cy)
            r_full = rc["major"] / 2 - rt
            assert r_full > 0, f"{s}: thread mill too big for the major dia"
            assert rc["thread_depth"] + t["teeth"] <= t["neck"] + 1e-6, f"{s}: thread deeper than the neck reach"
            assert rc["thread_depth"] + 1.0 <= rc["pilot_depth"], f"{s}: thread tip too close to the pilot bottom (ball-nosed pilots need a full-dia margin)"
            z_bot = top - rc["thread_depth"]           # tip (lowest tooth) starts here
            z_top = top + t["teeth"] + 0.5              # lowest tooth clear of the surface -> all teeth out
            passes = [min(t["stepover"], r_full), r_full]
            L(f"(thread {s} x{rc['pitch']} {rc['thread_depth']} deep at u{u} v{v}; radial passes {passes})")
            for r in passes:
                L(f"G0 X{cx:.3f} Y{cy:.3f}")
                L(f"G0 Z{top + 1.0:.3f}")
                L(f"G1 Z{z_bot:.3f} F{t['plunge']:.0f}")            # down the pilot at the centre
                L(f"G1 X{cx + r:.3f} Y{cy:.3f} F{t['feed']:.0f}")   # radial lead-in
                helix(cx, cy, r, z_bot, z_top, rc["pitch"], t["feed"], ccw=True, close=False)  # climb: G3, rising
                L(f"G1 X{cx:.3f} Y{cy:.3f} F{t['feed']:.0f}")
                L(f"G0 Z{a.safe_z:.3f}")
    return body


# tool extras the .fctb carries beyond load_tool()
for k, (n, t) in tools.items():
    d = json.load(open(REPO / TOOLS[k][1]))
    par = d["parameter"]
    t["pitch"] = float(str(par.get("Pitch", "0")).split()[0])
    t["neck"] = float(str(par.get("NeckLength", par.get("CuttingEdgeLength"))).split()[0])
    t["teeth"] = float(str(par["CuttingEdgeLength"]).split()[0])

used = []
for key in ("oflute", "ball2", "tm_m3", "tm_m5"):
    if any(RECIPES[s]["pilot"] == key or RECIPES[s]["tm"] == key for s, _, _ in holes):
        used.append(key)

L("(Threaded holes - helical pilots + thread milling)")
L("(Origin: M464 S2 probed corner; frame + tilt plane from probe_survey_fit.py)")
L("(=== Tool table - load these bits before running ===)")
for key in used:
    n, t = tools[key]
    L(f"(T{n} = {t['name']})")
L("(each section starts with its own Tn M6 - fit the bit the machine asks for)")
L("G90 G94"); L("G17"); L("G54"); L("G21")
for key in used:
    section(key, pilots(key) if key in ("oflute", "ball2") else threads(key))
L("M852"); L("G17 G90"); L("G28"); L("M30")

# asserts: every Z within [top - 13, safe]; every XY inside the block
zmin = min(fr.top(*xy(u, v)) for _, u, v in holes) - 13.0
for l in lines:
    if l.startswith(("G0", "G1")):
        w = {p[0]: float(p[1:]) for p in l.split() if p[0] in "XYZ"}
        if "Z" in w:
            assert zmin - 1e-3 <= w["Z"] <= a.safe_z + 1e-3, l
        if "X" in w:
            d = (w["X"] - fr.o[0], w["Y"] - fr.o[1])
            u = d[0] * fr.ex[0] + d[1] * fr.ex[1]; v = d[0] * fr.ey[0] + d[1] * fr.ey[1]
            assert 0 <= u <= fr.u_max and 0 <= v <= fr.v_len, f"outside block: {l}"

Path(a.out).write_text("\n".join(lines) + "\n")
print(f"wrote {a.out} ({len(lines)} lines); tools in order: " + ", ".join(f"T{tools[k][0]} {tools[k][1]['name']}" for k in used))
