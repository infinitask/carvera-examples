# `gcode_simulate.py`

A 2.5D Z-buffer simulator for 3-axis CNC milling jobs. Reads one or more `.gcode` files in the order they'd run on the machine, simulates the cuts against a rectangular stock blank, and writes out:

- A binary STL of the **remaining stock surface** (top + four side walls + bottom). Load it in any STL viewer, FreeCAD, or a 3D-print slicer to inspect what's left after the job.
- An optional grayscale heightmap PNG (`--heightmap`). 255 = top of stock; 0 = bottom of stock.

Pair it with [`python/render_stl.py`](../python/render_stl.py) to get a committable 3-up (3/4 view + side + top) PNG preview of the simulated stock.

**The `.stl` is scratch — commit the PNGs, not the mesh.** The STL is a regeneratable Z-buffer artifact (rerun this script from the committed `.gcode` to recreate it byte-for-byte), and at a fine `--resolution` it balloons to tens of MB — clone/pull debt over LFS for a file nothing downstream consumes. So `simulated_stock.stl` is `.gitignore`d; the small `render_stl.py` PNG (+ optional heightmap PNG) are the artifacts that go in the repo. Inspect the mesh locally, then let it stay untracked.

## When to use

- After authoring a new CAM pipeline, to sanity-check the result before cutting metal.
- When a real cut goes wrong, to compare the planned remaining stock against what came off the machine.
- To verify rest-machining math — does the cutout pass actually skip the already-pocketed air column?

The simulator is exact for 3-axis milling (X, Y, Z + spindle rotation around Z). That covers every job in this repo.

## Usage

```bash
freecadcmd python/gcode_simulate.py -- \
    --stock 50x35x2 \
    --resolution 0.15 \
    --out  cam/<pipeline>/simulated_stock.stl \
    --heightmap cam/<pipeline>/simulated_stock_heightmap.png \
    cam/<pipeline>/01_pass.gcode \
    cam/<pipeline>/02_pass.gcode \
    cam/<pipeline>/03_pass.gcode
```

The script runs under `freecadcmd` for one reason only: FreeCAD's bundled Python already has `numpy` + `Pillow`. The script doesn't import anything FreeCAD-specific — same trick `render_stl.py` uses.

After the STL is written, render it to a 3-up PNG:

```bash
freecadcmd python/render_stl.py -- \
    cam/<pipeline>/simulated_stock.stl \
    cam/<pipeline>/simulated_stock.png \
    --size 1500x500 --color 0.7,0.7,0.72 --bg 0.96,0.96,0.96
```

## Flags

| Flag | Required | Meaning |
|---|---|---|
| `--stock WxHxD` | yes | Stock blank dimensions in mm. Origin is bottom-left of the top face; +Z is up. Same convention as the rest of the repo. |
| `--out PATH` | yes | Where to write the binary STL of the remaining stock. |
| `--resolution N` | no, default 0.15 mm | Grid cell size for the heightmap. Smaller = sharper but quadratically more triangles in the STL and slower sim. 0.1 mm fully resolves finishing-pass detail; 0.15 mm is a good default; 0.3 mm is fine for quick sanity checks. |
| `--heightmap PATH` | no | If set, also dump a grayscale heightmap PNG (255 = top of stock, 0 = bottom). |
| `--tool T:KIND:...` | no, repeatable | Override the built-in tool table for a given T-number. Two kinds: `T:flat:diameter_mm` or `T:vbit:included_angle_deg:tip_diameter_mm`. |

## Built-in tool table

Maps Carvera T-numbers → tool geometry. Override per job with `--tool`.

| T | Geometry | Source file under [`tools/`](../tools/) |
|---|---|---|
| T1 | flat endmill, 1.0 mm dia | `endmill_1mm_1F_3L_alu.fctb` |
| T5 | flat endmill, 2.0 mm dia | `endmill_2mm_1F_8L_alu.fctb` |
| T6 | V-bit, 30° included, 0.2 mm tip | `engrave_30deg_0.2tip_1F_plastic.fctb` |
| T7 | flat endmill, 6.0 mm dia | (one of the 6 mm `.fctb`s — pick yours with `--tool`) |

The table is intentionally tight — the script doesn't try to parse `.fctb` files because the T-number ↔ tool mapping lives in the operator's head (and the Carvera carousel), not in the gcode. When you bring in a new tool, either edit the defaults in [`python/gcode_simulate.py`](../python/gcode_simulate.py) or pass `--tool` on the command line.

## How the simulation works

1. Allocate a 2D float32 heightmap of shape `(ceil(W/res)+1, ceil(H/res)+1)`. Every cell starts at Z = 0 (top of stock).
2. Walk through every G-code line. Track `T<n> M6` tool changes and `G0/G1` motion modal state. Each line's X/Y/Z get folded into the current position (we assume `G90` absolute — every pipeline in the repo emits that in the preamble).
3. For each motion segment from `p0` to `p1`, walk along it in small steps so the tool's swept footprint has no gaps:
   - For a **flat endmill**: max step = half the tool radius.
   - For a **V-bit**: max step = `min(res, 0.25 mm)` (V-bit segments in our gcode are already ~`res` long, so this is rarely the binding constraint).
4. At each step's `(x, y, z)`, take a `min()` between the active tool's vertical-offset kernel (centred on the cell `(round(x/res), round(y/res))`) and the heightmap. For a flat endmill the kernel is zero inside the cutting disc; for a V-bit it grows as `(r - r_tip) / tan(half_angle)` outside the tip.
5. After all gcode is consumed, clamp the heightmap to `[-stock_d, 0]` and build the surface mesh: top quads (2 tris per cell) + 4 walls + bottom face.

The output STL is **closed and oriented**: outward-facing normals everywhere. Slicers (e.g. PrusaSlicer) treat it as a solid for cross-section preview.

## Sharp edges

- **2.5D only.** The script is a Z-buffer — every cell holds one Z value. If your job has overhangs or undercuts (4/5-axis), the simulation collapses them onto the top surface. Not a problem for the Carvera Air, which is 3-axis.
- **No collision detection.** This is a surface simulator, not a CAM verifier. It tells you what the workpiece looks like after the cuts described by the gcode — it does NOT check rapid moves for crashes or compare against the tool's published cut envelope. Use Carvera Controller's preview or the [`cam/<pipeline>/<name>.md`](../cam/) doc for that.
- **Tool numbers are hard-coded.** If you run a job with a tool that isn't in the built-in table, the script aborts with a hint about `--tool`. There's no auto-discovery from `tools/*.fctb` — see "Built-in tool table" above for the reasoning.
- **`G0` rapid moves stamp the same as `G1`.** This is intentional: if a `G0` move ever cuts into stock (because somebody scripted a rapid that bumps into the work), the simulator shows it. In practice every pipeline in this repo only does `G0` at safe-Z, so this is a no-op.
- **Resolution vs file size.** At 0.15 mm/cell on a 50×35 mm stock you get ~160 k triangles → ~8 MB STL; halving the resolution quadruples the triangle count (0.05 mm → ~70 MB). The STL is gitignored (see above), so this size is a *local disk + regen-time* cost, not repo/LFS debt — but the committed heightmap PNG still grows with resolution, so pick the coarsest resolution that's still useful for your sanity check.
- **Resolution must be ≤ the smallest feature you care to see.** The sim is a Z-buffer — if a milled feature is narrower than ~2 cells across, it aliases away into the surrounding average and looks "missing" in both the STL and the heightmap. Engraving with the T6 V-bit produces kerfs as narrow as its 0.2 mm tip diameter; fur strokes, engraved text serifs, and similar fine relief detail need `--resolution 0.05` (or finer) to resolve. The 0.15 mm default is right for coarser-grained pipelines (pocketing, perimeter cutouts, brass holes) where the smallest feature width is ≥ 0.6 mm, and where the 1/9× STL size is the better tradeoff.
- **V-bit "plunge crater" artefacts are real.** Where the finish gcode lifts the V-bit out of a relief peak and plunges back down to the background depth between two adjacent X-samples, the simulator stamps the V-bit's deep-Z swept disc (radius = `tip_dia/2 + max_depth * tan(angle/2)` — ~0.64 mm for a 30°/0.2 mm tip at -1.2 mm). That shows up in the heightmap as a circular crater around the plunge point. These are NOT sim noise — the physical V-bit really does cut a 1.27 mm-diameter cone at full depth, so the milled part will carry the same marks. If you want fewer/smaller craters, tighten the finishing pass's X step / Y stepover in the emitter so the V-bit walks the slopes more gradually rather than plunging (this was seen on an earlier V-bit relief; [`cam/relief_engrave/`](../cam/relief_engrave/relief-engrave.md) finishes with a ball nose instead) — but the bit's cone half-angle (15° from vertical) is fundamentally less steep than the relief's silhouette edge, so some crater is unavoidable.
- **V-bit kernel accuracy.** The cone kernel is `kernel[r] = (r - r_tip) / tan(half_angle)` for cells outside the tip disc, sampled at the simulator's grid resolution. Each cell of radial offset adds `res × tan(90° - half_angle)` of cone height — so a 30° V-bit at `--resolution 0.05` quantises the cone at `0.05 × tan(75°) = 0.187 mm` height steps. The tip disc itself is at most `ceil(r_tip / res)` cells across; at 0.05 mm/cell and a 0.2 mm tip that's 2 cells = `0.1 mm` radial. Sub-0.1 mm V-bit features (e.g. very fine engraving serifs) need `--resolution 0.025` or finer to resolve faithfully; for typical heightmap-relief work (fur, ornament, 0.15–0.30 mm strokes) `--resolution 0.05` is the right ceiling — going finer quadruples STL size for marginal accuracy gain.

## Worked example — thin-sheet edge trim (`cam/block_edge_trim/`)

```bash
freecadcmd python/gcode_simulate.py -- \
    --stock 232x132x4.04 \
    --resolution 0.15 \
    --tool 5:flat:2.0 \
    --out cam/block_edge_trim/simulated_stock.stl \
    --heightmap cam/block_edge_trim/simulated_stock_heightmap.png \
    cam/block_edge_trim/block_edge_trim.gcode

freecadcmd python/render_stl.py -- \
    cam/block_edge_trim/simulated_stock.stl \
    cam/block_edge_trim/simulated_stock.png \
    --size 1500x600 --color 0.7,0.7,0.72 --bg 0.96,0.96,0.96
```

(The simulator has `flat` and `vbit` tool kernels only — no ball nose — so
a relief job's ball finish pass isn't simulated; use the relief emitter's own
`*_preview_terrain.png` / `*_preview_coverage.png` for that.)

Outputs land next to the gcode so the simulated artefact stays with the pipeline it came from.
