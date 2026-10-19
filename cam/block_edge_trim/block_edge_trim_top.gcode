(Exported by FreeCAD)
(Post Processor: makera_post)
(Output Time:2026-06-21 14:15:28.775583)
(begin preamble)
(*** PREP THE MACHINE BY HAND BEFORE RUNNING ***)
(  1. Load tool T5 - 2mm 1F endmill - into the spindle.)
(  2. Probe / set the work Z zero on the TRUE top of THIS sheet.)
(  3. Verify the active XY work zero is the bottom-left corner, with)
(     the trimmed edge facing +X.)
(  4. SPOILBOARD: this cuts ~0.3mm past the underside, so the cut line)
(     must sit over a sacrificial layer / clear gap, not bare fixture.)
(  5. SECURE THE OFFCUT: this is a full through-cut, so the waste strip)
(     comes free at the end. Tape/clamp it so it cannot shift into the)
(     cutter. Clamp the KEPT side, clear of the cut line.)
(=== Tool table - load these bits before running ===)
(T5 = 2mm 1F 8mm End Mill (Aluminum))
G90 G94
G17
G54
T5 M6
M3 S12000
M851 S75


G21
(begin operation: BlockEdgeTrim)
G0 X-3.0000 Y123.3200
G0 Z5.0000
G1 Z-1.0000 F200.0000 S12000.0
G1 X192.5200 Y123.3200 F500.0000 S12000.0
G1 Z-2.0000 F200.0000 S12000.0
G1 X-3.0000 Y123.3200 F500.0000 S12000.0
G1 Z-3.0000 F200.0000 S12000.0
G1 X192.5200 Y123.3200 F500.0000 S12000.0
G1 Z-4.0000 F200.0000 S12000.0
G1 X-3.0000 Y123.3200 F500.0000 S12000.0
G1 Z-4.3000 F200.0000 S12000.0
G1 X192.5200 Y123.3200 F500.0000 S12000.0
G0 Z10.0000
(finish operation: BlockEdgeTrim)
(begin postamble)
M05
M852
G17 G90
G28
M30

G21
