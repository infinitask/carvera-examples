# Disc M5-hole drilling pipeline (Carvera Air)

`cam/drill_m5_hole_metal_v2/drill_m5_hole_metal_v2.py` drives FreeCAD
Path headlessly via `freecadcmd` to helical-mill **one central
through-hole** in a round metal disc. The disc geometry is built **in
code** with `Part.makeCylinder` plus a `Part.makeCylinder` Boolean cut —
there is no STL in the loop.

A generic CAM pipeline — lives under `cam/` because nothing about it is
bound to one product. Use it for any round disc that needs a single
concentric hole; the dimensions are env-var driven.

The default job is the one it was written for: a **25 mm diameter ×
1.6 mm stainless-steel disc, one central 5.3 mm hole (M5 close-fit
clearance), drilled to 1.9 mm depth** so it clears the bottom of the
1.6 mm disc by ~0.3 mm.

## Why helical milling, not a drill cycle?

The hole (5.3 mm) is far wider than the tool (2 mm), so a plain drill
cycle (`G81`/`G82`/`G83`) can't make it — a twist/plunge drill only ever
bores a hole at its own diameter. Helical interpolation has the tool
spiral around a circle of radius `(hole_radius − tool_radius)` while
ramping down, removing the hole volume with **no straight plunge**. For
the default config that's a **1.65 mm-radius spiral** with pitch =
StepDown. This is the same technique as [`cam/brass_holes/`](../brass_holes/)
— the reference pipeline this one is modelled on.

### Two concentric helical passes, drilled INSIDE-OUT (5.3 mm hole, 2 mm tool)

For this hole/tool ratio FreeCAD emits **two** helical passes. The order
matters — the op is set to `StartSide = "Inside"` so it drills **from the
centre outward**:

- **Inner pass first** — tool centre spirals at radius **1.0 mm** (sweep
  0 → 2.0 mm), clearing the central material.
- **Outer pass second** — tool centre spirals at radius **1.65 mm** (sweep
  0.65 → 2.65 mm), widening to the finished wall at radius 2.65 mm (Ø5.3).

Together they clear radius 0 → 2.65 = **Ø5.3 exactly**. (Contrast
`brass_holes`' 4 mm hole, which clears in a single pass because the 2 mm
tool at a 1.0 mm spiral radius already reaches the centre.)

**Why inside-out (learned on the first real cut).** The original order was
outside-in (`StartSide = "Outside"`): it cut the finished wall first and
left a ~1.3 mm-diameter **free-standing core** in the middle, which snapped
off and **flew out** of the machine as the final inner pass reached it —
a metal projectile. Inside-out removes the centre first, so every later
pass is only ever *widening an existing hole*; nothing is left unsupported
to break loose. Reach for `StartSide = "Inside"` on any helical bore wider
than the tool.

**Tradeoff: inside-out forces conventional milling here.** With
`StartSide = "Inside"`, FreeCAD's Helix always posts `G3` (conventional on
this internal feature) *regardless of the `Direction` property* — verified
by posting with both `"CW"` and `"CCW"` (identical output). The old
outside-in order posted `G2` (climb). Climb (best finish, least
work-hardening in stainless) is **not recoverable** once you go inside-out.
Conventional is accepted here: a through-hole in thin stock, light finish
engagement, feeds already proven on the material. If a future job needs
climb *and* no flying core, you'd need a different strategy (e.g. a small
centre plunge/rough op before an outside-in finish) rather than one Helix
op.

## Running the pipeline

Defaults give the job above; override any env var to adapt. WSL (FreeCAD
AppImage extracted at `~/squashfs-root/`):

```bash
CAM_OUT=cam/drill_m5_hole_metal_v2/drill_m5_hole_metal_v2.gcode \
~/squashfs-root/usr/bin/freecadcmd cam/drill_m5_hole_metal_v2/drill_m5_hole_metal_v2.py
```

macOS (`freecadcmd` inside the FreeCAD.app bundle):

```bash
CAM_OUT=cam/drill_m5_hole_metal_v2/drill_m5_hole_metal_v2.gcode \
/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd cam/drill_m5_hole_metal_v2/drill_m5_hole_metal_v2.py
```

That writes both the G-code and (by default) `drill_m5_hole_metal_v2.FCStd`
alongside it for inspection in the FreeCAD GUI.

Variables (all have defaults, so the minimum invocation is just `CAM_OUT`):

| Var                 | Default | Meaning |
| ------------------- | ------- | ------- |
| `CAM_OUT`           | —       | Output G-code file (required). |
| `CAM_FCSTD`         | —       | Override the auto-derived FCStd path. Set empty (`CAM_FCSTD=`) to skip saving it (rare — always-on is the standard; see `docs/cam-conventions.md`). |
| `CAM_DISC_DIAMETER` | `25`    | Disc diameter (mm). |
| `CAM_DRILL_DEPTH`   | `1.9`   | Depth the hole is drilled to (mm) = the modelled solid thickness. Deliberately ~0.3 mm deeper than the real 1.6 mm disc so the helix clears the bottom despite thickness variation; the bit spends the last ~0.3 mm in air. |
| `CAM_HOLE_DIAMETER` | `5.3`   | Finished hole diameter (mm). Must be **> tool diameter** (2 mm) to helix-mill, and **< disc diameter**. |
| `CAM_CENTER_X`      | radius  | X of the disc/hole centre from anchor 1 (mm). Defaults to `disc_radius` (disc tangent to the anchor-1 X axis). Must be ≥ radius so the whole disc stays in +X. |
| `CAM_CENTER_Y`      | radius  | Y of the disc/hole centre from anchor 1 (mm). Defaults to `disc_radius` (tangent to the anchor-1 Y axis). Must be ≥ radius. |

## Work-coordinate convention

Carvera Air zero is the **top surface** of the stock; X/Y zero is
**anchor 1** (the machine's fixed front-left work-holding corner, which
`G54` is set to — see [`docs/carvera-air.md`](../../docs/carvera-air.md)).
The disc is fixtured in the **+X/+Y** quadrant from anchor 1 and, by
default, placed **tangent to both anchor axes**, so its centre sits at
`(disc_radius, disc_radius)` = **(12.5, 12.5)** and every toolpath
coordinate is positive. No part-centre re-zero. The disc top is at Z=0,
the hole is cut to Z=−1.9.

**Ensure there is clearance under the hole** — the bit exits ~0.3 mm
past the underside of the disc, so the fixture must have relief (or a
sacrificial backer) beneath the hole. The prep block at the top of the
G-code repeats this.

## Geometry (default values)

- Disc: Ø25 mm, modelled 1.9 mm thick (= drill depth; real disc 1.6 mm)
- One central through-hole, Ø5.3 mm (M5 close-fit clearance)
- Centre at (12.5, 12.5); wall from hole edge to disc edge = 12.5 − 2.65
  = **9.85 mm**
- Helix spiral radius = hole_radius − tool_radius = 2.65 − 1.0 = **1.65 mm**

`drill_m5_hole_metal_v2_preview.png` is a 3-up render of this **target
part** (round disc + round hole), rendered from `..._part.stl` (exported
from the FCStd). See the note under *Previewing* for why it's a part
render and not a stock simulation.

## Tool and feeds/speeds

Stored in [`tools/endmill_2mm_1F_8L_alu.fctb`](../../tools/endmill_2mm_1F_8L_alu.fctb)
— a 2 mm, 1-flute carbide endmill, 8 mm flute length. ATC slot **T5**
(`tools/carvera_air.fctl`). Pipeline reads, verbatim:

- SpindleSpeed: 12 000 RPM
- HorizFeed: 500 mm/min
- VertFeed (plunge): 200 mm/min
- RampFeed: 300 mm/min
- Stepdown: 0.15 mm

**Material note.** This is the *aluminium* preset (the only metal tool in
`tools/`). The job owner has confirmed these feeds are **dialled in for
stainless steel** on this machine, so they are used **as-is** — the
pipeline does not override them. If a real cut chatters / smears /
squeals, the first lever is to drop `HorizFeed` (the `.fctb`'s
`SourceNote` records that the feeds were Fusion-computed assuming 4
flutes on a physically 1-flute bit, so effective chipload is already 4×
the stated `Chipload`). Stainless also work-hardens — keep the bit
moving, and an air blast (`M7`/`M9`) to clear chips is worth adding at
the machine if swarf packs the flute.

Safe/clearance heights are Python constants in the script: Safe 5 mm,
Clearance 10 mm (relative to the stock top).

## G-code preamble / postamble

Preamble carries a **hand-prep warning** and a **tool legend** (required
by CLAUDE.md, "CAM G-code: tool legend at the top") ahead of the modes:

```
(*** PREP THE MACHINE BY HAND BEFORE RUNNING ***)
(  ... load T5, fixture +X/+Y tangent to anchor 1, set G54 XY, probe Z ...)
(=== Tool table - load these bits before running ===)
(T5 = 2mm 1F 8mm End Mill [Aluminum])
G90 G94
G17
G54        -- work coord system 1 = anchor 1
T5 M6      -- Carvera ATC: load tool 5 (2mm 1F metal endmill)
M3 S12000  -- spindle forward at 12k RPM
```

Postamble: `M05 / G17 G90 / G28 / M30` (spindle off, restore modes, park
at clearance, end). `G28` lifts Z first, so it is safe on this shallow
job.

## Known sharp edges / gotchas

- **Round base solid → the outer rim is a cylindrical face too.** A disc
  is a cylinder, so face-detection can't just grab "every cylindrical
  face" like `brass_holes` does on a box — it would try to "drill" the
  25 mm outer rim. `find_hole_faces()` filters by **radius** and selects
  only the face at the hole radius (and asserts there is exactly one).
- **The bit name has parentheses; a `(` closes a G-code comment early.**
  The `.fctb` name is `2mm 1F 8mm End Mill (Aluminum)`; the tool legend
  bracket-swaps the parens → `[Aluminum]` (`_safe_comment()`), keeping
  the comment ASCII and un-truncated on the Carvera. (Same hazard
  documented in `pocket_slots._prep_warning`.)
- **`Units.Quantity(500, Units.Velocity)` means 500 mm/s**, not mm/min.
  Feeds are passed as `"N mm/min"` string literals to avoid the 60×
  error (as in `brass_holes`).
- **Path op depth properties are expression-bound by default.** A
  `doc.recompute()` re-evaluates and overwrites literals; the script
  calls `op.setExpression(prop, None)` first to detach.
- **`freecadcmd` swallows positional args** (→ env-var config) and
  **script-level `print()`** (→ `sys.stderr.write` + flush).
- **Makera's post aborts at the first motion if `SPINDLE_SPEED < 5`** —
  the script pre-seeds `makera_post.SPINDLE_SPEED` before `export()`.

## Previewing

The repo's `python/gcode_simulate.py` **cannot** preview this pipeline
faithfully: it only interprets `G0`/`G1` moves and treats each `G2`/`G3`
**arc as a straight line to its endpoint**. This job's bore is entirely
`G2` arcs, so a stock simulation renders the round hole as a horizontal
**slot** — actively misleading, so it is deliberately **not** committed.
Instead:

- `drill_m5_hole_metal_v2.FCStd` is the authoritative visual artefact —
  open it in the FreeCAD GUI to inspect the part, stock, and the actual
  helical toolpath (same precedent as `brass_holes`, which also emits
  arcs and commits no stock PNG).
- `drill_m5_hole_metal_v2_preview.png` (rendered from
  `drill_m5_hole_metal_v2_part.stl`) is an honest 3-up of the **target
  part geometry** — a round disc with a centred round through-hole — for
  a one-glance check that the hole is concentric and correctly sized.

Toolpath correctness (round arcs, centre 12.5/12.5, spiral radius 1.65,
depth −1.9, feeds from the `.fctb`) is verified by reading the emitted
`.gcode` directly. For rapid-move / crash checking, use Carvera
Controller's own preview at the machine.

## Sending to the machine

Not part of this pipeline. Slicing/authoring and dispatch are separate
steps, and the CNC is **local-only** (never dispatched from CI). When you
do run it, load T5, set the XY work zero to anchor 1 and probe Z on the
disc top per the prep block, ensure relief under the hole, and launch
from the lab PC. See [`docs/carvera-air.md`](../../docs/carvera-air.md).
