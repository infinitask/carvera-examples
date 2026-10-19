"""Brass-plate hole-drilling pipeline for the Carvera Air, via FreeCAD Path.

Helical-mills N holes through a rectangular brass plate that is defined
purely in code — no STL in or out. Intended for sheet-stock jobs:
indicator plates, fixture adapters, mounting panels where the hole
diameter exceeds the available tool diameter (so a plain drill cycle
can't do the job, a helical ramp can).

Default geometry: 100 × 100 × 1.3 mm plate, four 4 mm through-holes
inset 8 mm from the two nearest edges (one per corner of the plate).
The 1.3 mm "thickness" is intentionally ~0.3 mm deeper than the real
sheet (~1.0 mm) so the bit clears the bottom even on stock with
thickness variation.

Run with freecadcmd (headless). Configuration via environment variables:

    CAM_OUT=cam/brass_holes/brass_holes.gcode \\
    ~/squashfs-root/usr/bin/freecadcmd cam/brass_holes/brass_holes.py

An FCStd is saved alongside the G-code by default (same basename) for
visual review. Override with CAM_FCSTD or set CAM_FCSTD= to skip it.

XYZ zero is the top surface of the stock, bottom-left corner when
viewed from above — the Carvera Air convention. The plate sits with
its top at Z=0, bottom at Z=-plate_z.
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
# shared repo-level `tools/` directory. The 2 mm 1F aluminum endmill sits
# at slot 5 of `tools/carvera_air.fctl`, so the preamble issues `T5 M6`.
# ---------------------------------------------------------------------------

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)  # cam/brass_holes/brass_holes.py → repo root
TOOL_BIT_FILE = os.path.join(_REPO_ROOT, "tools", "endmill_2mm_1F_8L_alu.fctb")
TOOL_NUMBER = 5  # physical ATC slot, matches tools/carvera_air.fctl

with open(TOOL_BIT_FILE) as _f:
    _BIT_SPEC = json.load(_f)

_PARAM = _BIT_SPEC["parameter"]
_ATTR = _BIT_SPEC["attribute"]


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

# Makera's FreeCAD post processor (vendored submodule). Same path and
# args layout as block_edge_trim — only the tool number in the preamble
# changes. See block_edge_trim/block-edge-trim.md for why these defaults.
POST_PROCESSOR_PATH = (
    "vendor/CarveraProfiles/CAM_Post_Processors/Freecad/1.1PostProcessor/"
    "makera_post.py"
)
_PREAMBLE = f"G90 G94\\nG17\\nG54\\nT{TOOL_NUMBER} M6\\nM3 S{SPINDLE_RPM}\\n"
POST_ARGS = f'--no-show-editor --comments --preamble "{_PREAMBLE}"'


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

        self.plate_x = _float_env("CAM_PLATE_X", 100.0)
        self.plate_y = _float_env("CAM_PLATE_Y", 100.0)
        self.plate_z = _float_env("CAM_PLATE_Z", 1.3)
        self.hole_diameter = _float_env("CAM_HOLE_DIAMETER", 4.0)
        self.hole_inset = _float_env("CAM_HOLE_INSET", 8.0)

        for name, v in (
            ("CAM_PLATE_X", self.plate_x),
            ("CAM_PLATE_Y", self.plate_y),
            ("CAM_PLATE_Z", self.plate_z),
            ("CAM_HOLE_DIAMETER", self.hole_diameter),
            ("CAM_HOLE_INSET", self.hole_inset),
        ):
            if v <= 0:
                raise SystemExit(f"{name} must be > 0; got {v}")

        if self.hole_diameter < TOOL_DIAMETER_MM:
            raise SystemExit(
                f"CAM_HOLE_DIAMETER ({self.hole_diameter}mm) is smaller than "
                f"the tool diameter ({TOOL_DIAMETER_MM}mm) — cannot helix-mill"
            )
        # The two inset holes along each axis must not overlap (centre-to-
        # centre distance ≥ 2× radius, plus a hair). Inset dictates centre
        # location: two centres along X sit at (inset, plate_x - inset), so
        # their separation is plate_x - 2*inset.
        for axis, length in (("X", self.plate_x), ("Y", self.plate_y)):
            gap = length - 2 * self.hole_inset
            if gap < self.hole_diameter + 0.01:
                raise SystemExit(
                    f"hole inset {self.hole_inset}mm leaves only {gap:.3f}mm "
                    f"between hole centres on {axis}; need ≥ "
                    f"{self.hole_diameter}mm"
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
# Geometry: build the plate directly as a Part solid
# ---------------------------------------------------------------------------

def build_plate_with_holes(doc, plate_dims, hole_centers, hole_diameter):
    """Build the final-part solid: a plate box with cylindrical through-
    holes cut at each centre. Plate sits with its top face at Z=0 and
    bottom-left corner at (0, 0, -z), matching machine zero.

    The holes are modelled in the base shape so FreeCAD Path's Helix op
    has cylindrical features to latch onto — the op walks the base
    shape's faces, finds ones whose surface is a Cylinder, and generates
    helical tool paths for each. Stock is a plain box (no holes), so the
    toolpath removes exactly the material between stock and part.

    Boolean-cut cylinders are projected 0.1 mm past both faces of the
    plate to avoid coplanar-face degeneracies that can leave zero-area
    film faces after the cut.
    """
    plate = Part.makeBox(plate_dims[0], plate_dims[1], plate_dims[2])
    plate.translate(FreeCAD.Vector(0, 0, -plate_dims[2]))

    radius = hole_diameter / 2.0
    pad = 0.1
    cyl_height = plate_dims[2] + 2 * pad
    for cx, cy in hole_centers:
        cyl = Part.makeCylinder(
            radius,
            cyl_height,
            FreeCAD.Vector(cx, cy, -plate_dims[2] - pad),
        )
        plate = plate.cut(cyl)

    obj = doc.addObject("Part::Feature", "BrassPlate")
    obj.Shape = plate
    return obj


def find_cylindrical_hole_faces(base_obj):
    """Return the ``FaceN`` names of cylindrical faces in the base shape
    — i.e., the walls of each through-hole we cut into the plate.

    Helix op's Base can be either auto-detected (``processHoles = True``)
    or given explicitly as a feature list. The explicit form is more
    robust: if the shape ends up with unexpected extra cylindrical
    features (shouldn't happen with makeBox+makeCylinder cuts, but never
    trust meshing), they won't accidentally become drill targets.
    """
    names = []
    for idx, face in enumerate(base_obj.Shape.Faces, start=1):
        surface = face.Surface
        # Surface type check works across FreeCAD versions — the class
        # name is 'Cylinder' for cylindrical faces regardless of minor
        # version differences in the Part module.
        if surface.__class__.__name__ == "Cylinder":
            names.append(f"Face{idx}")
    return names


# ---------------------------------------------------------------------------
# Tool bit + controller (mirrors block_edge_trim's configure_tool_controller)
# ---------------------------------------------------------------------------

def configure_tool_controller(job):
    """Apply our tool + feeds/speeds onto the Job's default ToolController.

    Same pattern as block_edge_trim: Job.Create() spawns a default TC
    with a generic 5mm endmill; we edit in place rather than building a
    ToolBit from scratch. Velocity is passed as a string literal
    (``"N mm/min"``) to sidestep the Quantity-velocity 60× unit trap
    documented in block-edge-trim.md.
    """
    if not job.Tools.Group:
        raise RuntimeError("Job has no default tool controller")
    tc = job.Tools.Group[0]

    tc.Label = f"TC: {TOOL_DIAMETER_MM:g}mm {TOOL_FLUTES}F End Mill (Alu)"
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
# Helix op — helical drilling for all cylindrical holes in the base
# ---------------------------------------------------------------------------

def add_helix_op(job, base_obj):
    """Helical-mill every cylindrical hole-feature in the base shape.

    The op descends along a helical path whose radius is
    ``(hole_radius − tool_radius)`` and whose pitch is ``StepDown``. No
    vertical plunge, no pilot hole needed — the spiral itself is the
    entry. For a 4mm hole with a 2mm tool, that's a 1mm-radius spiral.

    StartDepth sits at the top of the stock (Z = 0 for our plate),
    FinalDepth at the bottom. With ``StepDown = 0.15 mm`` on a 1.3 mm
    plate that works out to ~9 helical turns per hole — comfortably
    conservative for a 1F endmill in brass.

    Several properties (StepDown, StartDepth, FinalDepth, *Height) are
    expression-bound on fresh ops and would be overwritten by the next
    recompute. ``setExpression(prop, None)`` detaches the binding — same
    trap documented in block-edge-trim.md for Profile.
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

    # Climb milling on an internal feature with an M3 (CW-from-above)
    # spindle means the tool travels CW around the hole. Opposite sense
    # to an outside profile (where climb = CCW) — the "work side" flips
    # when you go from outside to inside a feature.
    if hasattr(op, "Direction"):
        op.Direction = "CW"
    # Start the spiral at the outside of the cut (tool's outer edge at
    # the hole wall) and step inward if the op supports it. For a 4mm
    # hole with a 2mm tool the "inward step" degenerates to a single
    # spiral at tool-radius offset, but we set the mode explicitly so
    # FreeCAD doesn't pick a wide-spiral variant on larger holes.
    if hasattr(op, "StartSide"):
        op.StartSide = "Outside"

    # Explicit feature list. Auto-detection via processHoles /
    # processCircles works in most cases but has surprised us before;
    # listing the faces by name is unambiguous.
    face_names = find_cylindrical_hole_faces(base_obj)
    if not face_names:
        raise SystemExit(
            "no cylindrical faces found in the base shape — "
            "Helix op has nothing to drill"
        )
    op.Base = [(base_obj, face_names)]
    log(f"helix targets {len(face_names)} cylindrical face(s): "
        f"{', '.join(face_names)}")

    return op


# ---------------------------------------------------------------------------
# Post processor (copy of block_edge_trim's post_process; shared code could
# be extracted later when there's a third pipeline)
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
    # Pre-seed its module-level global; the spindle has already been
    # started by the preamble M3 S<rpm> so the per-line S matches state.
    if hasattr(mod, "SPINDLE_SPEED"):
        mod.SPINDLE_SPEED = float(SPINDLE_RPM)

    mod.export(ops, out_path, POST_ARGS)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    cfg = Config()
    out_path = os.path.abspath(cfg.out)

    plate_dims = (cfg.plate_x, cfg.plate_y, cfg.plate_z)
    inset = cfg.hole_inset
    hole_centers = [
        (inset, inset),
        (plate_dims[0] - inset, inset),
        (inset, plate_dims[1] - inset),
        (plate_dims[0] - inset, plate_dims[1] - inset),
    ]

    doc = FreeCAD.newDocument("cam")

    log(f"plate {plate_dims[0]}x{plate_dims[1]}x{plate_dims[2]} mm, "
        f"4 holes dia={cfg.hole_diameter} mm, inset={inset} mm from edges")
    for cx, cy in hole_centers:
        log(f"    hole centre ({cx}, {cy})")

    base = build_plate_with_holes(doc, plate_dims, hole_centers,
                                  cfg.hole_diameter)
    bbox = base.Shape.BoundBox
    log(f"part bbox: X[{bbox.XMin:.3f}, {bbox.XMax:.3f}]  "
        f"Y[{bbox.YMin:.3f}, {bbox.YMax:.3f}]  "
        f"Z[{bbox.ZMin:.3f}, {bbox.ZMax:.3f}]")

    log("creating Path Job")
    job = JobMain.Create("CarveraAir", [base])

    # Stock matches the plate exactly — no XY excess, no Z over-top.
    # StockFromBase wraps the base bbox and the bbox of a plate with
    # cut holes is still the full plate (cuts don't change the bbox),
    # so ExtN=0 gives us a 100x100x1.3 mm solid — the pre-machining
    # block of brass.
    stock = job.Stock
    for prop in ("ExtXneg", "ExtXpos", "ExtYneg", "ExtYpos",
                 "ExtZneg", "ExtZpos"):
        setattr(stock, prop, Units.Quantity(0, Units.Length))

    sbb = job.Stock.Shape.BoundBox
    log(f"stock bbox: X[{sbb.XMin:.3f}, {sbb.XMax:.3f}]  "
        f"Y[{sbb.YMin:.3f}, {sbb.YMax:.3f}]  "
        f"Z[{sbb.ZMin:.3f}, {sbb.ZMax:.3f}]")

    configure_tool_controller(job)

    op = add_helix_op(job, base)
    log(f"ops: 1 helix op, StepDown={STEPDOWN_MM}mm "
        f"(≈{int(plate_dims[2] / STEPDOWN_MM) + 1} turns/hole)")

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
