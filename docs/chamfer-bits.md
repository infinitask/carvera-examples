# Chamfer bits — the cone is shorter than you think

The one-parameter trap that ruins a chamfer op: a 45° chamfer bit only cuts
along its **cone**, and the cone is short. Above it sits the **unchamfered
shank at full diameter with no relief** — command the tip too deep and the
shank plows a vertical wall into the workpiece instead of cutting a chamfer.
We cut this exact defect into an epoxy test block on 2026-08-15: a clean 45° face ~1 mm wide with a vertical lip above it, because the
recipe asked for more chamfer than the bit's cone can deliver.

## The geometry

For a chamfer bit with tip flat Ø`d_tip`, max Ø`D`, and 45° flanks:

```
cone height          H = (D − d_tip) / 2
shank-safe rule      tip_depth_below_top ≤ H
achieved width       w = tip_depth + d_tip/2 − edge_offset
```

Our T7 (`tools/chamfer_45deg_3.175_makera_hardwood.fctb`): Ø3.175, tip
Ø0.1 → **H = 1.5375 mm**. The old recipe (width 1.5 + tip clearance 0.5 →
tip 2.0 below top) violated the rule by 0.46 mm: the cone topped out under
the surface and the shank cut a 1.04 mm-deep vertical wall around the rim.

The corollary: width and tip clearance **trade against each other** inside
the cone. A 1.5 mm chamfer from this bit leaves only 0.0375 mm of tip
clearance (cone top exactly at the surface on the final pass); a comfortable
0.5 mm clearance caps the width at ~1.04 mm. The near-zero clearance is
fine in practice — the tip's slow-speed centre only grazes an edge that a
previous pass already cut.

## Check the tool file — and don't trust it blindly

The vendor `.fctb` for this bit claimed `CuttingEdgeHeight: 3 mm` — which is
**geometrically impossible** (a 45° flank from Ø0.1 to Ø3.175 spans 1.5375 mm
of height; 3 mm of edge would need a Ø6+ bit). The pipeline's width check
"passed" against that fiction, which is why the defect shipped. Both fixed
2026-08-15:

- `tools/chamfer_45deg_3.175_makera_hardwood.fctb` now carries the derived
  1.5375 mm (correction noted in its `SourceNote`).
- The chamfer pipeline we used **derives** the cone height from the bit's own diameters, refuses a tool
  file whose `CuttingEdgeHeight` exceeds it, caps the tip depth at the cone
  (keeping the requested width, shrinking the clearance, warning), and
  aborts if the width alone is uncuttable.

**When you set up any new chamfer/V bit**: compute `(D − d_tip)/2` yourself
and sanity-check the vendor's cutting-edge number against it before trusting
a recipe to it. The angle matters too — for a non-45° flank the height is
`(D − d_tip) / (2·tan(flank°))`.

## Recovering a lip (the fix that worked)

If a shank-plowed lip has already been cut: one shallow perimeter pass whose
45° plane is **coplanar with the existing chamfer face**, with the cone top
at/just above the local surface, shaves the lip off and blends invisibly —
the pass removes only the lip triangle and grazes the rest. Worked example
(probe-compensated, single pass, edge offset 0.0375, tip 1.4875 below the
local top).
