"""Single-side (X-negative) brass-plate hole-drilling pipeline.

Variant of `brass_holes/brass_holes.py` for stock that is too large to
let the Carvera Air drill all four corners in one setup. Drills only the
**two holes on the X-negative side of the plate** — at `(inset, inset)`
and `(inset, plate_y − inset)`. The intended workflow is:

  1. Mount stock with one long edge facing the X-negative side of the
     bed; run this G-code → drills the two near-side holes.
  2. Rotate the workpiece 180° in-plane (so the opposite long edge is
     now on the X-negative side); re-run the same G-code → drills the
     remaining two holes, which were previously on the X-positive side
     and out of reach.

After both passes the plate has the same four-corner hole pattern that
`brass_holes/brass_holes.py` produces in a single setup on smaller stock.

Default geometry: 200 × 300 × 1.3 mm plate, two 4 mm through-holes inset
8 mm from the X-negative edge and from the nearest Y edge. The 1.3 mm
"thickness" is intentionally ~0.3 mm deeper than the real sheet
(~1.0 mm) so the bit clears the bottom even on stock with thickness
variation.

Run with freecadcmd (headless). Configuration via environment variables:

    CAM_OUT=cam/brass_holes_xneg/brass_holes_xneg.gcode \\
    ~/squashfs-root/usr/bin/freecadcmd cam/brass_holes_xneg/brass_holes_xneg.py

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
# Tool + feeds/speeds profile (same as brass_holes/brass_holes.py)
# ---------------------------------------------------------------------------

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)  # cam/brass_holes_xneg/brass_holes_xneg.py → repo root
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

        # Defaults reflect the use case in the original task: stock too big
        # for a single-setup four-corner job. Override per-run as needed.
        self.plate_x = _float_env("CAM_PLATE_X", 200.0)
        self.plate_y = _float_env("CAM_PLATE_Y", 300.0)
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

        # Single-side variant: only two holes, both on the X-negative edge
        # of the plate at Y = inset and Y = plate_y - inset. Validation
        # therefore differs from the four-corner script:
        #   - X: hole must fit fully inside the plate, so the inset has to
        #     be at least the hole radius and at most plate_x - radius.
        #     There is only one X position, so no two-hole separation check
        #     in this axis.
        #   - Y: same two-hole separation rule as the original script.
        radius = self.hole_diameter / 2.0
        if self.hole_inset < radius:
            raise SystemExit(
                f"hole inset {self.hole_inset}mm is smaller than the hole "
                f"radius {radius}mm — hole would poke past the X-negative "
                f"edge of the plate"
            )
        if self.hole_inset > self.plate_x - radius:
            raise SystemExit(
                f"hole inset {self.hole_inset}mm exceeds plate_x - radius "
                f"({self.plate_x - radius}mm); hole centre would land off "
                f"the plate"
            )
        gap_y = self.plate_y - 2 * self.hole_inset
        if gap_y < self.hole_diameter + 0.01:
            raise SystemExit(
                f"hole inset {self.hole_inset}mm leaves only {gap_y:.3f}mm "
                f"between hole centres on Y; need ≥ {self.hole_diameter}mm"
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
    """Build the final-part solid: plate box with cylindrical through-holes.

    Plate sits with its top face at Z=0 and bottom-left corner at
    (0, 0, -z), matching machine zero. Boolean-cut cylinders are projected
    0.1 mm past both faces of the plate to avoid coplanar-face
    degeneracies that can leave zero-area film faces after the cut.
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
    """Return the ``FaceN`` names of cylindrical faces in the base shape."""
    names = []
    for idx, face in enumerate(base_obj.Shape.Faces, start=1):
        surface = face.Surface
        if surface.__class__.__name__ == "Cylinder":
            names.append(f"Face{idx}")
    return names


# ---------------------------------------------------------------------------
# Tool bit + controller
# ---------------------------------------------------------------------------

def configure_tool_controller(job):
    """Apply our tool + feeds/speeds onto the Job's default ToolController."""
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
# Helix op
# ---------------------------------------------------------------------------

def add_helix_op(job, base_obj):
    """Helical-mill every cylindrical hole-feature in the base shape."""
    op = HelixOp.Create("Helix", parentJob=job)

    for prop in ("StepDown", "StartDepth", "FinalDepth",
                 "ClearanceHeight", "SafeHeight"):
        try:
            op.setExpression(prop, None)
        except Exception:
            pass

    stock_bb = job.Stock.Shape.BoundBox
    op.StartDepth = Units.Quantity(f"{stock_bb.ZMax} mm")
    op.FinalDepth = Units.Quantity(f"{stock_bb.ZMin} mm")
    op.StepDown = Units.Quantity(f"{STEPDOWN_MM} mm")
    op.ClearanceHeight = Units.Quantity(f"{CLEARANCE_HEIGHT_MM} mm")
    op.SafeHeight = Units.Quantity(f"{SAFE_HEIGHT_MM} mm")

    if hasattr(op, "Direction"):
        op.Direction = "CW"
    if hasattr(op, "StartSide"):
        op.StartSide = "Outside"

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
# Post processor
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
    # Two holes, both on the X-negative edge of the plate. The Y positions
    # mirror the X-negative pair from the four-corner script.
    hole_centers = [
        (inset, inset),
        (inset, plate_dims[1] - inset),
    ]

    doc = FreeCAD.newDocument("cam")

    log(f"plate {plate_dims[0]}x{plate_dims[1]}x{plate_dims[2]} mm, "
        f"2 holes (X-neg side) dia={cfg.hole_diameter} mm, "
        f"inset={inset} mm from edges")
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

    stock = job.Stock
    for prop in ("ExtXneg", "ExtXpos", "ExtYneg", "ExtYpos",
                 "ExtZneg", "ExtZpos"):
        setattr(stock, prop, Units.Quantity(0, Units.Length))

    sbb = job.Stock.Shape.BoundBox
    log(f"stock bbox: X[{sbb.XMin:.3f}, {sbb.XMax:.3f}]  "
        f"Y[{sbb.YMin:.3f}, {sbb.YMax:.3f}]  "
        f"Z[{sbb.ZMin:.3f}, {sbb.ZMax:.3f}]")

    configure_tool_controller(job)

    add_helix_op(job, base)
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
        if e.code not in (None, 0):
            log(f"ABORTED: {e.code}")
        raise
    except Exception:
        import traceback
        sys.stderr.write("[cam] FAILED:\n")
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        raise


_run()
