# CAM generators and sample jobs

Each folder is a self-contained generator with its own doc. The committed
`.gcode` files are what the generator emitted for the job we ran. Treat them
as examples: regenerate for your own stock, and read the prep block at the top
of each file before you run it.

| Folder | What it does | Engine |
| --- | --- | --- |
| [`brass_holes/`](brass_holes/brass-holes.md) | Helical-mill N through-holes in a thin rectangular plate. | FreeCAD Path |
| [`brass_holes_xneg/`](brass_holes_xneg/brass-holes-xneg.md) | Single-side variant for a plate too big to reach all holes in one setup: drill one side, flip 180°, run again. | FreeCAD Path |
| [`drill_m5_hole_metal_v2/`](drill_m5_hole_metal_v2/drill-m5-hole-metal-v2.md) | One centred hole in a metal disc fixtured at anchor 1, inside-out helix. | FreeCAD Path |
| [`block_edge_trim/`](block_edge_trim/block-edge-trim.md) | A straight parting cut through a thin sheet to trim one edge to size. | FreeCAD Path |
| [`relief_engrave/`](relief_engrave/relief-engrave.md) | Any grayscale depth map → probe-compensated rough + ball-finish relief on box stock. Also `part_off_gcode.py` (face off from the top) and `probe_survey_fit.py` (fits `geometry.json` from a probe survey). | numpy |
| [`thread_mill/`](thread_mill/thread-mill.md) | Threaded holes: helical pilot bore, then a Makera multi-tooth thread mill. M3 / M5 proven in epoxy. | numpy |

`relief_engrave/jobs/epoxy_remnant/` is a worked example of the probe flow: the
raw probe survey, the fitted `geometry.json`, and the thread-test G-code
generated from it.

Conventions shared by every generator: [`../docs/cam-conventions.md`](../docs/cam-conventions.md).
