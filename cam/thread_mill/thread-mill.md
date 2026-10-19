# Thread milling on the Carvera Air (Makera thread mills)

`cam/thread_mill/thread_holes.py` puts threaded holes into a probed box
workpiece: a **helical-bored pilot** (any endmill / ball nose smaller than
the thread's minor diameter) followed by a **thread-milling helix** with
one of Makera's multi-tooth thread mills. Frame, tilt and origin come from
`geometry.json` (`scripts/cnc_measure_box_stock.sh`), the same as the
relief tool, so Z is relative to the *local* top of the block.

```bash
python3 cam/thread_mill/thread_holes.py <job>/geometry.json --out <job>/holes.gcode \
    --hole M3 8 10 --hole M3 8 24 --hole M5 40 10 --hole M5 40 24
scripts/send_to_cnc.sh <job>/holes.gcode --ip <addr>      # one file, one Tn M6 per tool section
```

## The bits

Makera's thread mills (vendored library `Makera_Carvera_M<n>_Threadmill_*.fctb`,
translated into [`tools/threadmill_m3_2.42mm_3F_plastic.fctb`](../../tools/threadmill_m3_2.42mm_3F_plastic.fctb)
and [`tools/threadmill_m5_4mm_3F_plastic.fctb`](../../tools/threadmill_m5_4mm_3F_plastic.fctb))
are **multi-tooth**: 2 mm of 60° teeth at the tip, tooth pitch = thread
pitch, 3 flutes, cutter diameter a little under the thread's minor
(M3: Ø2.42, M5: Ø4.0), on a 1/8" shank behind a reduced neck (M3: 9 mm,
M5: 15 mm reach — that neck is the maximum thread depth). Because the
teeth already have the thread pitch, **one continuous helix at 1 pitch per
turn** from the bottom of the thread up until the lowest tooth clears the
surface cuts the full thread — every turn's teeth land in the groove the
previous turn's teeth started.

## Recipe (per size, in `RECIPES`)

| size | pilot | pilot tool | thread depth | orbit radius | passes |
|---|---|---|---|---|---|
| M3 × 0.5 | Ø2.6 × 7.5 | 2 mm ball nose (helix 0.25 mm/turn, 300 mm/min) | 6.0 | 0.29 | 0.20 → 0.29 |
| M5 × 0.8 | Ø4.3 × 12 | 3.175 O-flute (helix 0.5 mm/turn, 500 mm/min) | 10.5 | 0.50 | 0.35 → 0.50 |

- **Pilot** = minor diameter + a little (M3 minor 2.46 → 2.6, M5 4.13 →
  4.3): ~80–85 % thread engagement, plenty in epoxy/plastic and it keeps
  the thread mill from rubbing on the way in. A ball-nosed pilot has a
  ball-shaped bottom, so the pilot is 1 mm deeper than the thread tip
  (asserted).
- **Thread**: plunge down the pilot at the centre → radial lead-in →
  **G3-direction (CCW) helix rising** = climb milling for an internal
  right-hand thread → radial lead-out at the top → repeat at full radius.
  Two radial passes because a single full-depth pass on a 3-flute Ø2.4
  cutter chatters; the first pass takes ~70 % of the radial depth.
- **Feed is the tool-centre feed.** Makera's 400 mm/min is a tooth
  (periphery) speed; on an M3 the centre orbits at r = 0.29 mm, so the
  centre must move at ~(D_major − D_tool)/D_major × 400 ≈ 77 mm/min. The
  `.fctb`s carry 100 (M3) / 150 (M5) mm/min at the centre.
- Helices are linearised (36 segments/turn, `G1`) — no G2/G3, so the
  file goes through `gcode_simulate.py` and the firmware's plain path.

Safety asserts: every hole ≥ major/2 + 1 mm from the block edges, every
XY inside the block, every Z within [top − 13, safe], thread depth ≤ neck
reach, tool pitch == recipe pitch.

## Sharp edges / log

- 2026-08-17 first run: 2 × M3 + 2 × M5 into the epoxy remnant
  (`cam/relief_engrave/jobs/epoxy_remnant/thread_test.gcode`), tools in
  the order O-flute → ball → M3 mill → M5 mill, all via the file's own
  `Tn M6` prompts. **Result: all four threads good — M3 and M5 screws
  ran in cleanly** on the first attempt with the recipe above (Ø2.6 / Ø4.3
  pilots, two radial passes, 100 / 150 mm/min centre feed at 10 000 rpm).
  Whole job incl. three tool swaps ≈ 15 min.
- What made it work first time, for the next material: (1) the pilot a
  little over the minor diameter so the thread mill never rubs on the way
  down; (2) a ball-nosed pilot is fine as long as it's a ball radius
  deeper than the thread tip; (3) don't take Makera's 400 mm/min at the
  centre — convert it (`(D_major − D_tool)/D_major`) or the tooth feed is
  5× what the preset means; (4) the multi-tooth cutter needs exactly one
  continuous helix at the thread pitch — never step Z between turns by
  anything but the pitch, or the teeth cross-cut the thread they made;
  (5) `M3 S` first, then the plunge down the pilot at the centre — the
  radial lead-in/-out at the bottom/top means the tool never enters or
  leaves the wall mid-helix. Harder materials (aluminium, brass): the
  vendored `Makera_Carvera_M<n>_Threadmill_<Material>.fctb` presets have
  the rpm/feed — translate one into `tools/` and add a third radial pass.
