"""Block-edge trim pipeline for thin sheet stock (Carvera Air).

Cuts a single straight line CLEAN THROUGH a thin rectangular sheet to trim
a waste strip off one edge — a parting cut, not a facing or profiling pass.
The tool runs back and forth along the cut line, stepping down a little each
pass until it is all the way through. Built for thin sheet (a few mm) where
the spindle can lift the bit clear above the stock top between passes, so the
approach is a normal top-down one (this is NOT the tall-hardwood slot-pocketing /
end_trim case, where a tall brick forced lateral side-entry — here the stock
is thin enough that the bit clears the top with room to spare).

Default job: trim 42.48 mm off the RIGHT (+X) edge of a 232 x 132 x 4.04 mm
ABS sheet, cutting 4.3 mm deep (a little past the 4.04 mm thickness so the
strip is fully severed despite stock-thickness variation), with the 2 mm 1F
endmill at ATC slot 5. The kept (left) piece comes out exactly
232 - 42.48 = 189.52 mm wide.

The cut runs along Y (a left/right-edge trim is a vertical line at a fixed X);
plunges happen in free air just off each Y end of the sheet, so the bit never
plunges full-width into solid stock. Geometry is built purely in code — no STL
in the loop — mirroring cam/brass_holes/brass_holes.py.

Run with freecadcmd (headless). Configuration via environment variables:

    CAM_OUT=cam/block_edge_trim/block_edge_trim.gcode \\
    ~/squashfs-root/usr/bin/freecadcmd cam/block_edge_trim/block_edge_trim.py

An FCStd is saved alongside the G-code by default (same basename) for visual
review; override with CAM_FCSTD or set CAM_FCSTD= to skip it.

XYZ zero is the top surface of the stock, bottom-left corner viewed from above
(the Carvera Air convention used across this repo). The sheet sits with its top
face at Z=0, bottom at Z=-stock_z, bottom-left corner at (0, 0). The operator
fixtures the sheet with its RIGHT edge facing +X.
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


# ---------------------------------------------------------------------------
# Tool + feeds/speeds profile
#
# Single source of truth is the FreeCAD `.fctb` tool-bit file under the shared
# repo-level `tools/` directory. The 2 mm 1F 8 mm endmill sits at slot 5 of
# `tools/carvera_air.fctl`, so the preamble issues `T5 M6`. Its .fctb feeds are
# aluminum-tuned; for ABS we override the *cut* params (stepdown especially) via
# the defaults in Config below, but the tool geometry still comes from here.
# ---------------------------------------------------------------------------

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)  # cam/block_edge_trim/block_edge_trim.py -> repo root
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

# Human-readable bit name from the .fctb, emitted in the G-code tool legend so
# the operator can match Tn to a physical bit when loading the file. Repo
# convention — see CLAUDE.md, "CAM G-code: tool legend at the top".
TOOL_NAME = _BIT_SPEC.get("name", os.path.basename(TOOL_BIT_FILE))

SAFE_HEIGHT_MM = 5.0        # rapid-down target just above the stock top
CLEARANCE_HEIGHT_MM = 10.0  # full retract / park height above the stock top

POST_PROCESSOR_PATH = (
    "vendor/CarveraProfiles/CAM_Post_Processors/Freecad/1.1PostProcessor/"
    "makera_post.py"
)

# Tools this job loads, in ATC order: (tool number, bit name). Single-tool.
TOOLS_IN_JOB = [(TOOL_NUMBER, TOOL_NAME)]


def _tool_legend(tools):
    """G-code comment block mapping each tool number to its bit name, placed at
    the top of the program so the operator knows which physical bit each Tn is
    when loading the file. The `\\n` are the literal two-char escapes the Makera
    post expands into newlines — same convention as the preamble G-words. Kept
    ASCII (no em-dashes) so the controller never chokes on a comment. See
    CLAUDE.md, "CAM G-code: tool legend at the top"."""
    lines = ["(=== Tool table - load these bits before running ===)"]
    lines += [f"(T{nr} = {name})" for nr, name in tools]
    return "".join(line + "\\n" for line in lines)


def _prep_warning():
    """Loud comment block at the very TOP of every program. ASCII only, and NO
    parentheses inside the text — a `(` would close the G-code comment early.
    Thin-sheet job, so the standard top-down prep (NOT the tall-stock
    send_to_cnc.sh dance from pocket_slots)."""
    lines = [
        "(*** PREP THE MACHINE BY HAND BEFORE RUNNING ***)",
        "(  1. Load tool T5 - 2mm 1F endmill - into the spindle.)",
        "(  2. Probe / set the work Z zero on the TRUE top of THIS sheet.)",
        "(  3. Verify the active XY work zero is the bottom-left corner, with)",
        "(     the trimmed edge facing +X.)",
        "(  4. SPOILBOARD: this cuts ~0.3mm past the underside, so the cut line)",
        "(     must sit over a sacrificial layer / clear gap, not bare fixture.)",
        "(  5. SECURE THE OFFCUT: this is a full through-cut, so the waste strip)",
        "(     comes free at the end. Tape/clamp it so it cannot shift into the)",
        "(     cutter. Clamp the KEPT side, clear of the cut line.)",
    ]
    return "".join(line + "\\n" for line in lines)


# Dust extractor / fan, on the Carvera's External Control ("ext out") output:
# M851 S<pct> turns it on at <pct>% PWM, M852 turns it off. This is the output
# our extractor is wired to (NOT M801/M802, the built-in vacuum, which did not
# drive it — see docs/carvera-air.md). Convention: fire it in the preamble next
# to M3 and shut it off in the postamble next to M05, so it runs for exactly the
# cut. (pocket_slots runs 50% for its dust-only wood job; this ABS parting cut
# throws more swarf, so 75%.)
EXTRACTOR_PCT = 75

# Preamble: prep warning, tool legend, then modes + G54 + tool load + spindle on
# + extractor on (standard order: G90 G94 / G17 / G54 / T5 M6 / M3 / M851).
_PREAMBLE = (
    _prep_warning()
    + _tool_legend(TOOLS_IN_JOB)
    + f"G90 G94\\nG17\\nG54\\nT{TOOL_NUMBER} M6\\nM3 S{SPINDLE_RPM}\\n"
    + f"M851 S{EXTRACTOR_PCT}\\n"
)

# Postamble: spindle off, extractor off, restore modes, HOME, end. The bit
# finishes lifted to the clearance height ABOVE the thin sheet, so the G28
# clearance traverse is safe (no over-stock fouling — the stock is a few mm tall).
_POSTAMBLE = "M05\\nM852\\nG17 G90\\nG28\\nM30"

POST_ARGS = (
    f'--no-show-editor --comments '
    f'--preamble "{_PREAMBLE}" --postamble "{_POSTAMBLE}"'
)


# ---------------------------------------------------------------------------
# Environment-variable config (freecadcmd eats positional argv)
# ---------------------------------------------------------------------------

_DEFAULT_OUT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "block_edge_trim.gcode",
)

# Which edge gets trimmed. right/left cut a vertical line (travel along Y);
# top/bottom cut a horizontal line (travel along X). The waste side is the side
# the named edge is on; the kept piece is the remainder.
_EDGES = ("right", "left", "top", "bottom")


class Config:
    def __init__(self):
        self.out = os.environ.get("CAM_OUT") or _DEFAULT_OUT

        fcstd_env = os.environ.get("CAM_FCSTD")
        if fcstd_env is None:
            base, _ = os.path.splitext(self.out)
            self.fcstd = base + ".FCStd"
        else:
            self.fcstd = fcstd_env or None

        # Stock outer dimensions (mm). X across, Y deep, Z thick.
        self.stock_x = _float_env("CAM_STOCK_X", 232.0)
        self.stock_y = _float_env("CAM_STOCK_Y", 132.0)
        self.stock_z = _float_env("CAM_STOCK_Z", 4.04)

        # Which edge to trim, and how much to remove from it (mm).
        self.edge = (os.environ.get("CAM_EDGE") or "right").strip().lower()
        self.trim = _float_env("CAM_TRIM", 42.48)

        # Cut depth (mm, positive). Default 4.3 — a touch past the 4.04 mm sheet
        # so the strip severs fully despite thickness variation; the bit spends
        # the last ~0.26 mm in air below the sheet.
        self.cut_depth = _float_env("CAM_CUT_DEPTH", 4.3)

        # Axial depth of cut per pass (mm). Default 1.0 — sensible for ABS with
        # a 2 mm bit (4.3 mm -> 5 passes). ABS is soft and clears well on a 1F
        # bit; raise for fewer passes if the cut is happy, drop if it melts /
        # loads. (The .fctb's 0.15 mm is an aluminum helix-pitch value, far too
        # timid for a parting cut in ABS.)
        self.stepdown = _float_env("CAM_STEPDOWN", 1.0)

        # Feeds (mm/min). Default to the .fctb's aluminum values as a safe
        # starting point; for ABS, melting from too-low chipload is the main
        # risk — if the cut smears/gums rather than chips, RAISE the feed (or
        # drop the rpm) to get a coarser chip. Tune on the machine.
        self.feed_horiz = _float_env("CAM_FEED_HORIZ", _mm_per_min(_ATTR["HorizFeed"]))
        self.feed_vert = _float_env("CAM_FEED_VERT", _mm_per_min(_ATTR["VertFeed"]))

        # How far past each end of the cut line to run, in free air (mm), so the
        # strip fully separates at the corners and every plunge lands off the
        # stock. Must be >= the tool radius; default 3 mm.
        self.overrun = _float_env("CAM_OVERRUN", 3.0)

        # Kerf placement: "waste" (default) offsets the tool a radius onto the
        # WASTE side of the trim line, so the KEPT piece is exactly the nominal
        # size; "center" runs the tool centreline on the line (kept piece a
        # radius smaller).
        self.kerf_side = (os.environ.get("CAM_KERF_SIDE") or "waste").strip().lower()

        # ---- validation ----------------------------------------------------
        for name, v in (
            ("CAM_STOCK_X", self.stock_x),
            ("CAM_STOCK_Y", self.stock_y),
            ("CAM_STOCK_Z", self.stock_z),
            ("CAM_TRIM", self.trim),
            ("CAM_CUT_DEPTH", self.cut_depth),
            ("CAM_STEPDOWN", self.stepdown),
            ("CAM_FEED_HORIZ", self.feed_horiz),
            ("CAM_FEED_VERT", self.feed_vert),
            ("CAM_OVERRUN", self.overrun),
        ):
            if v <= 0:
                raise SystemExit(f"{name} must be > 0; got {v}")

        if self.edge not in _EDGES:
            raise SystemExit(
                f"CAM_EDGE must be one of {_EDGES}; got {self.edge!r}"
            )
        if self.kerf_side not in ("waste", "center"):
            raise SystemExit(
                f"CAM_KERF_SIDE must be 'waste' or 'center'; got "
                f"{self.kerf_side!r}"
            )

        # The trim must leave a positive kept dimension on the relevant axis.
        span = self.stock_x if self.edge in ("right", "left") else self.stock_y
        if self.trim >= span:
            raise SystemExit(
                f"CAM_TRIM ({self.trim} mm) must be < the stock dimension on "
                f"the trimmed axis ({span} mm) — nothing would be left"
            )

        if self.overrun < TOOL_DIAMETER_MM / 2.0:
            raise SystemExit(
                f"CAM_OVERRUN ({self.overrun} mm) must be >= the tool radius "
                f"({TOOL_DIAMETER_MM / 2.0} mm) so the strip fully separates "
                f"at the corners"
            )

        if self.cut_depth > TOOL_CUTTING_EDGE_MM:
            raise SystemExit(
                f"CAM_CUT_DEPTH ({self.cut_depth} mm) exceeds the tool's "
                f"cutting-edge height ({TOOL_CUTTING_EDGE_MM} mm) — the flute "
                f"would bury above the cutting edge"
            )
        if self.cut_depth < self.stock_z:
            log(f"WARNING: CAM_CUT_DEPTH ({self.cut_depth}) < stock thickness "
                f"({self.stock_z}) — this will NOT cut all the way through")


def _float_env(name, default):
    val = os.environ.get(name)
    if val is None or val == "":
        return float(default)
    try:
        return float(val)
    except ValueError as e:
        raise SystemExit(f"{name} must be a number; got {val!r} ({e})")


# ---------------------------------------------------------------------------
# Geometry of the cut line
# ---------------------------------------------------------------------------

def cut_geometry(cfg):
    """Resolve the trim into a concrete cut line.

    Returns a dict describing the line:
      travel_axis  -- "Y" (right/left edges) or "X" (top/bottom edges)
      fixed_axis   -- the constant axis ("X" or "Y")
      fixed_val    -- tool-centre coordinate on the fixed axis (mm)
      travel_lo/hi -- start/end of the sweep on the travel axis (mm), incl. the
                      free-air overrun past each end of the sheet
      boundary     -- the kept/waste boundary on the fixed axis (the nominal
                      trim line, before kerf offset)
    """
    r = TOOL_DIAMETER_MM / 2.0
    offset = 0.0 if cfg.kerf_side == "center" else r

    if cfg.edge in ("right", "left"):
        travel_axis, fixed_axis = "Y", "X"
        travel_span = cfg.stock_y
        if cfg.edge == "right":
            boundary = cfg.stock_x - cfg.trim   # waste is +X of here
            fixed_val = boundary + offset       # tool onto the +X waste side
        else:  # left
            boundary = cfg.trim                 # waste is -X of here
            fixed_val = boundary - offset
    else:  # top / bottom
        travel_axis, fixed_axis = "X", "Y"
        travel_span = cfg.stock_x
        if cfg.edge == "top":
            boundary = cfg.stock_y - cfg.trim   # waste is +Y of here
            fixed_val = boundary + offset
        else:  # bottom
            boundary = cfg.trim                 # waste is -Y of here
            fixed_val = boundary - offset

    return {
        "travel_axis": travel_axis,
        "fixed_axis": fixed_axis,
        "fixed_val": fixed_val,
        "travel_lo": -cfg.overrun,
        "travel_hi": travel_span + cfg.overrun,
        "boundary": boundary,
    }


# ---------------------------------------------------------------------------
# Geometry: part + stock solids (FCStd visual review only — the toolpath below
# is built directly, not derived from these)
# ---------------------------------------------------------------------------

def build_solids(doc, cfg, geo):
    """Add the plain stock and the kept part (stock minus the waste strip) as
    Part solids for visual review in the saved FCStd. Top face at Z=0, bottom
    at Z=-stock_z, bottom-left corner at (0, 0)."""
    sx, sy, sz = cfg.stock_x, cfg.stock_y, cfg.stock_z

    stock_solid = Part.makeBox(sx, sy, sz)
    stock_solid.translate(FreeCAD.Vector(0, 0, -sz))
    stock = doc.addObject("Part::Feature", "Stock")
    stock.Shape = stock_solid

    # Waste strip: everything on the waste side of the boundary, full sheet on
    # the other axis, full thickness. Cut it from the stock to show the keeper.
    b = geo["boundary"]
    if cfg.edge == "right":
        waste = Part.makeBox(sx - b, sy, sz, FreeCAD.Vector(b, 0, -sz))
    elif cfg.edge == "left":
        waste = Part.makeBox(b, sy, sz, FreeCAD.Vector(0, 0, -sz))
    elif cfg.edge == "top":
        waste = Part.makeBox(sx, sy - b, sz, FreeCAD.Vector(0, b, -sz))
    else:  # bottom
        waste = Part.makeBox(sx, b, sz, FreeCAD.Vector(0, 0, -sz))

    part = doc.addObject("Part::Feature", "KeptPart")
    part.Shape = stock_solid.cut(waste)
    return part, stock


# ---------------------------------------------------------------------------
# Toolpath: straight parting cut along the travel axis, zig-zag stepping down
# ---------------------------------------------------------------------------

def build_toolpath(cfg, geo):
    """Return the list of Path.Command moves for the parting cut.

    The bit traverses in XY to the start end of the cut line (in free air off
    the sheet) FIRST, then descends; for each depth level it plunges straight
    down at the current end (still in free air) and feeds along the travel axis
    to the other end, cutting the full line. It alternates ends each level so
    there is no wasted reposition, and finally lifts clear above the sheet.

    Every plunge lands off the stock (travel coord < 0 or > stock span), so the
    bit never plunges full-width into solid ABS — it always enters the material
    from the side at depth.

    Why XY-before-Z (NOT a leading "G0 Z<clearance>") — read this: the operator
    homes (G28) before play, which parks the head HIGH above the work, well
    above our work-Z clearance. A leading "G0 Z10" would therefore *descend* at
    the park corner — and on a run with no tool change (T5 already loaded, so
    `T5 M6` is a no-op and the head never repositions off the park) that descent
    fouls the tool setter / a fixture sitting at the park corner (observed: a
    no-probe run plunged at the park corner and alarmed). Traversing XY first
    happens at the safe parked height; the only descent is then at the cut start
    in free air off an end. This makes a no-probe launch safe.
    """
    fa = geo["fixed_axis"]
    ta = geo["travel_axis"]
    fixed = geo["fixed_val"]
    lo, hi = geo["travel_lo"], geo["travel_hi"]

    # FreeCAD Path.Command feeds are in its internal velocity unit (mm/s); the
    # Makera post wraps F as a Velocity and multiplies by 60. Divide our mm/min
    # feeds by 60 so the posted F is correct. (Hand-built commands only — the
    # ToolController-driven ops in brass_holes don't need this.)
    feed_horiz = cfg.feed_horiz / 60.0
    feed_vert = cfg.feed_vert / 60.0

    # Depth levels: step down by `stepdown`, last level landing exactly on the
    # full cut depth.
    z_levels = []
    z = 0.0
    while z < cfg.cut_depth - 1e-9:
        z = min(z + cfg.stepdown, cfg.cut_depth)
        z_levels.append(-z)

    C = Path.Command

    def at(coord):
        """Build an XY dict placing the tool at `coord` on the travel axis and
        `fixed` on the fixed axis."""
        return {fa: fixed, ta: coord}

    cmds = []
    # XY to the start end FIRST (at the parked/retracted height), THEN descend —
    # see the docstring for why a leading "G0 Z<clearance>" would foul the park
    # corner on a no-tool-change launch.
    cmds.append(C("G0", at(lo)))                 # to the start end, in free air off it
    cmds.append(C("G0", {"Z": SAFE_HEIGHT_MM}))  # descend to just above stock

    end_coord = lo            # tool currently sits at the `lo` end
    for z in z_levels:
        # Plunge at the current end (free air off the sheet), then cut to the
        # far end along the travel axis.
        cmds.append(C("G1", {"Z": z, "F": feed_vert}))
        far = hi if end_coord == lo else lo
        cmds.append(C("G1", {**at(far), "F": feed_horiz}))
        end_coord = far

    cmds.append(C("G0", {"Z": CLEARANCE_HEIGHT_MM}))  # lift clear above the sheet
    return cmds, z_levels


# ---------------------------------------------------------------------------
# Post-generation safety assertions
# ---------------------------------------------------------------------------

def assert_safe(cfg, geo, cmds, z_levels):
    """Fail the build if the toolpath could mis-cut or foul.

    INVARIANTS:
      1. Cut depth within the tool's cutting-edge height (checked in Config,
         re-asserted here against the actual levels).
      2. Every plunge (commanded Z below the safe height) happens with the
         travel coordinate OFF the stock — i.e. in free air past an end — so
         the bit never plunges full-width into solid ABS.
      3. The program ends lifted to clearance above the sheet, so the G28
         traverse is safe.
    """
    tol = 1e-6
    span_lo, span_hi = 0.0, (
        cfg.stock_y if geo["travel_axis"] == "Y" else cfg.stock_x
    )
    ta = geo["travel_axis"]

    if z_levels and -z_levels[-1] > TOOL_CUTTING_EDGE_MM + tol:
        raise SystemExit(
            f"SAFETY: deepest level {-z_levels[-1]:g} mm exceeds the tool "
            f"cutting-edge height {TOOL_CUTTING_EDGE_MM:g} mm"
        )

    # Track tool position modally and check every plunge.
    pos = {"X": None, "Y": None, "Z": None}
    for i, c in enumerate(cmds):
        p = c.Parameters
        prev_z = pos["Z"]
        for k in ("X", "Y", "Z"):
            if k in p:
                pos[k] = float(p[k])
        # A plunge = a downward Z move ending below the safe height.
        going_down = "Z" in p and (prev_z is None or pos["Z"] < (prev_z - tol))
        if going_down and pos["Z"] < SAFE_HEIGHT_MM - tol:
            t = pos[ta]
            off_stock = (t is not None) and (
                t < span_lo - tol or t > span_hi + tol
            )
            if not off_stock:
                raise SystemExit(
                    f"SAFETY: cmd {i} plunges to Z={pos['Z']:g} at "
                    f"{ta}={t} — that is over the stock "
                    f"[{span_lo:g},{span_hi:g}], not in free air off an end"
                )

    last_z = [float(c.Parameters["Z"]) for c in cmds if "Z" in c.Parameters][-1]
    if last_z < CLEARANCE_HEIGHT_MM - tol:
        raise SystemExit(
            f"SAFETY: program must end lifted to the clearance height "
            f"({CLEARANCE_HEIGHT_MM:g} mm); ended at Z={last_z:g}"
        )

    kept = geo["boundary"]
    log(f"safety OK: {len(cmds)} cmds, {len(z_levels)} passes "
        f"[{z_levels[0]:g}..{z_levels[-1]:g}] along {ta} "
        f"[{geo['travel_lo']:g}..{geo['travel_hi']:g}] at "
        f"{geo['fixed_axis']}={geo['fixed_val']:.3f}; kept boundary at "
        f"{geo['fixed_axis']}={kept:.3f}")


# ---------------------------------------------------------------------------
# Post processor (shared post with brass_holes / pocket_slots)
# ---------------------------------------------------------------------------

def post_process(ops, out_path):
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

    # Makera's post aborts at the first G1/G2/G3 if SPINDLE_SPEED < 5. Pre-seed
    # its module-level global; the spindle is already started by the preamble
    # M3 S<rpm> so the per-line S matches state.
    if hasattr(mod, "SPINDLE_SPEED"):
        mod.SPINDLE_SPEED = float(SPINDLE_RPM)

    mod.export(ops, out_path, POST_ARGS)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    cfg = Config()
    out_path = os.path.abspath(cfg.out)

    geo = cut_geometry(cfg)

    doc = FreeCAD.newDocument("cam")

    log(f"sheet {cfg.stock_x} x {cfg.stock_y} x {cfg.stock_z} mm ABS, trim "
        f"{cfg.trim} mm off the {cfg.edge.upper()} edge")
    log(f"cut line: {geo['fixed_axis']}={geo['fixed_val']:.3f} mm "
        f"(kept boundary {geo['fixed_axis']}={geo['boundary']:.3f}, kerf side "
        f"'{cfg.kerf_side}'), sweep along {geo['travel_axis']} "
        f"[{geo['travel_lo']:g}..{geo['travel_hi']:g}] mm")
    log(f"depth {cfg.cut_depth} mm in {cfg.stepdown} mm steps, "
        f"feeds H={cfg.feed_horiz:g} V={cfg.feed_vert:g} mm/min")

    build_solids(doc, cfg, geo)

    cmds, z_levels = build_toolpath(cfg, geo)
    assert_safe(cfg, geo, cmds, z_levels)

    op = doc.addObject("Path::Feature", "BlockEdgeTrim")
    op.Path = Path.Path(cmds)
    doc.recompute()
    # Path::Feature has no recompute logic, but re-assert after recompute so the
    # saved FCStd and the posted path are definitely the commands we built.
    op.Path = Path.Path(cmds)

    log(f"{len(z_levels)} pass(es) to Z=-{cfg.cut_depth:g} mm; plunges land in "
        f"free air off the {geo['travel_axis']} ends")

    if cfg.fcstd:
        fcstd = os.path.abspath(cfg.fcstd)
        doc.saveAs(fcstd)
        log(f"saved FCStd to {fcstd}")

    log(f"posting G-code -> {out_path}")
    post_process([op], out_path)
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
