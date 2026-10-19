# Carvera Air firmware — config snapshots & firmware links

Config for the Carvera Air. Pulled 2026-08-03 ahead of the flash to the community firmware
(needed for the Makera 3D Wired Probe, plus the TLO-management and
flex-compensation features).

> **Current state: the machine runs `2.1.0c`** — flashed over WiFi
> 2026-08-15, `version` verified. Post-flash checks: `TL0`
> and `/sd/config.txt` survived (config byte-identical to
> `config_stock_1.0.2.txt`, so no separate 2.1.0c snapshot is committed);
> **all WCS offsets (G54–G59.3) were wiped** — re-probe the work origin
> before any cut. Procedure + full learnings:
> [`docs/carvera-firmware-upgrades.md`](../docs/carvera-firmware-upgrades.md);
> behaviour changes: [`docs/carvera-air.md`](../docs/carvera-air.md).

| File | What it is |
| --- | --- |
| `config_stock_1.0.2.txt` | `/sd/config.txt` snapshot taken under stock firmware 1.0.2 — the machine's config as tuned. |
| `config_community_2.1.0c.txt` | `/sd/config.txt` snapshot after the community firmware flash. |

Firmware binaries are not redistributed here. Get them from the source:

- **Stock:** Makera's official releases (the Makera Controller's update screen, or Makera support). Back up your own `/sd/FIRMWARE.CUR` with `carvera-cli download` before any flash, and note its MD5 — that's your exact rollback image. Our stock 1.0.2 backup had MD5 `e82071426c259dc886fc1265683e7a33`.
- **Community:** [Carvera Community Firmware v2.1.0c](https://github.com/Carvera-Community/Carvera_Community_Firmware/releases/tag/v2.1.0c) (2026-04-08 stable). One unified binary for C1 + Air.

## Flashing / rolling back

Either direction is the same procedure — it's a file swap, not a one-way
door (Makera state it doesn't void the warranty):

- **Controller:** USB connection → Makera Controller → top-right menu →
  software update → Firmware tab → upload the `.bin`. The stock Makera
  Controller only accepts a file named exactly `firmware.bin`, so rename a
  copy first. Machine resets itself.
- **SD card fallback:** pull the card, delete `FIRMWARE.CUR`, drop the new
  binary in the root as `firmware.bin`, power up.

Confirm with the `version` command afterwards: stock reports plain
(`version = 1.0.2`), community firmware carries a `c` suffix (`2.1.0c`).

**Transfer gotcha:** the machine refuses file transfers while in **Alarm**
(e.g. after an e-stop) — `carvera-cli download` fails with XMODEM
`getc timeout in start sequence` and can leave the link wedged for the next
command or two. Unlock first (`M999`, with the physical e-stop released),
confirm `Idle` with `?`, then transfer.

## ⚠️ Before trusting a cut after a firmware change

Every machine recipe we wrote before the flash
was validated on stock **1.0.2** — the post-tool-change origin move, the
end-of-job G28 dash, `M493.2`/`M491` semantics, `M495 P1` return behaviour.
Re-verify each with air cuts on scrap before the next real cut, and note
that **v2.1.0c redefines TLO to mean cutter stickout from the spindle**, so
the documented "`TL0 ≈ 26.9`" gate value is re-baselined to **T1 ≈ 69.14 /
T7 ≈ 27.07** (measured 2026-08-15). Re-verification status is in
[`docs/carvera-air.md`](../docs/carvera-air.md) §"Community firmware
2.1.0c".
