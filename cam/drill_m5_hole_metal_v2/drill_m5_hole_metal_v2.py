"""Disc-drilling pipeline for the Carvera Air, via FreeCAD Path.

Helical-mills ONE central through-hole in a round metal disc that is
defined purely in code — no STL in or out. The default job is the one
this pipeline was written for:

    a 25 mm diameter x 1.6 mm stainless-steel disc, one central 5.3 mm
    hole (M5 close-fit clearance), drilled to 1.9 mm depth so it clears
    the bottom of the 1.6 mm disc with ~0.3 mm to spare.

Why helical milling, not a plain drill cycle? The hole (5.3 mm) is much
wider than the tool (2 mm), so a G81/G82/G83 drill can't make it — it
would only ever bore a hole at its own diameter. The Helix op spirals
the 2 mm endmill around a circle of radius (hole_radius - tool_radius)
= 1.65 mm while ramping down, removing the full hole volume with no
straight plunge. Same approach as cam/brass_holes/ (the reference
pipeline this one is modelled on); the only real difference here is a
*round* base solid (a disc) instead of a rectangular plate, which means
the outer rim of the disc is ALSO a cylindrical face — so hole-face
detection filters by radius to pick only the inner hole, never the rim.

The tool is the 2 mm 1F 8 mm aluminium endmill (tools/endmill_2mm_1F_8L_alu.fctb,
ATC slot T5). Its feeds/speeds were tuned for aluminium/brass but are —
per the job owner — dialled in for stainless steel too, so they are used
as-is (no override). See drill-m5-hole-metal-v2.md.

Run with freecadcmd (headless). Configuration via environment variables:

    CAM_OUT=cam/drill_m5_hole_metal_v2/drill_m5_hole_metal_v2.gcode \\
    ~/squashfs-root/usr/bin/freecadcmd cam/drill_m5_hole_metal_v2/drill_m5_hole_metal_v2.py

An FCStd is saved alongside the G-code by default (same basename) for
visual review. Override with CAM_FCSTD or set CAM_FCSTD= to skip it.

Work-coordinate convention (Carvera Air): XYZ zero is the top surface of
the stock, and X/Y zero is anchor 1 (the machine's fixed front-left
work-holding corner, which G54 is set to). The disc is fixtured in the
+X/+Y quadrant from anchor 1; by default it is placed tangent to both
anchor axes, so its centre sits at (disc_radius, disc_radius) =
(12.5, 12.5). Every toolpath coordinate is therefore positive and
referenced to anchor 1 — no per-job part-centre re-zero. The disc top
sits at Z=0, its bottom at Z=-1.6 (real), and the hole is cut to
Z=-1.9 (the modelled solid is 1.9 mm thick so the helix bottoms out
0.3 mm past the real disc — the last ~0.3 mm cuts air below the part).
"""

from __future__ import annotations

import json
import os
import sys


def log(msg):
    """Print that survives FreeCAD's stdout handling (flush to stderr)."""
    sys.stderr.write(f"[cam] {msg}\n")
    sys.stderr.flush()


import FreeCAD
import Part
from FreeCAD import Units

import Path  # noqa: F401  -- ensures the CAM workbench is loaded
from Path.Main import Job as JobMain
from Path.Op import Helix as HelixOp


# ---------------------------------------------------------------------------
# Tool + feeds/speeds profile
#
# Single source of truth is a FreeCAD `.fctb` tool-bit file under the
# shared repo-level `tools/` directory. The 2 mm 1F aluminium endmill sits
# at slot 5 of `tools/carvera_air.fctl`, so the preamble issues `T5 M6`.
# Feeds/speeds are read straight from the `.fctb` — never hardcoded here.
# ---------------------------------------------------------------------------

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)  # cam/drill_m5_hole_metal_v2/drill_m5_hole_metal_v2.py -> repo root
TOOL_BIT_FILE = os.path.join(_REPO_ROOT, "tools", "endmill_2mm_1F_8L_alu.fctb")
TOOL_NUMBER = 5  # physical ATC slot, matches tools/carvera_air.fctl

with open(TOOL_BIT_FILE) as _f:
    _BIT_SPEC = json.load(_f)

_PARAM = _BIT_SPEC["parameter"]
_ATTR = _BIT_SPEC["attribute"]
TOOL_NAME = _BIT_SPEC.get("name", "2mm 1F 8mm End Mill")


def _mm(text):
    return float(Units.Quantity(text).getValueAs("mm"))


def _mm_per_min(text):
    return float(Units.Quantity(text).getValueAs("mm/min"))


TOOL_DIAMETER_MM = _mm(_PARAM["Diameter"])
TOOL_FLUTES = int(_PARAM["Flutes"])
TOOL_CUTTING_EDGE_MM = _mm(_PARAM["CuttingEdgeHeight"])
TOOL_SHANK_DIAMETER_MM = _mm(_PARAM["ShankDiameter"])
TOOL_LENGTH_MM = _mm(_PARAM["Length"])

SPINDLE_RPM = int(float(_ATTR["SpindleSpeed"]))
FEED_HORIZ_MM_MIN = _mm_per_min(_ATTR["HorizFeed"])
FEED_VERT_MM_MIN = _mm_per_min(_ATTR["VertFeed"])
FEED_RAMP_MM_MIN = _mm_per_min(_ATTR["RampFeed"])
STEPDOWN_MM = _mm(_ATTR["Stepdown"])

SAFE_HEIGHT_MM = 5.0             # relative to top of stock
CLEARANCE_HEIGHT_MM = 10.0


# ---------------------------------------------------------------------------
# Tool legend + prep warning (emitted at the very top of the G-code)
# ---------------------------------------------------------------------------

# Tools this job loads, in ATC order: (tool number, bit name). Single-tool.
TOOLS_IN_JOB = [(TOOL_NUMBER, TOOL_NAME)]


def _safe_comment(text):
    """Make a string safe to drop inside a `( ... )` G-code comment.

    A raw `(` or `)` inside a comment closes it early on the Carvera — a
    known hazard (see pocket_slots._prep_warning). The `.fctb` bit name is
    "2mm 1F 8mm End Mill (Aluminum)", which carries parentheses, so swap
    them for square brackets. ASCII only, so the controller never chokes.
    """
    return text.replace("(", "[").replace(")", "]")


def _tool_legend(tools):
    """G-code comment block mapping each tool number to its bit name, placed
    at the top of the program so the operator knows which physical bit each
    Tn is when loading the file. The `\\n` are literal two-char escapes the
    Makera post expands into newlines — same convention as the preamble
    G-words. See CLAUDE.md, "CAM G-code: tool legend at the top"."""
    lines = ["(=== Tool table - load these bits before running ===)"]
    lines += [f"(T{nr} = {_safe_comment(name)})" for nr, name in tools]
    return "".join(line + "\\n" for line in lines)


def _prep_warning():
    """Loud comment block at the very TOP of the program. ASCII only, NO
    parentheses inside the text (a `(` would close the comment early).

    This is a SHALLOW job on a thin fixtured disc — no tall-stock reach
    hazard — so the standard `G54` + `T5 M6` order and a plain `G28` park
    are safe. The reminders are the fixturing/zeroing prerequisites the
    file itself does not perform (it neither homes nor probes)."""
    lines = [
        "(*** PREP THE MACHINE BY HAND BEFORE RUNNING ***)",
        "(  1. Load tool T5 - 2mm 1F 8mm metal endmill - into the spindle.)",
        "(     If the laser was the last head used, switch back first.)",
        "(  2. Fixture the disc in +X/+Y from anchor 1, tangent to both axes.)",
        "(  3. Set the XY work zero to anchor 1 - G54 - and confirm it.)",
        "(  4. Probe / set the work Z zero on the TRUE top of THIS disc.)",
        "(  Ensure clearance UNDER the hole - the bit exits ~0.3mm past.)",
    ]
    return "".join(line + "\\n" for line in lines)


# Makera's FreeCAD post processor (vendored submodule). Same path and args
# layout as brass_holes — only the preamble content changes.
POST_PROCESSOR_PATH = (
    "vendor/CarveraProfiles/CAM_Post_Processors/Freecad/1.1PostProcessor/"
    "makera_post.py"
)
_PREAMBLE = (
    _prep_warning()
    + _tool_legend(TOOLS_IN_JOB)
    + f"G90 G94\\nG17\\nG54\\nT{TOOL_NUMBER} M6\\nM3 S{SPINDLE_RPM}\\n"
)
# Spindle off, restore modes, HOME (G28 lifts Z first -> safe on this
# shallow job), end. Same as the Makera default; stated explicitly here.
_POSTAMBLE = "M05\\nG17 G90\\nG28\\nM30"
POST_ARGS = (
    f'--no-show-editor --comments '
    f'--preamble "{_PREAMBLE}" --postamble "{_POSTAMBLE}"'
)


# ---------------------------------------------------------------------------
# Environment-variable config (freecadcmd eats positional argv)
# ---------------------------------------------------------------------------

class Config:
    def __init__(self):
        self.out = _require_env("CAM_OUT")

        fcstd_env = os.environ.get("CAM_FCSTD")
        if fcstd_env is None:
            base, _ = os.path.splitext(self.out)
            self.fcstd = base + ".FCStd"
        else:
            self.fcstd = fcstd_env or None

        self.disc_diameter = _float_env("CAM_DISC_DIAMETER", 25.0)
        # Modelled thickness = drill depth. Deliberately ~0.3 mm deeper than
        # the real 1.6 mm disc so the helix bottoms out below the part and
        # guarantees a clean through-hole despite thickness variation. The
        # bit spends the last ~0.3 mm in air once it has cleared the metal.
        self.drill_depth = _float_env("CAM_DRILL_DEPTH", 1.9)
        self.hole_diameter = _float_env("CAM_HOLE_DIAMETER", 5.3)

        # Hole = disc centre (concentric). Default placement is tangent to
        # the anchor-1 X/Y axes, so the centre sits at (radius, radius).
        radius = self.disc_diameter / 2.0
        self.center_x = _float_env("CAM_CENTER_X", radius)
        self.center_y = _float_env("CAM_CENTER_Y", radius)

        for name, v in (
            ("CAM_DISC_DIAMETER", self.disc_diameter),
            ("CAM_DRILL_DEPTH", self.drill_depth),
            ("CAM_HOLE_DIAMETER", self.hole_diameter),
            ("CAM_CENTER_X", self.center_x),
            ("CAM_CENTER_Y", self.center_y),
        ):
            if v <= 0:
                raise SystemExit(f"{name} must be > 0; got {v}")

        # Helical milling needs the hole strictly wider than the tool, or
        # the spiral radius (hole_r - tool_r) collapses to <= 0.
        if self.hole_diameter <= TOOL_DIAMETER_MM:
            raise SystemExit(
                f"CAM_HOLE_DIAMETER ({self.hole_diameter}mm) must be larger "
                f"than the tool diameter ({TOOL_DIAMETER_MM}mm) to helix-mill "
                f"(a same-size hole would need a plain drill, not this pipeline)"
            )
        # The hole must fit inside the disc with real wall left around it.
        if self.hole_diameter >= self.disc_diameter:
            raise SystemExit(
                f"CAM_HOLE_DIAMETER ({self.hole_diameter}mm) does not fit "
                f"inside the disc ({self.disc_diameter}mm)"
            )
        # Centre must sit far enough from anchor 1 that the whole disc is in
        # +X/+Y (tangent placement is the minimum: centre >= radius).
        for axis, c in (("X", self.center_x), ("Y", self.center_y)):
            if c < self.disc_diameter / 2.0 - 1e-9:
                raise SystemExit(
                    f"CAM_CENTER_{axis} ({c}mm) puts part of the disc at "
                    f"negative {axis}; centre must be >= radius "
                    f"({self.disc_diameter / 2.0}mm) so the disc stays in +{axis}"
                )


def _require_env(name):
    val = os.environ.get(name)
    if not val:
        raise SystemExit(f"missing required env var: {name}")
    return val


def _float_env(name, default):
    val = os.environ.get(name)
    if val is None or val == "":
        return float(default)
    try:
        return float(val)
    except ValueError as e:
        raise SystemExit(f"{name} must be a number; got {val!r} ({e})")


# ---------------------------------------------------------------------------
# Geometry: build the disc + central hole directly as a Part solid
# ---------------------------------------------------------------------------

def build_disc_with_hole(doc, disc_diameter, drill_depth, center, hole_diameter):
    """Build the final-part solid: a round disc with one cylindrical
    through-hole cut at its centre. The disc top face sits at Z=0 and its
    bottom at Z=-drill_depth, matching machine zero (top of stock).

    The hole is modelled into the base shape so FreeCAD Path's Helix op has
    a cylindrical feature to latch onto — the op walks the base shape's
    faces, finds ones whose surface is a Cylinder, and generates a helical
    tool path. Stock is the plain disc (no hole), so the toolpath removes
    exactly the hole volume.

    The boolean-cut cylinder is projected 0.1 mm past both faces of the
    disc to avoid coplanar-face degeneracies that can leave zero-area film
    faces after the cut.
    """
    cx, cy = center
    disc_radius = disc_diameter / 2.0

    disc = Part.makeCylinder(
        disc_radius,
        drill_depth,
        FreeCAD.Vector(cx, cy, -drill_depth),
    )

    hole_radius = hole_diameter / 2.0
    pad = 0.1
    cyl = Part.makeCylinder(
        hole_radius,
        drill_depth + 2 * pad,
        FreeCAD.Vector(cx, cy, -drill_depth - pad),
    )
    disc = disc.cut(cyl)

    obj = doc.addObject("Part::Feature", "MetalDisc")
    obj.Shape = disc
    return obj


def find_hole_faces(base_obj, hole_diameter):
    """Return the ``FaceN`` names of cylindrical faces whose radius matches
    the HOLE — i.e. the wall of the central through-hole, and NOT the disc's
    own outer rim (which is also a cylindrical face, at the disc radius).

    A disc is a cylinder, so face detection MUST discriminate by radius or
    the Helix op would happily try to "drill" the 25 mm outer rim. We match
    the hole radius to a tight tolerance; anything at the disc radius (the
    rim) is left out of the target list.
    """
    hole_radius = hole_diameter / 2.0
    tol = 0.01
    names = []
    for idx, face in enumerate(base_obj.Shape.Faces, start=1):
        surface = face.Surface
        # Class name is 'Cylinder' for cylindrical faces across FreeCAD
        # versions. Guard on Radius so the outer rim is excluded.
        if surface.__class__.__name__ != "Cylinder":
            continue
        radius = float(surface.Radius)
        if abs(radius - hole_radius) <= tol:
            names.append(f"Face{idx}")
    return names


# ---------------------------------------------------------------------------
# Tool bit + controller (mirrors brass_holes' configure_tool_controller)
# ---------------------------------------------------------------------------

def configure_tool_controller(job):
    """Apply our tool + feeds/speeds onto the Job's default ToolController.

    Job.Create() spawns a default TC with a generic endmill; we edit it in
    place. Velocity is passed as a string literal (``"N mm/min"``) to
    sidestep the Quantity-velocity 60x unit trap (Units.Quantity(500,
    Units.Velocity) means 500 mm/s) documented in brass-holes.md.
    """
    if not job.Tools.Group:
        raise RuntimeError("Job has no default tool controller")
    tc = job.Tools.Group[0]

    tc.Label = f"TC: {TOOL_DIAMETER_MM:g}mm {TOOL_FLUTES}F End Mill (Metal)"
    tc.ToolNumber = TOOL_NUMBER
    tc.HorizFeed = Units.Quantity(f"{FEED_HORIZ_MM_MIN} mm/min")
    tc.VertFeed = Units.Quantity(f"{FEED_VERT_MM_MIN} mm/min")
    tc.HorizRapid = Units.Quantity("3000 mm/min")
    tc.VertRapid = Units.Quantity("1500 mm/min")
    tc.SpindleSpeed = float(SPINDLE_RPM)
    tc.SpindleDir = "Forward"

    bit = tc.Tool
    for prop, value in (
        ("Diameter", Units.Quantity(TOOL_DIAMETER_MM, Units.Length)),
        ("CuttingEdgeHeight", Units.Quantity(TOOL_CUTTING_EDGE_MM, Units.Length)),
        ("ShankDiameter", Units.Quantity(TOOL_SHANK_DIAMETER_MM, Units.Length)),
        ("Length", Units.Quantity(TOOL_LENGTH_MM, Units.Length)),
        ("Flutes", TOOL_FLUTES),
        ("Material", _PARAM.get("Material", "Carbide")),
        ("Chipload", Units.Quantity(_PARAM.get("Chipload", "0 mm"))),
    ):
        if hasattr(bit, prop):
            setattr(bit, prop, value)

    return tc


# ---------------------------------------------------------------------------
# Helix op — helical drilling of the central hole
# ---------------------------------------------------------------------------

def add_helix_op(job, base_obj, hole_diameter):
    """Helical-mill the central hole-feature of the base disc.

    The op descends along a helical path whose radius is
    ``(hole_radius - tool_radius)`` and whose pitch is ``StepDown``. No
    vertical plunge, no pilot hole needed — the spiral itself is the entry.
    For a 5.3 mm hole with a 2 mm tool that is a 1.65 mm-radius spiral.

    StartDepth sits at the top of the stock (Z=0), FinalDepth at the bottom
    (Z=-drill_depth). With StepDown = 0.15 mm over 1.9 mm that is ~13
    helical turns — conservative for a 1F endmill in metal.

    Several depth/height properties are expression-bound on fresh ops and
    would be overwritten by the next recompute. ``setExpression(prop,
    None)`` detaches the binding — the same trap documented in
    brass-holes.md.
    """
    op = HelixOp.Create("Helix", parentJob=job)

    for prop in ("StepDown", "StartDepth", "FinalDepth",
                 "ClearanceHeight", "SafeHeight"):
        try:
            op.setExpression(prop, None)
        except Exception:
            # Not all Helix versions expose every property as an expression;
            # the ones that don't just ignore the detach call.
            pass

    stock_bb = job.Stock.Shape.BoundBox
    op.StartDepth = Units.Quantity(f"{stock_bb.ZMax} mm")
    op.FinalDepth = Units.Quantity(f"{stock_bb.ZMin} mm")
    op.StepDown = Units.Quantity(f"{STEPDOWN_MM} mm")
    op.ClearanceHeight = Units.Quantity(f"{CLEARANCE_HEIGHT_MM} mm")
    op.SafeHeight = Units.Quantity(f"{SAFE_HEIGHT_MM} mm")

    # Arc sense / climb-vs-conventional: with StartSide="Inside" (below)
    # FreeCAD's Helix always posts G3 (conventional milling on this internal
    # feature) *regardless of Direction* — verified by posting with both "CW"
    # and "CCW" (byte-identical G3 output). So the climb (G2) sense the old
    # outside-in order gave (climb = CW orbit = G2 on an internal feature with
    # an M3 spindle) is NOT recoverable once we go inside-out. Conventional is
    # the accepted tradeoff for drilling inside-out (which is what stops the
    # central core breaking loose), and it's fine here: a through-hole in thin
    # stock, light finish engagement, feeds already proven on this material.
    # Direction is set for intent only; it does not change the posted output
    # while StartSide="Inside".
    if hasattr(op, "Direction"):
        op.Direction = "CW"
    # Start the spiral at the INSIDE (centre) and step OUTWARD to the wall.
    # This matters when the hole is wide enough (relative to the tool) to
    # need more than one concentric pass: an "Outside"-first order cuts the
    # finished wall first and leaves a free-standing core in the middle,
    # which snaps off and flies away as the final inner pass reaches it
    # (observed on the first cut of this job — a Ø~1.3 mm slug launched).
    # "Inside" removes the central material first, so every later pass is
    # only ever widening an existing hole — nothing is left unsupported to
    # break loose. Drill inside-out.
    if hasattr(op, "StartSide"):
        op.StartSide = "Inside"

    # Explicit feature list, filtered to the hole radius so the disc's outer
    # rim (also cylindrical) can never become a drill target.
    face_names = find_hole_faces(base_obj, hole_diameter)
    if not face_names:
        raise SystemExit(
            "no cylindrical face at the hole radius found in the base shape "
            "— Helix op has nothing to drill (check disc/hole geometry)"
        )
    if len(face_names) != 1:
        raise SystemExit(
            f"expected exactly one central hole face, found {len(face_names)}: "
            f"{', '.join(face_names)} — geometry is not a single central hole"
        )
    op.Base = [(base_obj, face_names)]
    log(f"helix target: {face_names[0]} (hole wall)")

    return op


# ---------------------------------------------------------------------------
# Post processor (copy of brass_holes' post_process)
# ---------------------------------------------------------------------------

def post_process(job, out_path):
    ops = list(job.Operations.Group)

    import importlib.util
    post_path = os.path.join(_REPO_ROOT, POST_PROCESSOR_PATH)
    if not os.path.isfile(post_path):
        raise SystemExit(
            f"post processor not found at {post_path} — did the "
            f"vendor/CarveraProfiles submodule initialize? "
            f"Try: git submodule update --init --recursive"
        )
    spec = importlib.util.spec_from_file_location("makera_post", post_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Makera's post aborts at the first G1/G2/G3 if SPINDLE_SPEED < 5.
    # Pre-seed its module-level global; the spindle has already been started
    # by the preamble M3 S<rpm> so the per-line S matches state.
    if hasattr(mod, "SPINDLE_SPEED"):
        mod.SPINDLE_SPEED = float(SPINDLE_RPM)

    mod.export(ops, out_path, POST_ARGS)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    cfg = Config()
    out_path = os.path.abspath(cfg.out)

    center = (cfg.center_x, cfg.center_y)

    doc = FreeCAD.newDocument("cam")

    log(f"disc dia={cfg.disc_diameter} mm, drill depth={cfg.drill_depth} mm, "
        f"central hole dia={cfg.hole_diameter} mm at centre {center}")
    log(f"helix spiral radius = {cfg.hole_diameter / 2.0 - TOOL_DIAMETER_MM / 2.0:.3f} mm "
        f"(hole_r {cfg.hole_diameter / 2.0} - tool_r {TOOL_DIAMETER_MM / 2.0})")

    base = build_disc_with_hole(doc, cfg.disc_diameter, cfg.drill_depth,
                                center, cfg.hole_diameter)
    bbox = base.Shape.BoundBox
    log(f"part bbox: X[{bbox.XMin:.3f}, {bbox.XMax:.3f}]  "
        f"Y[{bbox.YMin:.3f}, {bbox.YMax:.3f}]  "
        f"Z[{bbox.ZMin:.3f}, {bbox.ZMax:.3f}]")

    log("creating Path Job")
    job = JobMain.Create("CarveraAir", [base])

    # Stock matches the disc exactly — no XY excess, no Z over-top. A cut
    # hole does not change the bounding box, so StockFromBase with all
    # extensions 0 gives the pre-machining blank (a box bounding the disc;
    # only its Z extents feed the helix depth, so the box vs round outline
    # doesn't matter to the toolpath).
    stock = job.Stock
    for prop in ("ExtXneg", "ExtXpos", "ExtYneg", "ExtYpos",
                 "ExtZneg", "ExtZpos"):
        setattr(stock, prop, Units.Quantity(0, Units.Length))

    sbb = job.Stock.Shape.BoundBox
    log(f"stock bbox: X[{sbb.XMin:.3f}, {sbb.XMax:.3f}]  "
        f"Y[{sbb.YMin:.3f}, {sbb.YMax:.3f}]  "
        f"Z[{sbb.ZMin:.3f}, {sbb.ZMax:.3f}]")

    configure_tool_controller(job)

    add_helix_op(job, base, cfg.hole_diameter)
    turns = int(cfg.drill_depth / STEPDOWN_MM) + 1
    log(f"ops: 1 helix op, StepDown={STEPDOWN_MM}mm (~{turns} turns)")

    doc.recompute()

    if cfg.fcstd:
        fcstd = os.path.abspath(cfg.fcstd)
        doc.saveAs(fcstd)
        log(f"saved FCStd to {fcstd}")

    log(f"posting G-code -> {out_path}")
    post_process(job, out_path)
    log("done")


def _run():
    try:
        main()
    except SystemExit as e:
        # freecadcmd suppresses SystemExit messages — print explicitly.
        if e.code not in (None, 0):
            log(f"ABORTED: {e.code}")
        raise
    except Exception:
        import traceback
        sys.stderr.write("[cam] FAILED:\n")
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        raise


# freecadcmd runs scripts with exec() in a fresh namespace; __name__ can be
# "__main__" or not depending on build, so just call unconditionally.
_run()
