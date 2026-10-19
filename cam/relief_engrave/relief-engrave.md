# Relief engraving from a heightmap (probe-compensated)

Carve a grayscale depth map into a box workpiece on the Carvera Air,
with the image laid onto the **measured** top face (3D-probe survey) and
every Z riding the **measured** tilt plane. First job: a pet portrait,
20 mm deep into a 100.8 × 35.2 × 37.5 epoxy test block (2026-08-16).

**This is a general-purpose relief tool — reuse it, don't write a new
emitter.** Any "carve this photo / logo / depth map into a block" job
starts here: `relief_gcode.py` is generic (any heightmap, any box
stock, measured or nominal, tools read from `tools/*.fctb`), and each
job is just a folder under [`jobs/`](jobs/) holding its heightmap,
prompt, survey, `geometry.json`, the emitted G-code and the previews.
It has no **cutout-with-tabs pass**; if you need one, add it here as a
third file.

## Pipeline

1. **Depth map** — an image model (we used Gemini) turns the
   photo into a heightmap: white = original top surface, black = full
   depth, black background. Ask for *geometry not lighting* (fur colour
   must not change brightness), then post-process deterministically with
   Pillow: clamp grooves the model drew pure-black (nostrils, creases —
   they'd become full-depth slots), light blur, fade cut-off edges. Keep
   the prompt in `<job>/heightmap_prompt.txt`.
2. **Measure the stock** — `M464` corner origin, then
   [`scripts/cnc_measure_box_stock.sh`](../../scripts/cnc_measure_box_stock.sh)
   and [`probe_survey_fit.py`](probe_survey_fit.py) → `geometry.json`
   (corners, tilt plane). See
   [`docs/carvera-probe-uneven-stock.md`](../../docs/carvera-probe-uneven-stock.md).
3. **Generate** —

   ```bash
   python3 cam/relief_engrave/relief_gcode.py <job>/heightmap.png <job>/geometry.json \
       --out-stem <job>/<name> --depth 20
   ```

   writes `<name>_01_rough_oflute.gcode`, `<name>_02_finish_ball.gcode`
   and two previews (`_preview_terrain.png`, `_preview_coverage.png` —
   white = ball, grey = flat tool only, black = untouched). **Commit the
   previews with the G-code.**
4. **Run** — `scripts/send_to_cnc.sh <file> --ip …` per file; each file
   opens with its own `Tn M6` so the firmware prompts the swap and
   measures the new tool.

## Reusing it on a new job

```bash
mkdir -p cam/relief_engrave/jobs/<job>          # heightmap.png + heightmap_prompt.txt in here
scripts/cnc_measure_box_stock.sh --ip <addr> --x <X> --y <Y> --origin <gx gy gz> \
    --out cam/relief_engrave/jobs/<job>/survey_$(date +%F).txt      # -> geometry.json
python3 cam/relief_engrave/relief_gcode.py jobs/<job>/heightmap.png jobs/<job>/geometry.json \
    --out-stem jobs/<job>/<name> --depth <mm> \
    --orient head-xplus --fit width --anchor plus [--margin 1] \
    --ball-collar-dia 6 --ball-collar-from 20                       # your ball bit's collar/nut
```

- **Placement**: `--orient head-xplus` (image top toward X+ — portrait
  along the X axis, un-mirrored for a viewer at the front with the head
  to their right) or `head-yplus` (top toward Y+, un-mirrored looking
  down from the front); `--fit width|height` stretches that image
  dimension across the block (or `--image-width-mm` for an explicit
  scale); `--anchor plus|minus|centre` slides the footprint along the
  free axis; `--margin` keeps off every edge. Whatever isn't under the
  footprint is **left untouched** — the offsets model it as "terrain =
  top plane", so no tool can dip into it (asserted).
- **No probe survey?** `--nominal-stock X Y` instead of `geometry.json`
  assumes a square block, flat top at Z0, X−/Y− top corner on the WCS
  origin (set it with `M464 … S2` or the corner probe). Fine for a
  small, freshly-faced blank; measure anything sawn/cast/tilted.
- **Other tools**: `--rough-tool-file` / `--ball-tool-file` point at any
  `tools/*.fctb` (diameter, flute length, shank, feeds, rpm, stepdown,
  stepover are read from it — override with the matching flags). The
  rough tool must reach `--depth`; the ball's shank and collar are
  clearance-checked per position, so a short ball just gets less of the
  deep floor (the coverage preview shows exactly where).
- **Ball collar / nut**: `--ball-collar-dia D --ball-collar-from H`
  models a Ø D obstruction starting H mm above the tip (donut collar or
  the collet nut for a short stick-out) with +0.5 mm radial / 1.0 mm
  vertical margin. Read the "REQUIRED stick-out" line the file prints
  and check `$# TL0` after the swap before playing.

First job (as run): `--depth 20 --orient head-xplus --fit width --anchor plus
--ball-collar-dia 6 --ball-collar-from 20`, geometry from a 3D-probe survey.

## Tools and why the split

- **Rough + walls + floor: 3.175 mm single-flute O-flute, 25 mm flute**
  ([`tools/endmill_3.175mm_1F_25L_oflute_plastic.fctb`](../../tools/endmill_3.175mm_1F_25L_oflute_plastic.fctb)).
  Reaches 20 mm; the big single gullet clears resin chips instead of
  re-welding them; shank = flute diameter so no shank-rub check needed.
  Z-level raster roughing (1 mm stepdown, 1.2 stepover, 0.3 stock), then
  a 0-stock raster finish of the whole region at 0.5 stepover (this is
  what finishes the −depth floor and every steep wall), then a
  cross-direction pass over the ball-excluded walls.
- **Finish: 2 mm 2-flute ball nose, 12 mm flute on a 3.175 shank**
  ([`tools/ballnose_2mm_2F_12L_plastic.fctb`](../../tools/ballnose_2mm_2F_12L_plastic.fctb)).
  0.12 mm stepover raster over everything it can reach — **the shank is
  wider than the cutter**, so the generator computes, per tool position,
  the highest terrain under the shank disc and excludes any position
  where that rises more than (flute − 0.5) above the tip. Those spots
  (floor next to a 20 mm wall) stay with the O-flute. The ball is also
  skipped where it can't reach lower than the flat tool did (flat floor).

Both feeds come from Makera's plastic presets in the vendored library
(`docs/makera-tool-library.md`), the O-flute pulled back to ~⅔.

## Offsets are real tool offsets

Flat tip = max terrain under the disc; ball tip = max over the disc of
`terrain + sqrt(r² − d²) − r`. Computed on a 0.05 mm grid with numpy
shifted-max (no scipy). This is what stops the cutter dipping into a
neighbouring rise and what keeps both tools out of the untouched strip.

## Safety asserts (generator refuses to write otherwise)

- every cutting Z inside `[local top − depth − 0.5, local top + 0.5]`
- every XY endpoint inside the block outline + one tool radius on the
  three open faces, and **never** over the untouched strip
- ball never placed where the shank test fails
- files start with `G0 Z<safe>` (play runs from wherever the tip is)

## Sharp edges

- **Spindle-nose clearance on a deep relief.** The collet nut sits one
  stick-out (= the measured TLO on 2.1.0c) above the tip. A short finish
  bit pushed deep into the collet puts the nut *below* the top face at
  the deep points — the 38 mm ball at TLO 27 clears the −20.6 floor by
  6 mm; at TLO 20 it would land the nut on the block. The generator
  prints the **required minimum TLO** per file (deepest tip + 3 mm) and
  stamps it in the header; **do the `M6` on its own, read `$# TL0`, and
  only play the file if it meets it** (a same-tool `M6` inside the file
  is then a no-op).

- The 2 mm ball's stepover of 0.12 gives ~50 min on a 35 × 46 mm bust;
  point decimation (`--tol 0.005`) keeps the file to ~4 MB.
- Snap near-black to the floor (`--floor-snap`) — a blurred background at
  1–2/255 otherwise reads as "not floor" and drags the ball over the
  whole floor.
- Tilted top ⇒ the flat tool's offset on the floor is `r·|slope|` above
  the ball's; the "ball only where it beats the flat tool" threshold has
  to sit above that (0.03 mm here).
