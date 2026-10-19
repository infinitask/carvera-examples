# Probing uneven boss workpieces — compensate in the toolpath, not the fixture

How we machine an accurate feature (e.g. a perimeter chamfer) on stock whose
faces can't be trusted — band-sawn blanks, castings, anything that "likely
isn't 100% true": rotated a fraction of a degree in the fixture, faces
drafted or non-parallel, top surface tilted. Instead of shimming and
re-clamping until the part is square, we **measure the part as it sits with
the 3D wired probe and regenerate the toolpath around the measured
geometry**. Commissioned 2026-08-15 on an epoxy test block:
two runs, both flanks — rotation −0.43°, a Y face 0.75 mm inboard of its
caliper width, and a 0.55 mm top tilt all cut cleanly on the first try.

Reference implementation (fits everything from the raw `[PRB:…]` readings):
[`scripts/cnc_measure_box_stock.sh`](../scripts/cnc_measure_box_stock.sh)
+ [`cam/relief_engrave/probe_survey_fit.py`](../cam/relief_engrave/probe_survey_fit.py)
(see *The batched way to run it* below).
Probe hardware/cycle reference: [`docs/carvera-3d-probe.md`](carvera-3d-probe.md).
General machine ops: [`docs/carvera-air.md`](carvera-air.md).

## Why the host regenerates the G-code each time

The firmware (community 2.1.0c, Smoothieware-based) has **no G-code
programming layer** — no variables, no math, no conditionals, no `G68`
coordinate rotation. A probe result can land in exactly one place: the WCS
origin (`M464 … S2`, `M466 … S1`). So:

- **Translation-only error** (part clamped a couple of mm off, same square
  stock): a static job that opens with `M464 … S2` is enough. No
  regeneration needed.
- **Rotation, draft, taper, tilt**: the machine cannot bend its own
  coordinates around them. The host reads the touches back over WiFi,
  fits the geometry, and emits a fresh toolpath — two seconds of Python.
  From the operator's seat it's still one probe→generate→play sequence.

## The survey (13 touches, ~3 min)

All cycles are `M46x` probing cycles; each result is read back host-side
from `$#`'s `[PRB:x,y,z:1]` (machine coordinates, trailing `:1` = contact).
A failed probe (no contact within travel) deliberately **halts** the
machine — loud failure, never a silent bad number.

1. **Fit the probe** — `M6 T999990` (probe port power is tool-gated; LED
   must light), auto-length on the setter, then sanity-check `$#` →
   `TL0 ≈ 34.33` and `M119` → `Probe: 0`.
2. **Sanity Z touch** near the centre (`M466 Z-15`) — confirms the top is
   where you think before any cycle dives near a face.
3. **Corner origin** — `M464 X10 Y10 H15 E5 S2` from ~10 mm above the
   X−/Y− corner writes the full XYZ work origin into G54. Every later
   number hangs off this, so it goes first.
4. **Top tilt plane** — 5 × `M466 Z-12` spread over the face (4 corners
   inset + centre); least-squares fit `z(x,y) = a + bx + cy`. Residuals
   ≤ ~2 µm mean the surface really is planar and the fit can be trusted;
   a big residual means warp — probe more stations.
5. **Side faces → top-edge lines** — 2 stations per face, `M466 X±8` /
   `M466 Y±8` from ~5 mm off the face at ~2–3 mm below the local top:
   - **Tip compensation**: the face is the probed centre ± tip radius
     (`zprobe.probe_tip_diameter / 2`, ours 0.975 after M460 calibration)
     in the probe direction.
   - **One depth per station** (`--depth`, default 2.5 mm below the local
     top). We used to take a second, deeper touch to measure a drafted
     face and extrapolate it up to the top edge; that was dropped
     2026-08-17 (the deep retract is where the probe crash-halted, and
     the drafts we've measured — ≤ 0.017 mm/mm on the epoxy — move the
     top edge by ~0.04 mm at 2.5 mm down). `probe_survey_fit.py` still
     fits a draft line if a log happens to contain two depths.
   - **Already-chamfered / rounded edges must be probed below the
     chamfer** — a touch on the flank reads garbage. That's just a deeper
     `--depth`; the face position is then taken at that depth.
6. **Corners** = intersections of the four fitted edge lines. Cross-check:
   corner (0,0) should land within a few tens of µm of the `M464` origin.

### Work-coordinate conversion (host-side)

`[PRB:…]` is machine coordinates. With the G54 just set and the probe's
TL0 (both read from `$#`):

```
work_X = PRB_X − G54_X
work_Y = PRB_Y − G54_Y
work_Z = PRB_Z − (G54_Z + TL0)
```

## The one-command way to run it — `scripts/cnc_measure_box_stock.sh` (standard since 2026-08-16)

Steps 1–6 don't need a human between touches except for the physical
probe swap, so **don't drive them one CLI call at a time** — that was
the 2026-08-15 way (18 separate turns, each waiting on a probe reply).
One command now does the whole thing, given the block's nominal size and
an *approximate* origin (paste the `[G54:…]` triple from a previous run
or `$#` — a few mm off is fine):

```bash
scripts/cnc_measure_box_stock.sh --ip 192.168.1.50 --x 100.8 --y 35.2 \
    --origin -289.2 -201.7 -96.8 \
    --out cam/relief_engrave/jobs/<job>/survey_$(date +%F).txt
# -> survey log + geometry.json beside it, probe left fitted at Z10
```

What it does, in order:

1. **Refuses unless `?` says Idle.** An Alarm/HALT is the operator's to
   clear — the script never sends `M999`/`G28`.
2. **`M6 T999990`** and waits: fit the probe, **plug it in** (LED lit —
   an unplugged probe alarms the auto tool-measure with `H:3`), confirm
   on the machine; the firmware measures TLO and the script polls `?`
   until `T:999990` carries a fresh TLO. `--no-tool-change` if it's
   already fitted and measured.
3. **Origin**: parks G54 on the approximate corner (`G10 L2 P1`), goes
   to safe height, one sanity `M466 Z-15` over the centre, then finds
   the *real* X− and Y− faces with long `M466 X12`/`Y12` touches from
   8 mm outside (this is what makes a few-mm-wrong `--origin` harmless),
   hovers 2.7 mm inside the true corner and runs `M464 X10 Y10 H15 E5 S2`
   → G54 = the corner. `--no-origin` keeps the current G54.
4. **Z survey** — 5 × `M466 Z-12` (4 inset corners + centre), then
   **fits the tilt plane from those five touches itself**, so every side
   touch that follows is at the requested depth below the *local* top of
   a tilted block.
5. **Side faces** — 2 stations per face, one touch each at `--depth`
   (default 2.5 mm below the *local* top; set it deeper than any edge
   chamfer — a touch on the flank reads garbage), approaching from
   `--off` (6 mm) outside.
6. **Fit** — runs `probe_survey_fit.py` with the G54/TL0 it read from
   `$#`: plane (+ residual, so a warped top shows), four top-edge lines,
   true corners, extents → `geometry.json` for the toolpath generator.

Why it's safe to batch: hover 10 mm above the top between touches;
every side approach starts outside the block; a wrong nominal size just
means a probe-fail **halt** (loud), not a crash; every reply is checked
and the first `error`/`Alarm` stops the script with the state printed.
The log line format (`<face> <station> <probe cmd> [PRB:…]`) is the fit
script's input contract, and **`[PRB:]` is raw** — the cycle's printed
"Final Position" is already tip-compensated (confirmed: M464's G54 =
PRB ± 0.975 exactly). Don't mix the two.

**Power-up re-zero (`--origin-only`).** The machine forgets nothing on
power-off except that you should re-verify it: run the same command
with `--origin-only` (no `--out` needed) and it does steps 1–3 only —
probe fit, sanity Z, X−/Y− faces, `M464` corner — and exits with the
new G54 printed. 2026-08-17: after an overnight power cycle the corner
came back within 0.04 mm of the previous day's, and the ball-finish
pass registered onto the roughing.

Re-fixturing check, for free: run it again after any re-clamp and diff
the corners — on 2026-08-16 the epoxy block came back within 0.25 mm of
the day before, and the survey said so in five minutes.

## The compensated toolpath

For the perimeter chamfer (the pattern generalises):

- **XY**: offset the tool centre a constant `EDGE_OFF` *outside* each
  measured edge line; join edges with G3 arcs of radius `EDGE_OFF` about
  each true corner, so the cutter wraps the corner at constant engagement.
  The loop is the measured quad — rotation and non-parallel faces fall out
  by construction.
- **Z**: every move's Z word is `plane(x, y) + depth`, so cut depth is
  constant relative to the **local** top. The plane is linear, so a
  straight G1 with an interpolated endpoint Z follows it exactly; small
  corner arcs deviate by single µm.
- **Safety asserts in the generator, always**: every cutting Z inside a
  sane band; every XY endpoint outside the measured stock polygon; arc
  radii consistent. The generator refuses to write a file that fails.
- Entry/exit and plunges happen in free air off an open face, and the
  file starts with a `G0 Z<safe>` lift — `play` starts from wherever the
  tip currently is (raw `play` has no path-origin move — see
  `docs/carvera-air.md`).

Send with `scripts/send_to_cnc.sh <file> --ip <addr>` (add `--unlock
--home` if recovering). The job's own `T7 M6` pauses for the probe→cutter
swap; the firmware measures the new tool and continues.

## Sharp edges hit while commissioning

- **The work envelope may not include your "clearance" point.** With the
  part probed at the front of the table the work origin sat 0.7 mm from
  the machine's Y− soft limit — a work `Y-20` clearance move tripped a
  soft-endstop Alarm, and the halted status showed the *commanded* MPos
  (never reached), so position needed a re-home. Pick clearance
  positions inside the envelope (Y+ side here).
- **`M461`/`M462` X/Y params are FULL width/diameter**, not radius —
  the bundled firmware readme is wrong; passing half-widths makes the
  dives land on top of the part.
- **`M465` (axis angle)** probes from the current position with no dive —
  position it at depth yourself and don't pass `H`.
- Chaining probe moves through the CLI: each `M46x` gets a generous
  `--timeout`; the fast `ok` is command queuing, not completion.
