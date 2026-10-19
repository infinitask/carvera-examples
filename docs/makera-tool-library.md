# The vendored Makera tool library

Makera ship a full FreeCAD tool-bit library for the Carvera / Carvera Air
bits — every bit in their tool packs, with per-material feeds and speeds.
It's already in this repo as part of the `CarveraProfiles` submodule:

```
vendor/CarveraProfiles/CAM_Post_Processors/Freecad/Tools/
├── bit/        615 × .fctb  (one per bit × material)
├── library/      7 × .fctl  (Ball Endmills, Corn Bits, Drill Bits,
│                             Engraving Bits, O Flute Bits, Thread Mills,
│                             Other Tools)
└── Shape/       13 × .fcstd (endmill, ballend, v-bit, drill, thread-mill, …)
```

**Use it as the starting point for feeds/speeds on any Makera bit** instead
of guessing or deriving numbers from scratch. It is *not* a drop-in
replacement for [`tools/`](../tools/) — see [Adopting a bit](#adopting-a-bit-into-tools)
for the copy-and-translate step, which is mandatory.

Upstream: <https://github.com/MakeraInc/CarveraProfiles> (`CAM_Post_Processors/Freecad/Tools/`).
The same directory also holds `1.1PostProcessor/makera_post.py` — the post
processor our pipelines already call — and `FusionCSVToFreecadTool`, the
converter Makera used to generate these `.fctb`s from their Fusion tool
tables (which explains the schema quirks below).

## Getting it

`git submodule update --init --recursive` checks it out.

## What's in it

615 `.fctb` files, but only **124 distinct physical bits** — each bit is
duplicated once per material with different feeds and speeds. Material
suffixes on the filename / `name`:

| Suffix | Bits |
| --- | --- |
| `_Hardwood`, `_Plastic`, `_Softwood` | 103 each |
| `_Aluminum`, `_Brass`, `_Copper` | 65 each |
| `_PCB` | 60 |
| `_Carbon_Fiber` | 51 |

By `ToolType`: 240 drills, 148 flat end mills, 104 chamfer mills (this
bucket includes the V-bit engravers), 75 ball end mills, 48 thread mills.

Finding the right one is a `ls | grep`:

```bash
ls vendor/CarveraProfiles/CAM_Post_Processors/Freecad/Tools/bit \
  | grep -i 'ball.*4.*22mm.*Hardwood'
```

## The schema is NOT ours — translate before use

A Makera `.fctb` and one of our `tools/*.fctb` are both "FreeCAD tool
bits", but the `attribute` block uses **different keys, different units,
and different semantics**. Our pipelines read the attribute block directly
(`brass_holes.py` → `_ATTR["SpindleSpeed"]`, `_mm_per_min(_ATTR["HorizFeed"])`,
…), so pointing `TOOL_BIT_FILE` at a Makera bit **raises `KeyError` on the
first feed lookup**. Even after renaming the keys, the values are bare
numbers with no unit suffix and `Units.Quantity("500").getValueAs("mm/min")`
throws `ValueError: Unit mismatch` (verified under FreeCAD 1.1.1).

| Makera | Ours | Notes |
| --- | --- | --- |
| `spindleSpeed`: `"12000"` | `SpindleSpeed`: `"12000"` | RPM, bare number both sides |
| `cutting_feedrate`: `"500"` | `HorizFeed`: `"500 mm/min"` | **append the unit** |
| `plunge_feedrate`: `"300"` | `VertFeed`: `"300 mm/min"` | **append the unit** |
| `ramp_feedrate`: `"333.333"` | `RampFeed`: `"333 mm/min"` | **append the unit** |
| `stepdown`: `"0.2"` | `Stepdown`: `"0.2 mm"` | mm both sides; **append the unit** |
| `stepover`: `"0.453571"` | `Stepover`: `"2.5 mm"` | **different semantics** — see below |
| `rampAngle`: `"1.8"` | `RampAngle`: `"2 deg"` | degrees; **append the unit** |
| `lead_in_feedrate` | — | no equivalent; we don't use it |
| — | `SourceNote` | ours records *why* the numbers are what they are |

**`stepover` is a fraction of tool diameter, not a distance.** A 6 mm
two-flute engraving bit carries `stepover: "0.03"` — 0.03 mm would be
absurd, 3% of 6 mm (0.18 mm) is sane for an engraver. Multiply by the
`Diameter` parameter to get our mm value. Note also that **only 226 of the
615 bits carry `stepover` at all**, and the 240 drills have no
`cutting_feedrate` / `stepdown` / `ramp_feedrate` either (a drill only
plunges) — handle the missing keys rather than assuming the full set.

`Chipload` in the `parameter` block is in **µm** (`"20.83 µm"`), where ours
is in mm (`"0.1000 mm"`). `Units.Quantity` parses both, so this one is safe
to pass through — just don't compare the raw strings.

## Adopting a bit into `tools/`

`tools/` stays the single source of truth for anything a pipeline runs
(that rule is in [`cam-conventions.md`](cam-conventions.md)). Don't reference a
`vendor/...` bit path from pipeline code — the submodule is upstream's to
change, and our numbers get tuned against real cuts.

1. Pick the Makera bit for the **bit + the material you're cutting**.
2. Copy it to `tools/<our-naming>.fctb` — our filenames are descriptive
   and lowercase (`endmill_6mm_2F_52L_hardwood.fctb`), not Makera's.
3. Rewrite the `attribute` block into our key/unit scheme (table above),
   converting `stepover` from fraction → mm.
4. Set `name` to a human-readable bit name — it lands verbatim in the
   G-code **tool legend**, so keep it ASCII and paren-free. Makera's names
   are mostly fine but **8 chamfer bits carry a `″` (U+2033) inch mark**
   (`Makera_Chamfering Bit - 1/8″ Shank_Aluminum`) which the Carvera can
   choke on in a comment — replace it with `in` or `"`-free wording. (No
   Makera name contains parentheses, so the `()` → `[]` swap in
   `drill_m5_hole_metal_v2.py` → `_safe_comment()` isn't triggered by
   them.)
5. Add a `SourceNote` recording that the numbers came from the Makera
   library and which file, so a later revision knows what it's revising.
6. Register it in [`tools/carvera_air.fctl`](../tools/carvera_air.fctl) at
   the **ATC slot the bit physically sits in** — the `nr` is the `T<n>` the
   G-code emits, so a wrong number loads the wrong bit.

Treat the Makera numbers as a **starting point, not gospel**: they're
generic for the bit/material pair and don't know our stock, fixturing or
the Carvera Air's rigidity. `tools/endmill_6mm_2F_52L_hardwood.fctb` is
eight revisions deep in chatter tuning for exactly that reason.

## Sharp edges

- **`shape` filenames are mis-cased.** 223 bits reference `Endmill.fcstd` /
  `Ballend.fcstd`, but every shape file — both in the vendored `Shape/`
  directory and in FreeCAD's own
  (`/opt/freecad/usr/Mod/CAM/Tools/Shape/endmill.fcstd`) — is lowercase.
  On a case-sensitive filesystem that reference doesn't resolve. It
  doesn't affect our pipelines (they `json.load` the `.fctb` and edit the
  Job's default tool controller rather than going through FreeCAD's
  tool-bit loader), but it will bite if you open a Makera bit in the
  FreeCAD GUI on Linux. **Lowercase `shape` when you copy a bit into
  `tools/`.**
- **Don't add the vendored `library/*.fctl` to a Job.** They're 240-entry
  libraries whose `nr` values are upstream's arbitrary ordering, not our
  ATC slots. `tools/carvera_air.fctl` maps slot numbers to the bits
  actually in the machine.
- **The submodule is pinned** (currently `v1.4.0`). Bumping it is a
  deliberate `git submodule update --remote` + commit, so a bit you copied
  won't silently change under you — another reason to copy rather than
  reference.

## Related

- [`cam-conventions.md`](cam-conventions.md) — Carvera Air CAM conventions, the `tools/`
  single-source-of-truth rule, CAM authoring lessons
- [`carvera-air.md`](carvera-air.md) — sending G-code to the machine, and
  the other vendored post-processors as an M-code reference
