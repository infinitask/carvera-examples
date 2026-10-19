# Single-side (X-neg) brass-plate hole-drilling pipeline (Carvera Air)

`brass_holes_xneg/brass_holes_xneg.py` is a sibling of
`cam/brass_holes/brass_holes.py` for sheet stock that is **too large to
fit a four-corner pattern in one Carvera Air setup**. It drills only
the **two holes on the X-negative side** of the plate; the operator
flips the workpiece 180° in-plane and re-runs the same G-code to drill
the other two.

Same tool (2 mm 1F aluminum endmill, T5), same Helix op, same Makera
post processor, same env-var contract — only the hole pattern and the
plate-size defaults differ.

## When to use which

| Stock fits all four corners in one setup? | Use |
| ----------------------------------------- | --- |
| Yes                                       | `cam/brass_holes/brass_holes.py` (one run) |
| No (too long in X)                        | `brass_holes_xneg/brass_holes_xneg.py` (run twice with a 180° flip between) |

The Carvera Air has a working envelope of ~360 × 240 mm in XY but
fixturing, clamps, and probe routes can shrink that significantly. The
single-side variant assumes you've decided ahead of time that the
X-negative half of the plate is reachable and the X-positive half isn't
(typical for L-fixtures or side clamps that block the X-positive side).

## Two-pass workflow

1. Mount the plate so the long edge that should receive the **first**
   pair of holes faces the X-negative side of the bed. Probe / set
   work zero at the bottom-left corner of the stock as usual.
2. Run the G-code → drills the two holes at `(inset, inset)` and
   `(inset, plate_y − inset)`.
3. Loosen the fixture, **rotate the workpiece 180° in plane** (the
   long edge that was on X-positive is now on X-negative). Re-probe /
   re-zero at the new bottom-left corner.
4. Re-run the **same** G-code — the same two near-side holes are now
   drilled on the previously-unreachable side.

After both passes the plate has the same four-corner pattern that
`cam/brass_holes/` produces in one shot on smaller stock.

Implication: the `CAM_PLATE_X` / `CAM_PLATE_Y` you pass should be the
**actual** stock dimensions, not "half a plate" — the script needs the
true Y dimension to compute `(inset, plate_y − inset)` for the second
hole, and the true X dimension to validate that the inset fits.

## Running the pipeline

WSL (Linux AppImage extracted at `~/squashfs-root/`):

```bash
CAM_OUT=cam/brass_holes_xneg/brass_holes_xneg.gcode \
~/squashfs-root/usr/bin/freecadcmd cam/brass_holes_xneg/brass_holes_xneg.py
```

macOS (`freecadcmd` shipped inside the FreeCAD.app bundle):

```bash
CAM_OUT=cam/brass_holes_xneg/brass_holes_xneg.gcode \
/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd cam/brass_holes_xneg/brass_holes_xneg.py
```

Linux (`freecadcmd` on PATH, e.g. an extracted FreeCAD AppImage — see
`docs/freecad-wsl-install.md`):

```bash
CAM_OUT=cam/brass_holes_xneg/brass_holes_xneg.gcode \
freecadcmd cam/brass_holes_xneg/brass_holes_xneg.py
```

That writes both the G-code and (by default)
`brass_holes_xneg.FCStd` alongside it for visual inspection in the
FreeCAD GUI.

Variables (all have sensible defaults so the minimum invocation is
just `CAM_OUT`):

| Var                   | Default | Meaning                                                                                                                                                       |
| --------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `CAM_OUT`             | —       | Output G-code file (required).                                                                                                                                |
| `CAM_FCSTD`           | —       | Override the auto-derived FCStd path. Set to empty (`CAM_FCSTD=`) to skip saving it (rare — always-on is the standard; see `docs/cam-conventions.md`).                        |
| `CAM_PLATE_X`         | `200`   | Plate X dimension (mm). Default reflects the typical "too big" use case from the task that motivated this script.                                             |
| `CAM_PLATE_Y`         | `300`   | Plate Y dimension (mm). Same.                                                                                                                                 |
| `CAM_PLATE_Z`         | `1.3`   | Plate thickness (mm). Same `1.0 → 1.3 mm` over-cut as `cam/brass_holes/` to absorb sheet-thickness variation.                                                     |
| `CAM_HOLE_DIAMETER`   | `4`     | Hole diameter (mm). Must be ≥ tool diameter, else the script aborts.                                                                                          |
| `CAM_HOLE_INSET`      | `8`     | Distance from the X-negative edge and from the nearest Y edge to the centre of each hole (mm). Holes sit at `(inset, inset)` and `(inset, plate_y − inset)`.  |

## Work-coordinate convention

Same as `cam/brass_holes/`: Carvera Air zero is the
**top surface** of the stock, **bottom-left** corner viewed from above.
The plate is placed so that its top face sits at Z=0 and its
bottom-left corner at (0, 0). Stock matches the plate exactly
(`StockFromBase` with all extensions = 0).

When you flip the workpiece for the second pass, **re-probe / re-zero
at the new bottom-left corner** of the now-rotated stock. The G-code
itself is unchanged.

## Geometry (default values)

- Plate: 200 × 300 × 1.3 mm brass
- Two through-holes, 4 mm diameter, both on the X-negative side
- Centres at `(8, 8)` and `(8, 292)` — 8 mm inset from the X-negative
  edge and from the nearest Y edge
- Edge-to-edge distance from hole to plate edge:
  `inset − hole_radius` = 6 mm (wall thickness around each hole)
- Centre-to-centre distance along Y: `plate_y − 2·inset` = 284 mm

## Tool, feeds/speeds, preamble/postamble

Identical to `cam/brass_holes/`: 2 mm 1F aluminum endmill at slot 5,
12 000 RPM, 500 mm/min HorizFeed, 200 mm/min VertFeed, 0.15 mm
Stepdown. Preamble `T5 M6 / M3 S12000`, Makera default postamble. See
`cam/brass_holes/brass-holes.md` for the full table and the (important)
note about the 4-flute → 1-flute Fusion preset history that affects
the effective chipload.

## Validation differences vs `cam/brass_holes/`

The original script checks that `plate − 2·inset ≥ hole_diameter` on
**both** axes (so neither pair of corner holes overlaps). This script
has only **one** X position, so:

- X-axis check: `radius ≤ inset ≤ plate_x − radius` — the single hole
  must fit fully inside the plate width. There is no two-hole
  separation check on X.
- Y-axis check: same as the original — `plate_y − 2·inset ≥ hole_diameter`
  so the two holes don't overlap.

This means the script will accept very narrow plates (e.g. `plate_x =
20 mm`) without complaint, as long as the inset itself fits.

## Known sharp edges / gotchas

All gotchas listed under `cam/brass_holes/brass-holes.md` § "Known sharp
edges" apply unchanged here (Quantity-velocity 60× trap,
expression-bound depth properties, freecadcmd swallowing argv,
Makera post `SPINDLE_SPEED < 5` abort). Nothing new specific to the
single-side variant has surfaced yet — log here if it does.

## Extending

The same three obvious next variants from `cam/brass_holes/`'s doc apply:
arbitrary hole pattern, peck/retract for thicker stock, mixed-diameter
holes. A natural fourth, specific to the single-side workflow, would
be a `CAM_SIDE` env var (`xneg` / `xpos` / `yneg` / `ypos`) that
reflects the two holes onto whichever edge is reachable in the current
setup — useful for fixtures that block a different side. Not
implemented today; one direction (X-negative) is enough for the job
that motivated this script.
