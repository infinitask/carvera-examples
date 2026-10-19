# Conventions for an AI agent working in this repo

This repo holds Carvera Air notes, CAM generators and sample G-code. An AI
agent (Claude Code) may read it, generate toolpaths and talk to the machine.
These rules are non-negotiable. Load the topic docs under [`docs/`](docs/) as
the task needs them.

## CNC safety — never send a command unless the operator told you to, in this turn

The CNC is a hazard to **people and hardware**. **Never send any
state-changing command to the machine on your own initiative.** That covers
motion (`play`, jogs, `G28`/home, `G0`/`G1`), unlock (`M999`),
spindle/accessory (`M3`/`M05`, `M851`/`M852`) and probing (`M495`, `M46x`).
Send one **only** when the operator has explicitly asked for *that* action in
the current exchange. Read-only status (`?`, `$#`) is fine at any time.

**When the operator HAS authorized a job run, use
[`scripts/send_to_cnc.sh`](scripts/send_to_cnc.sh), not a hand-rolled
`carvera-cli` sequence.**
`scripts/send_to_cnc.sh <gcode> --ip <addr> [--unlock] [--home] [--auto-z-probe DX DY]`
does unlock (`M999`), home (`G28`, which lifts Z first), upload **with a
verified landing**, optional auto-Z-probe, and `play`, in the right order.
Hand-rolled commands are for steps the script doesn't cover (probing cycles,
`M6` coordination, jogs the operator dictates). Never run the CNC from CI or
other unattended automation.

**Above all: NEVER autonomously unlock or "recover" an e-stopped, Alarm, or
HALT machine.** An e-stop is a deliberate safety stop. The operator may have
hit it because **the bit is in the work, or because their hands are inside
the machine.** `M999` re-enables motion, and the next move can drive the bit
or gantry into the work or the operator. When a job alarms or stops,
**report the state (a read-only `?` is OK) and stop.** Wait for the operator
to say the machine is safe and to ask for the specific recovery command.
Authorization for one unlock is not standing authorization for the next.

Further machine rules, each with its reasoning in
[`docs/carvera-air.md`](docs/carvera-air.md):

- Home with `G28`, never `$H` (rejected). Quote any `$…` command in bash.
- On **tall stock** (the spindle can't lift above the top), `G28` and the
  post-tool-change return can drag the bit through the part. Evacuate by hand
  with one move at a time, and check `?` between moves.
- Probe and cut as **two separate launches** on tall stock.
- `M0` does not pause a `play`-from-SD job. Don't use it as a safety gate.
- After any firmware flash, **the WCS offsets are zeroed**. Re-probe before
  any cut.
- With the 3D probe, `M6 T999990` powers the port. No LED means a wrong tool
  number, not a broken probe. See [`docs/carvera-3d-probe.md`](docs/carvera-3d-probe.md).

## CAM conventions

- **Tool legend at the top of every G-code file.** One comment line per tool
  the job loads, mapping the tool number to the bit name taken verbatim from
  the `.fctb` `"name"` field. Keep it ASCII. Swap `()` for `[]` inside it,
  because `(` ends a G-code comment early.

  ```gcode
  (=== Tool table - load these bits before running ===)
  (T1 = 6mm 2F 52mm Up-Cut Router Bit)
  ```

- **`tools/*.fctb` is the single source of truth** for diameters, feeds and
  speeds. Pipeline code reads them. Don't hardcode them, and don't point code
  at the vendored Makera library. Copy and translate the bit into `tools/`
  first ([`docs/makera-tool-library.md`](docs/makera-tool-library.md)).
- XYZ zero is the stock top, bottom-left corner seen from above.
- Every generator ships with a sibling `.md`. Update it in the same commit
  when you change the generator's contract or find a new sharp edge.
- A generator must refuse to write a file that fails its safety asserts
  (Z band, XY bounds, tool clearance). Don't weaken an assert to make a job
  emit.

More: [`docs/cam-conventions.md`](docs/cam-conventions.md).

## Chamfer / V bits

A 45° chamfer bit only cuts along a cone of height `(D − d_tip) / 2`. A deeper
tip makes the full-diameter shank plow a vertical wall. Check a vendor's
`CuttingEdgeHeight` against that formula
([`docs/chamfer-bits.md`](docs/chamfer-bits.md)).

## Scratch work

Put exploratory probes and throwaway files in `/tmp`, not the repo.
