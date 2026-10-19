# Carvera Air examples

Notes, scripts and sample CAM jobs from about six months of running a
[Makera Carvera Air](https://www.makera.com/) in a small workshop, much of it
driven by an AI coding agent (Claude Code). It's shared as-is for other Carvera
owners: everything here was used on a real machine, but **check every file
against your own machine, bits, fixtures and firmware before you cut with it.**

## What's here

| Path | What it is |
| --- | --- |
| [`CLAUDE.md`](CLAUDE.md) | Safety rules and conventions for an AI agent that drives the CNC. Useful as a checklist for humans too. |
| [`docs/carvera-air.md`](docs/carvera-air.md) | **Start here.** Connecting with `carvera-cli`, uploading and `play`ing jobs, recovering from alarms, the tool-change gotchas, the `M495` auto-probe, M-code reference, pre-flight checklist, quiet-cutting notes, troubleshooting. |
| [`docs/carvera-3d-probe.md`](docs/carvera-3d-probe.md) | The Makera 3D Wired Probe: power, wiring check, tip calibration, the `M460`–`M466` probing cycles. |
| [`docs/carvera-probe-uneven-stock.md`](docs/carvera-probe-uneven-stock.md) | Machining stock that isn't square: probe it as fixtured, fit the tilt and rotation, regenerate the toolpath. |
| [`docs/carvera-firmware-upgrades.md`](docs/carvera-firmware-upgrades.md) | Flashing stock → community firmware over WiFi, what survives a flash (WCS offsets don't), and what to re-verify. |
| [`docs/cam-conventions.md`](docs/cam-conventions.md) | Conventions the CAM scripts follow, plus hard-won FreeCAD Path lessons. |
| [`docs/makera-tool-library.md`](docs/makera-tool-library.md) | Using Makera's own FreeCAD tool library as the source of feeds and speeds. |
| [`docs/chamfer-bits.md`](docs/chamfer-bits.md) | Why a chamfer bit's cone is shorter than you think. |
| [`docs/gcode-simulate.md`](docs/gcode-simulate.md) | A 2.5D stock-removal simulator for checking G-code before cutting. |
| [`docs/freecad-wsl-install.md`](docs/freecad-wsl-install.md) | Headless `freecadcmd` on WSL / Linux. |
| [`scripts/send_to_cnc.sh`](scripts/send_to_cnc.sh) | Unlock → home → verified upload → optional auto-Z-probe → `play`, in a safe order. |
| [`scripts/cnc_measure_box_stock.sh`](scripts/cnc_measure_box_stock.sh) | Probe a box-shaped blank in one command and write `geometry.json` (corners + tilt plane). |
| [`cam/`](cam/README.md) | CAM generators and sample jobs, each with its own doc and emitted `.gcode`. |
| [`tools/`](tools/) | FreeCAD `.fctb` tool definitions with the feeds and speeds we settled on, plus `carvera_air.fctl`. |
| [`carvera/`](carvera/README.md) | `/sd/config.txt` snapshots (stock 1.0.2 and community 2.1.0c) and firmware links. |
| [`python/`](python/) | `gcode_simulate.py` and `render_stl.py` (STL → PNG preview). |

## Setup

```bash
git clone --recurse-submodules https://github.com/infinitask/carvera-examples
cd carvera-examples

# carvera-cli (see docs/carvera-air.md, "One-time setup", for the C-compiler gotcha)
python3 -m venv python/venv
python/venv/bin/python -m pip install -e vendor/carvera-cli
python/venv/bin/python -m pip install -r requirements.txt
```

Submodules (both upstream, not ours):

- [`vendor/carvera-cli`](https://github.com/hagmonk/carvera-cli) — command-line control and file transfer over WiFi/USB.
- [`vendor/CarveraProfiles`](https://github.com/MakeraInc/CarveraProfiles) — Makera's post-processors and FreeCAD tool library.

The FreeCAD-driven generators (`brass_holes`, `brass_holes_xneg`,
`drill_m5_hole_metal_v2`, `block_edge_trim`) run under `freecadcmd`. The
relief, part-off and thread-mill generators are plain Python + numpy + Pillow.

## Safety

A CNC can hurt people and wreck itself. In short (the full list is in
[`CLAUDE.md`](CLAUDE.md)):

- Never unlock (`M999`) or home a machine that someone else e-stopped. Their
  hands, or the bit, may be in the work.
- Keep a hand on the e-stop for the first move after every tool change and for
  every `G28`, especially with tall stock on the bed.
- Our recipes were verified on **our** machine and firmware. Air-cut or use
  scrap first.

## Status

This is a snapshot, not a maintained project. No support is offered.
