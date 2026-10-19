# Carvera Air firmware upgrades — procedure & learnings

How to flash the Carvera Air, what a flash
does and doesn't touch, and the re-verification work every firmware change
drags behind it. Written from the 2026-08-15 upgrade **stock 1.0.2 →
community 2.1.0c**; update it on every future flash.

Config backups and firmware links live in [`carvera/`](../carvera/README.md).
Machine operation generally: [`docs/carvera-air.md`](carvera-air.md).

## The flash procedure (over WiFi — no SD pull)

1. **Back up first** — pull `/sd/FIRMWARE.CUR` and `/sd/config.txt` with
   `carvera-cli download` and keep them somewhere safe with MD5s noted
   (see [`carvera/README.md`](../carvera/README.md)). Rollback is then always one upload away.
2. Machine **on, idle, not in Alarm** (Alarm blocks file transfers —
   XMODEM `getc timeout`; `M999` first with the e-stop released).
3. From the repo root:
   ```bash
   python/venv/bin/carvera-cli --wifi --device 192.168.1.50 \
     upload-firmware carvera/<firmware>.bin --reset
   ```
   The CLI validates the image (ARM vector table + Thumb signature),
   uploads to `/sd/firmware.bin` with MD5 verification, and resets; the
   bootloader flashes it on the way up. **Don't kill power mid-reset.**
4. The machine drops off the network for ~20–30 s. Poll, then confirm:
   ```bash
   python/venv/bin/carvera-cli --wifi --device 192.168.1.50 command version
   ```
   Community builds carry a `c` suffix (`2.1.0c`); stock reports plain
   (`1.0.2`). Verified 2026-08-15: total wall time ≈ 40 s.

Rollback is the same command with the stock backup binary.

## What survives a flash, what doesn't (measured 2026-08-15)

| State | Survived? | Evidence |
| --- | --- | --- |
| `/sd/config.txt` | **Yes — byte-identical.** A flash swaps the firmware only; config is a separate file the new firmware reads as-is. New-firmware config keys simply take their in-firmware defaults until you `config-set` them. | post-flash download diffed clean against `carvera/config_stock_1.0.2.txt` |
| `TL0` (active tool length) | **Yes** | `$#` still showed T7's `-15.2672` |
| Active tool number | **Yes** | `?` still `T:7,…` |
| **WCS offsets (G54–G59.3)** | **NO — all wiped to zero.** | `$#` all zeros; G54 had held the brick's probed work origin |
| Homed state | Yes (was homed before and after) — but treat as unverified after any flash; check `?` before trusting coordinates | |

**⇒ After every flash: re-establish the work origin (re-probe) before any
cut**, and re-run `$#` to see what else moved. Never assume a WCS survived.

## Re-verification — a flash invalidates every machine-behaviour claim

Our recipes encode *observed firmware behaviour* (tool-change return moves,
`M495` semantics, TL0 gate values, G28 dash paths). A firmware change makes
all of it **unverified until re-tested — air cuts / scrap only** until the
checklist is green. The 1.0.2→2.1.0c findings are in [`carvera-air.md`](carvera-air.md) §"Community firmware
2.1.0c"; reuse its shape for the next upgrade:

- TL0 gate values (any semantics change moves every documented number)
- Tool-change motion (`M6` end-of-change move; `M493.2`+`M491` recipe)
- `M495` probe + return path; end-of-job `G28` dash
- Rapid speeds (`G0`) and anything feed-rate-adjacent
- Doc sweep: `carvera/README.md`, `docs/carvera-air.md` and every job doc
  — update the claims **in the same commit**

## Learnings log

### 2026-08-15 — stock 1.0.2 → community 2.1.0c

- **Latest ≠ latest tag**: releases include pre-release RCs (`v2.2.0c-RC1`
  was newer); flash the latest **stable** (`Latest` badge on GitHub).
- The upload+flash+reboot cycle is fast (~40 s) and undramatic; the CLI's
  firmware validation and MD5 check make the WiFi path safe — no SD pull
  needed.
- **WCS wipe** was the only state loss (table above). TL0 and config
  survived.
- The community firmware's behaviour is fully sourced on GitHub
  (`Carvera-Community/Carvera_Community_Firmware`) — read the release
  notes **and** the dispatch code (`src/modules/tools/zprobe/ZProbe.cpp`,
  `src/modules/tools/atc/ATCHandler.cpp`) before trusting a new M-code;
  the release notes are accurate but terse.
- New introspection commands worth running right after a flash:
  `M493.4` (tool-setter position + current/reference tool offsets),
  `M889` (tool-slot table), `config-get sd <key>`, `M119` (probe pin
  live state). All read-only.
- The stock Makera Controller app pairs with stock firmware; after moving
  to community firmware, prefer the community Controller if the UI is
  needed — and note our CLI/scripted path doesn't care either way.
- **Same-day live re-verification results**: TLO stickout semantics
  confirmed (`TL0 = contact MZ − reference_tool_mz`; T1 router = 69.14,
  T7 chamfer = 27.07); the stock post-`M6` dash-to-work-origin is **gone**
  (returns to the request XY — watched it live, never approached the
  part); `G28` unchanged; `M493.2`/`M491` both behave. A firmware upgrade
  can *remove* hazards too — re-verification is how you find out you can
  retire a workaround, not just that recipes still work.
- The `?` status `T:` field changed format (4 fields) and can show a
  garbage tool number mid-ATC — gate on `$#`, and expect status-line
  parsing in our tooling to need a look after any major firmware bump.
