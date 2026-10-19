# CAM conventions for the Carvera Air

Every CAM generator in this repo targets the Makera Carvera Air.

## Conventions

- **XYZ zero** is the top surface of the stock, bottom-left corner viewed from above. Match this in any new CAM script.
- **Tool definitions** are FreeCAD `.fctb` / `.fctl` files under [`tools/`](../tools/). They are the single source of truth. Pipeline code reads feeds, speeds and diameter from the `.fctb`; don't hardcode them in the Python.
- **Starting feeds/speeds for a Makera bit come from the vendored Makera library**, not from guesswork: `vendor/CarveraProfiles/CAM_Post_Processors/Freecad/Tools/` holds 615 `.fctb`s (124 bits × per-material variants). **Copy the bit into `tools/` and translate it.** Makera's `attribute` block uses different keys, unitless values and a fraction-of-diameter `stepover`, so pointing pipeline code at a vendored path fails with `KeyError`. Mapping table and sharp edges: [`makera-tool-library.md`](makera-tool-library.md).
- **Post processor**: Makera's, vendored at `vendor/CarveraProfiles/CAM_Post_Processors/Freecad/1.1PostProcessor/makera_post.py`. Use it from any FreeCAD-driven pipeline (`cam/brass_holes/` is the reference implementation).
- **Save the FCStd alongside the G-code** unless explicitly overridden. It's the only way to inspect the generated job in the FreeCAD GUI afterwards. `cam/brass_holes/brass_holes.py` does this by default.
- **Tool legend at the top of every G-code.** Each emitted `.gcode` opens with a comment block mapping the tool numbers the job loads to their bit names (e.g. `(T1 = 6mm 2F 52mm Up-Cut Router Bit)`), so the operator can confirm the right bit is in each slot. Build it from the `.fctb` `"name"` field, list only the tools the job calls, keep it ASCII, and emit it via the post-processor preamble.
- **Extractor in the preamble.** Emit `M851 S<pct>` (the "ext out" port) next to `M3`, and `M852` next to `M05` in the postamble.

## Which engine: FreeCAD Path or a numpy emitter

- **Solid-model-driven ops → FreeCAD Path** (`freecadcmd`). Pockets, slots, profiles, chamfers, drilling: anything defined by edges and faces of a solid. Examples: `brass_holes/`, `block_edge_trim/`, `drill_m5_hole_metal_v2/`.
- **Raster / heightmap-driven ops → plain Python (numpy + Pillow) that writes G-code itself.** A relief carving is "every grid point has a height", a raster problem. Tool offsets are morphological dilations of the height grid (flat tip = max under the disc; ball tip = max of terrain + √(r²−d²) − r). Shank, collar and nut clearance are extra `(radius, height-above-tip)` disc tests. Examples: `relief_engrave/`, `thread_mill/`.

Both kinds emit the same G-code conventions and both read their tools from `tools/*.fctb`. The `.fctb` is a JSON record, so the numpy emitters read it as plain JSON.

## CAM authoring lessons (hard-won)

- **Drill helical bores INSIDE-OUT (`StartSide = "Inside"`).** When a hole is wider than the tool, FreeCAD helix-mills it as concentric passes. Starting from the *outside* cuts the finished wall first and leaves a free-standing core that **snaps off and flies out of the machine** as the last inner pass reaches it. This happened on `cam/drill_m5_hole_metal_v2`'s first real cut. `StartSide = "Inside"` removes the centre first. **Caveat:** with `"Inside"` the Helix op always posts **`G3` (conventional milling)** whatever the `Direction` property says, so you lose climb milling.
- **A round base solid has cylindrical faces you must NOT drill.** "Every cylindrical face is a hole" works for a rectangular plate, but a disc's outer rim is also a cylindrical face. Filter hole faces by **radius** and assert the expected count. See `drill_m5_hole_metal_v2.py` → `find_hole_faces()`.
- **`gcode_simulate.py` can't preview arcs.** It treats each `G2`/`G3` as a straight line to the endpoint, so a helical bore renders as a slot. Don't trust a sim of an arc-based job; open the `.FCStd` or read the posted `.gcode`.
- **A `.fctb` tool name with parentheses breaks the tool legend.** A `(` closes a G-code comment early on the Carvera. Swap `()` → `[]` when building the legend line. See `drill_m5_hole_metal_v2.py` → `_safe_comment()`.
