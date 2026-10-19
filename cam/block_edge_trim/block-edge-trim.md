# Block-edge trim pipeline (Carvera Air)

`cam/block_edge_trim/block_edge_trim.py` drives FreeCAD Path headlessly via
`freecadcmd` to cut a **single straight line clean through a thin sheet** —
trimming a waste strip off one edge of the stock. It is a **parting cut**, not
a facing or profiling pass: the bit runs back and forth along one straight line,
stepping down a little each pass until it is all the way through.

A generic CAM pipeline — lives under `cam/` because nothing about it is bound to
one product. Use it for any thin rectangular sheet you need cut down to size.
Dimensions, edge, trim amount and depth are env-var driven.

## Why a hand-built line, not a Profile / Pocket op

We want a single straight severing cut, so the toolpath is built directly as a
list of `Path.Command` moves
rather than driven by a FreeCAD **Profile** op (which traces a whole part
outline) or a **Pocket** op (which clears an area). That keeps the cut to one
clean line and gives full control over the kerf side, the free-air plunge
points, and the stepdown.

Note this is **thin-sheet** work: the stock is only a few mm tall, so the
spindle lifts the bit clear above the top between moves and we use a normal
top-down approach. That is the opposite of slot-pocketing / end-trim jobs on tall stock,
where a tall hardwood brick puts the stock top near the Z-travel limit and
forces a lateral side-entry — none of that applies here.

## How the cut runs

For a **right/left**-edge trim the cut line is vertical (a fixed X), so the bit
**sweeps along Y**; for a **top/bottom**-edge trim it is horizontal (fixed Y) and
sweeps along X. The sweep runs a little past **both** ends of the sheet (the
`CAM_OVERRUN`, default 3 mm of free air) so the strip separates fully at the
corners.

The program **traverses in XY to the start end first, then descends** — it does
*not* lead with a `G0 Z<clearance>` move. The operator homes (`G28`) before
play, which parks the head high above the work; a leading `G0 Z10` would
therefore *descend* at the park corner, and on a launch with no tool change (the
bit already loaded, so `T5 M6` is a no-op and the head never repositions off the
park) that descent fouls the tool setter / a fixture at the park corner — an
observed crash. Traversing XY first happens at the safe parked height, so the
only descent is at the cut start in free air. This makes a **no-probe launch
safe** (you don't need the auto-Z-probe's tool change to pull the head off the
park first).

Then each depth pass:

1. plunges straight down at the current end — which is **off the sheet, in free
   air** — so the bit never plunges full-width into solid ABS;
2. feeds along the travel axis to the far end, cutting the full line;
3. the next pass plunges at *that* end and cuts back — alternating, no wasted
   repositioning.

After the last pass the bit lifts to the clearance height above the sheet, so
the postamble `G28` traverse is safe.

## Kerf placement

`CAM_KERF_SIDE=waste` (default) offsets the tool one **radius onto the waste
side** of the nominal trim line, so the **kept piece comes out exactly the
nominal size**. For the default job (trim 42.48 mm off a 232 mm-wide sheet) the
kept left piece is exactly `232 − 42.48 = 189.52` mm, and the tool centreline
runs at X = 190.52. Use `CAM_KERF_SIDE=center` to put the tool centreline on the
line instead (kept piece a radius — 1 mm — smaller).

## Running the pipeline

WSL (Linux AppImage extracted at `~/squashfs-root/`):

```bash
CAM_OUT=cam/block_edge_trim/block_edge_trim.gcode \
~/squashfs-root/usr/bin/freecadcmd cam/block_edge_trim/block_edge_trim.py
```

macOS (`freecadcmd` inside the FreeCAD.app bundle):

```bash
CAM_OUT=cam/block_edge_trim/block_edge_trim.gcode \
/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd cam/block_edge_trim/block_edge_trim.py
```

That writes both the G-code and (by default) `block_edge_trim.FCStd` alongside
it (stock + kept part + toolpath) for visual review in the FreeCAD GUI.

Variables (all have defaults — the minimum invocation is just `CAM_OUT`):

| Var              | Default  | Meaning                                                                                                   |
| ---------------- | -------- | --------------------------------------------------------------------------------------------------------- |
| `CAM_OUT`        | alongside | Output G-code file. Defaults to `block_edge_trim.gcode` next to the script.                               |
| `CAM_FCSTD`      | —        | Override the auto-derived FCStd path. Set empty (`CAM_FCSTD=`) to skip saving it (rare — always-on is the standard). |
| `CAM_STOCK_X`    | `232`    | Stock width (mm, across).                                                                                  |
| `CAM_STOCK_Y`    | `132`    | Stock depth (mm).                                                                                          |
| `CAM_STOCK_Z`    | `4.04`   | Stock thickness (mm).                                                                                      |
| `CAM_EDGE`       | `right`  | Which edge to trim: `right`, `left`, `top`, `bottom`. right/left sweep Y; top/bottom sweep X.              |
| `CAM_TRIM`       | `42.48`  | How much to remove from that edge (mm). Must be < the stock dimension on the trimmed axis.                 |
| `CAM_CUT_DEPTH`  | `4.3`    | Cut depth (mm, positive). Default a touch past the 4.04 mm sheet so it severs fully. Must be ≤ the bit's 8 mm cutting-edge height. |
| `CAM_STEPDOWN`   | `1.0`    | Axial depth per pass (mm). 1.0 is sensible for ABS with the 2 mm bit (4.3 → 5 passes). Raise for fewer passes if the cut is happy; drop if it melts/loads. |
| `CAM_FEED_HORIZ` | `500`    | Cutting feed along the line (mm/min). From the .fctb; tune for ABS (see below).                            |
| `CAM_FEED_VERT`  | `200`    | Plunge feed (mm/min). From the .fctb.                                                                      |
| `CAM_OVERRUN`    | `3`      | Free-air run past each end of the line (mm). Must be ≥ the tool radius so corners separate.                |
| `CAM_KERF_SIDE`  | `waste`  | `waste` keeps the kept piece exact (tool a radius onto the waste side); `center` runs the centreline on the line. |

## Work-coordinate convention

Carvera Air zero is the **top surface** of the stock, **bottom-left** corner
viewed from above. The sheet sits top face at Z=0, bottom at Z=−`stock_z`,
bottom-left corner at (0, 0). Fixture the sheet with the trimmed (right) edge
facing **+X**.

## Tool and feeds/speeds

Tool geometry comes from `tools/endmill_2mm_1F_8L_alu.fctb` — a 2 mm, 1-flute
carbide endmill, 8 mm flute length, at ATC slot **5** (so the preamble loads
`T5`). Spindle 12 000 RPM. The .fctb's feeds are **aluminum**-tuned; the
pipeline reuses them as a safe starting point but the *cut* params here are
chosen for **ABS**:

- Stepdown **1.0 mm** (the .fctb's 0.15 mm is an aluminum helix pitch — far too
  timid for a parting cut).
- Feeds 500 / 200 mm/min. **ABS melts when the chipload is too low** (the tool
  rubs instead of cutting): if the cut smears/gums rather than throwing chips,
  **raise the feed** (or drop the rpm) to get a coarser chip. Single-flute helps
  chip evacuation in plastic. Tune on the machine.

Safe/clearance heights are Python constants: rapid-down to **5 mm** above the
top, full retract / park at **10 mm**.

## G-code preamble / postamble

A hand-prep warning + tool legend (per CLAUDE.md, "CAM G-code: tool legend at
the top"), then the standard Makera setup:

```
G90 G94
G17
G54
T5 M6      -- Carvera ATC: load tool 5 (2mm 1F endmill)
M3 S12000  -- spindle forward at 12k RPM
M851 S75   -- dust extractor (ext-out) on at 75%
```

Postamble: `M05 / M852 / G17 G90 / G28 / M30`. The **dust extractor** runs for
exactly the cut: `M851 S75` (75% PWM) fires next to `M3` in the preamble and
`M852` shuts it off next to `M05` in the postamble — the Carvera's External
Control ("ext out") output our extractor is wired to (**not** `M801`/`M802`, the
built-in vacuum, which doesn't drive it — see `docs/carvera-air.md`). The
percentage is the `EXTRACTOR_PCT` constant at the top of the script.

## Operator notes (important for a through-cut)

- **Spoilboard**: the cut goes ~0.3 mm past the underside, so the cut line must
  sit over a sacrificial layer or a clear gap — not bare fixture.
- **Secure the offcut**: the waste strip comes free at the end of the cut. Tape
  or clamp it so it can't shift into the cutter, and clamp the **kept** side,
  clear of the cut line.

## Known sharp edges / gotchas

Shared with the other hand-built-`Path.Command` pipeline (`pocket_slots`):

- **`Path.Command` feed values are in mm/s** — the Makera post wraps F as a
  Velocity and multiplies by 60. The script divides the mm/min feeds by 60
  before building the commands, so the posted F is correct. (The
  ToolController-driven ops in `brass_holes` don't need this — only hand-built
  commands do.)
- **`freecadcmd` swallows positional args** (→ env-var config) and **swallows
  script-level `print()`** (→ `sys.stderr.write` + `flush`).
- **Makera's post aborts at the first motion if `SPINDLE_SPEED < 5`.** The
  script pre-seeds `makera_post.SPINDLE_SPEED` to the target RPM before
  `export()`.

## Extending

Currently set up to trim **one** edge per run (default `right`, the only edge in
the task so far — though `left`/`top`/`bottom` are wired and validated in the
geometry). Obvious next steps:

1. **All four edges in one program.** The original job removed 9.68 / 15.7 /
   42.48 / 35.6 mm off top/left/right/bottom of a 232 × 132 sheet. A multi-edge
   mode would emit the four straight cuts in sequence (mind which strips come
   free and when — order the cuts so the workpiece stays clamped).
2. **Lead-in tabs.** For an unclamped offcut, leaving a thin tab (skip the last
   ~0.2 mm over a short span) keeps the strip attached until snapped off by hand.
