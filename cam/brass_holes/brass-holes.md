# Brass-plate hole-drilling pipeline (Carvera Air)

`cam/brass_holes/brass_holes.py` drives FreeCAD Path headlessly via
`freecadcmd` to helical-mill a set of through-holes in a rectangular
brass plate. The plate geometry is built **in code** using `Part.makeBox`
plus `Part.makeCylinder` Boolean cuts — there is no STL in the loop.

A generic CAM pipeline — lives under `cam/` because nothing about it
is bound to one product. Use it for any rectangular plate with N
through-holes; the dimensions and hole pattern are env-var driven.

## Why helical milling, not drilling?

Default geometry is four 4 mm holes cut with the **2 mm** 1F aluminum
endmill (`tools/endmill_2mm_1F_8L_alu.fctb`). A plain drill cycle
(`G81`) can only drill holes at the tool's own diameter — it can't
widen. Helical interpolation has the tool spiral around a circle of
radius `(hole_radius − tool_radius)` while ramping down, so it
removes the full hole volume without a plunge. For the default config
that's a **1 mm-radius spiral** with pitch = StepDown.

## Running the pipeline

Defaults give the job in the task prompt; override any of the env
vars to adapt. The env-var contract is identical across platforms;
only the path to `freecadcmd` differs.

WSL (Linux AppImage extracted at `~/squashfs-root/`):

```bash
CAM_OUT=cam/brass_holes/brass_holes.gcode \
~/squashfs-root/usr/bin/freecadcmd cam/brass_holes/brass_holes.py
```

macOS (`freecadcmd` shipped inside the FreeCAD.app bundle):

```bash
CAM_OUT=cam/brass_holes/brass_holes.gcode \
/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd cam/brass_holes/brass_holes.py
```

That writes both the G-code and (by default) `brass_holes.FCStd`
alongside it for visual inspection in the FreeCAD GUI.

Variables (all have sensible defaults so the minimum invocation is
just `CAM_OUT`):

| Var                   | Default | Meaning                                                                                                                                                       |
| --------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `CAM_OUT`             | —       | Output G-code file (required).                                                                                                                                |
| `CAM_FCSTD`           | —       | Override the auto-derived FCStd path. Set to empty (`CAM_FCSTD=`) to skip saving it (rare — always-on is the standard; see `docs/cam-conventions.md`).                        |
| `CAM_PLATE_X`         | `100`   | Plate X dimension (mm).                                                                                                                                       |
| `CAM_PLATE_Y`         | `100`   | Plate Y dimension (mm).                                                                                                                                       |
| `CAM_PLATE_Z`         | `1.3`   | Plate thickness (mm). Defaults to 1.3 — deliberately ~0.3 mm deeper than the nominal 1 mm sheet to ensure through-cuts despite stock thickness variation. The bit will spend the last ~0.3 mm in air once it's cleared the real material. |
| `CAM_HOLE_DIAMETER`   | `4`     | Hole diameter (mm). Must be ≥ tool diameter, else the script aborts.                                                                                          |
| `CAM_HOLE_INSET`      | `8`     | Distance from each plate edge to the centre of the nearest hole (mm). The four holes sit at `(inset, inset)`, `(plate_x − inset, inset)`, and the two mirrors on the far Y edge. |

## Work-coordinate convention

Carvera Air zero is the **top surface** of
the stock, **bottom-left** corner viewed from above. The plate is
placed so that its top face sits at Z=0, bottom-left at (0, 0). Stock
matches the plate exactly (`StockFromBase` with all extensions = 0) —
for this kind of sheet-stock job there is no excess material to
trim away first.

## Geometry (default values)

- Plate: 100 × 100 × 1.3 mm brass
- Four through-holes, 4 mm diameter
- Centres at `(8, 8)`, `(92, 8)`, `(8, 92)`, `(92, 92)` — 8 mm inset
  from the nearest two edges
- Edge-to-edge distance from hole to plate edge: `inset − hole_radius`
  = 6 mm (wall thickness around each hole)
- Centre-to-centre distance along X and Y: 84 mm

## Tool and feeds/speeds

Stored in `tools/endmill_2mm_1F_8L_alu.fctb` — a 2 mm, 1-flute carbide
endmill, 8 mm flute length, rated for aluminum. Pipeline reads:

- SpindleSpeed: 12 000 RPM
- HorizFeed: 500 mm/min
- VertFeed: 200 mm/min
- RampFeed: 300 mm/min
- Stepdown: 0.15 mm

**These feeds came from a Fusion 360 aluminum preset that was configured
assuming 4 flutes.** The tool is actually 1 flute (corrected 2026-04-23
in both the filename and the `Flutes` field), which means the effective
per-tooth chipload at the listed feeds is 4× what the `Chipload`
parameter says (0.0417 mm/tooth rather than 0.0167). For brass at the
small radial engagement of a widening helix this is acceptable, but if
the cut goes noisy / chatters / smears, the first lever is to drop
`HorizFeed` by ~4×. See the SourceNote on the `.fctb` file for history.

Safe/clearance heights are still Python constants in `brass_holes.py`:

- Safe height 5 mm, Clearance 10 mm

## G-code preamble / postamble

Standard Makera-post setup. The tool number in the preamble is **`T5`**
because the 2 mm aluminum endmill is at slot 5 in
`tools/carvera_air.fctl`. Preamble:

```
G90 G94    -- from Makera default
G17
G54        -- work coord system 1
T5 M6      -- Carvera ATC: load tool 5 (2mm 1F aluminum endmill)
M3 S12000  -- spindle forward at 12k RPM
```

Postamble is Makera's default (`M05 / G17 G90 / G28 / M30`).

## Known sharp edges / gotchas

Gotchas that have hit this pipeline:

- **`Units.Quantity(650, Units.Velocity)` means 650 mm/s**, not mm/min.
  Pass `"650 mm/min"` as a string literal to avoid the 60× error.
- **Path op depth properties are expression-bound by default**
  (`StepDown` etc. are tied to `OpToolDiameter` / `OpFinalDepth`). A
  `doc.recompute()` re-evaluates the expression and overwrites any
  literal value we set. Call `op.setExpression(prop, None)` to detach
  the binding first.
- **`freecadcmd` swallows positional args** (→ env-var config) and
  **swallows script-level `print()`** (→ `sys.stderr.write` + `flush`).
- **Makera's post aborts at the first motion if `SPINDLE_SPEED < 5`.**
  The script pre-seeds `makera_post.SPINDLE_SPEED` to the target RPM
  before calling `export()`.

New to this pipeline (none discovered yet beyond the above — this is a
simpler job than the block-edge trim — but record any here as they come
up).

## Extending

Obvious next variants:

1. **Arbitrary hole pattern.** Currently the 4 hole centres are
   computed from `CAM_HOLE_INSET`. A list-of-centres env var would let
   the same pipeline drive any N-hole pattern.
2. **Peck drilling for deeper plates.** At Z ≫ 1.3 mm on a 1F bit,
   swarf evacuation becomes the limit. Helix with retract-between-turns
   (`PathDressup.Tag` or a custom macro) would lift between stepdowns.
3. **Mixed-diameter holes in one job.** The current script applies a
   single `CAM_HOLE_DIAMETER` to every centre. A list-of-(x, y, d)
   tuples would allow different sizes — each would become its own set
   of cylindrical faces in the base, and the Helix op picks them all
   up automatically (StepDown and feeds are per-op, not per-hole).
