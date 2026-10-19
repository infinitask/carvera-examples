# Makera 3D Wired Probe — setup, cycles, calibration

The wired touch probe for the Carvera Air: how to power it, verify it,
calibrate it, and drive the firmware probing cycles. Commissioned
2026-08-15 on community firmware 2.1.0c — everything below
was verified live on our machine. Methodology for using it on
not-quite-true stock: [`docs/carvera-probe-uneven-stock.md`](carvera-probe-uneven-stock.md).
General machine ops: [`docs/carvera-air.md`](carvera-air.md); firmware
background: [`docs/carvera-firmware-upgrades.md`](carvera-firmware-upgrades.md).

## Powering it — the port is tool-number-gated

The probe's port only gets power when the **active tool number** says a
probe is loaded: `T0` (with `zprobe.tool_zero_is_3axis`), `T9999`, or
`T≥999990`. Fit the probe with a real tool change — `M6 T999990` — or
register it without motion via `M493.2 T999990`. **The LED on the probe
body lights when the port is powered**; no LED = wrong active tool number,
not a broken probe (we lost time to exactly this).

With the probe active, **crash detection** is armed: a stylus deflection
during a *non-probing* move halts the machine. That's a feature — but it
means you park and traverse deliberately.

## Verify before trusting it

- `M119` → `Probe: 0` idle, `1` with the stylus deflected by hand — proves
  the wiring end-to-end, zero risk.
- `$#` → `TL0` should be the probe's stickout after its length measure
  (ours ≈ **34.33**; measured on the setter by `M6`'s auto-cal or `M491`).
- A probe cycle that finds nothing within its travel **deliberately raises
  a machine halt** (`ALARM` / probe-fail): loud failure instead of a
  silent bad number. Unlock (operator's call), fix the setup, rerun.

## Tip calibration — `M460`

The *effective* tip diameter (physical ball minus trigger pre-travel)
is what the corner/boss/bore cycles compensate with, stored in
`zprobe.probe_tip_diameter`. Calibrate against a feature of known size —
we used the epoxy block's caliper-verified 100.82 mm width with the boss
variant (`M460.2`), which wrote **1.950** (2 mm ball, ~25 µm pre-travel
per side). After calibrating, cross-check a probed width against the
caliper — ours agreed to 4 µm. The value is config, so it survives
reboots; it's snapshotted in `carvera/config_community_2.1.0c.txt`.

For raw single-axis touches (`M466`) **you** apply the compensation:
face position = probed centre ± `tip_diameter/2` (0.975) in the probe
direction.

## Cycle reference (community 2.1.0c)

All cycles start from the current position, print their result, and leave
it in `$#`'s `[PRB:x,y,z:1]` (machine coords; trailing `:1` = contact).
Common params: `H` = dive depth below the start, `E` = approach/retract
distance, `S1`/`S2` = save the result into the active WCS.

| Cycle | What | Notes |
| --- | --- | --- |
| `M460` / `M460.2` | tip calibration (bore / boss) | against a known-size feature |
| `M461` | bore centre | **X/Y = FULL diameter** |
| `M462` | boss centre + width | **X/Y = FULL width, not radius** — the bundled readme says radius and is WRONG; half-values make the dives land on top of the part. Start close above the part (the dive is `H` from the start height — from 30 mm up with `H15` it probes air) |
| `M463` / `M464` | inside / outside corner | `M464 X10 Y10 H15 E5 S2` from ~10 mm above an X−/Y− corner probes both faces + top and **writes the full XYZ origin into the WCS** (positive X/Y for an X−/Y− corner) |
| `M465` | edge angle | probes from the **current position immediately** — no dive; position at depth yourself, do **not** pass `H`. `X` = separation between the two touch points, `E` = approach |
| `M466` | single-axis double-tap | `M466 Z-12`, `M466 Y8` / `Y-8` / `X8` / `X-8` — sign = direction, value = max travel. `S1` saves as WCS zero for that axis |

Drive them over WiFi with `carvera-cli … --timeout 90 command "M46x …"` —
the near-instant `ok` is **queuing**, not completion; the timeout must
cover the actual probing motion.

## `Alarm H:16` — "3D Probe crash detected" — and why `M999` may not clear it

`H:16` = `CRASH_DETECTED` (community firmware `Kernel.h`): with the probe
active, the tip triggered during a **non-probing** move. The detector
(`ZProbe::read_probe`) is armed whenever an axis is moving and `probing`
is false, and only re-fires after the tip has *released* since the last
trigger. Two things follow:

- **It can fire inside an `M46x` cycle.** Each cycle is fast probe →
  contact → **1.5 mm retract with detection armed** → slow re-probe. If the
  stylus registers a release-then-retrigger during that retract, you get
  `H:16` with MPos = the retract endpoint. Seen 2026-08-17 on the second
  (6.5 mm-deep) touch of a milled slot wall on the epoxy remnant — the
  block size and origin were fine (the 2.5 mm touch on the same face had
  just read 48.69 vs the 48.6 slot wall) and the wall was smooth on
  inspection, so treat it as a stylus/input glitch: cause not established.
- **`M999` is the unlock, but only if the dispatcher is listening.**
  While halted the firmware drops everything except `M999`
  (`GcodeDispatch.cpp`) — that's what clears an `H:1` stop button or a
  crash during a plain `G0`. But when the crash happens *inside* an M46x
  cycle the handler is stuck in `wait_for_idle()` and never returns to the
  dispatcher, so `M999`/`$X` are never read (the CLI times out; only the
  out-of-band `?` answers). Unplugging the probe doesn't help. **Recovery
  is a power cycle**, then home; the WCS survives (it's in EEPROM), the
  probe's TLO does not — re-run the tool change.

Sequence for an agent: read `?` (`H:16` → this section), tell the
operator, try `M999` once with a short timeout; if it times out, ask for a
power cycle rather than retrying. Never jog toward the work to "free" it.

## Planning probe moves

- **Clearance**: know your fixture heights (ours reach 25 mm) and stay
  above them when traversing; know where the **soft limits** are — a work
  origin probed at the table edge can leave "obvious" clearance moves
  outside the envelope (a work `Y-20` move tripped a soft-endstop halt
  on our first session).
- **Already-chamfered or broken edges**: probe the face **below** the
  chamfer, at two depths, and extrapolate to the virtual top edge (see
  the uneven-stock doc). Blank edges probe fine at a normal shallow depth.
- Read results back with `$#` and convert to work coordinates:
  `work = PRB − G54` (X/Y), `work_Z = PRB_Z − (G54_Z + TL0)`.
