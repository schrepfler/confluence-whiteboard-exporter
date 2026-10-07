# Plan

Where the export stands against the live editor, and what is left. The
rules behind each item are in
[confluence-whiteboard-model.md](confluence-whiteboard-model.md).

## Done

Checked board by board, side by side with the live editor:

- Shapes, outlines and dashes from the editor's own shape definitions.
- Box sizes: shapes keep their basis width and grow only downward to fit
  their text; empty shapes are never grown.
- Text: the editor's type size, line height, weights, optical size and
  heading spacing, inside each shape's content box. Every box holds its
  text and line breaks match.
- Colours: the stored legacy palette drawn in the editor's current theme.
- Connectors: curved, straight and right-angled routes, bend handles,
  rounded bends, line ends, and labels on the line or to either side.
- Images, including ones still loading when the board was copied.
- Library icons (e.g. AWS) as named placeholders.
- The Confluence API token: a clear message when it is rejected.

## Remaining

- **Right-angled connectors without bend handles** take a simpler route
  than the editor's, which also steers around other shapes.
- **Library icon artwork** (AWS and other collections) is not available;
  icons are drawn as a tile with their name.
- **Images on connectors** whose picture cannot be downloaded are drawn as
  placeholders, with a warning.
- **The font** is Atlassian Sans only where it is installed; elsewhere a
  system font stands in, so line breaks can differ slightly.
- **Unsupported element types** (stickies, sections, tables, mind maps,
  cards, Jira issues, …) are left out with a warning. Supporting one needs
  a sample board containing it.
- **Dark theme**: colours are resolved against the light theme only.
- **List bullets** are the browser's: a little larger than the editor's
  and about 4 units further from the text.
- **Text sits up to a pixel high** in our renders (more at small font
  scales), though the editor's engine and CSS put the baseline in the same
  place; most likely the browser rounding the font's ascent and descent.

## Next: a reference board

The rules above were worked out by comparing exports with real boards by
eye. To keep checking them, and to catch the editor changing, the tests
need a board of their own: every element we draw, in every variation,
built from code, with what the editor makes of it kept as the reference.

### Principles

- **The board is generated, not drawn.** A spec in the repo describes it,
  so its content is synthetic and anyone can recreate it in their own
  Confluence. Nothing identifying a site, space or person is committed.
- **Confluence is needed to refresh the references, not to run the
  tests.** The references are committed; the default test run is offline.
  A live run checks a tester's own board against them and can update them.
- **Numbers decide, pictures explain.** Pass or fail comes from geometry
  read from the editor (boxes, paths, label positions), within stated
  tolerances. Pixel comparisons go into a report for people to look at.

### Pieces

1. **Spec** (`wb2canvas/reference.py`): a grid of labelled cells, each
   holding one variation with a caption naming it:
   - every shape kind, at two sizes; fill on and off; each stroke style
     and size; a sample of palette colours;
   - text: alignment, vertical alignment, font scale, headings, lists,
     bold, text that overflows its box, an empty shape;
   - free text: each alignment, fixed and flexible width;
   - connectors: straight, curved and right-angled, every line end, each
     thickness and stroke style, with and without waypoints, handle axes
     on right-angled ones, labels centred and to either side at several
     proportions, ends attached and loose;
   - an image used twice, a library icon, a freehand line;
   - one of each unsupported element type, to check the warnings.
2. **Payload generator**: turns the spec into the clipboard payload the
   editor reads when pasting, the same format the extractor already
   reads from a copy.
3. **`wb2canvas reference create --space KEY`**: creates an empty
   whiteboard through the REST API and pastes the payload into it in the
   logged-in browser. The editor measures the text and grows the boxes
   itself, which is exactly what the references should capture. The
   board's id, site and the spec's hash are kept in a git-ignored local
   file, so a changed spec shows the board is out of date. It only ever
   writes to the board it created.
4. **References** (`tests/reference/golden/`, committed):
   - the editor's drawn geometry per cell, read from its runtime state:
     each element's box, each connector's path, each label's box
     (`geometry.json`), with the editor version they came from;
   - one image per cell from the live canvas at 100%, with fonts loaded
     and the editor's interface hidden (`images/`).
5. **Offline tests** (default `pytest`): export the golden dump and compare
   our geometry with the editor's per cell; render each cell and compare
   it with its reference image.
6. **Live tests** (`pytest --live`, needs the tester's Confluence through
   the same settings and browser login as an export): read the tester's
   reference board again and report any drift from the references, which
   means the editor changed; `--update-goldens` rewrites the references.
7. **Report** (`wb2canvas reference report`, writes `out/report/index.html`,
   not committed): one row per cell with the editor's image, our render,
   a diff overlay, the measured differences and pass, warn, known or
   fail; images embedded, so the page is self-contained. Our renders use
   Atlassian Sans saved from the live page by `reference snapshot` into
   the user cache, so text compares like for like; the font is never
   committed.

### Order of work

1. ✅ Spec, payload generator and `wb2canvas reference create`: 208 cells,
   548 elements, pasted and read back intact in about 30 seconds.
2. ✅ Reading the editor's drawn geometry and comparing it per cell:
   `wb2canvas reference snapshot` writes `tests/reference/golden/geometry.json`;
   `tests/test_reference_geometry.py` runs one test per cell, offline.
   170 cells matched at first; 40 were known gaps, listed below.
3. ✅ Close the gaps, one kind at a time, each flipping its cells to
   passing: all 208 cells now match the editor.
4. ✅ Reference images, our renders and the report: 185 cells pass,
   22 are known placeholders (icon artwork), one warns (list bullets)
   and none fail.
5. `--live` and `--update-goldens`; images and library icons on the board.

### Known gaps (step 3)

Closed so far, each found by the reference board and checked against it:

- ✅ **Free text** padding (8, not 24) and fixed width.
- ✅ **Text widths and line breaks**, from the editor's own text engine.
- ✅ **Drawings with a label below**: the drawing at its own aspect ratio
  plus the label.
- ✅ **Growth to fit text**, through each shape's content box, after the
  editor's one-line minimum.

- ✅ **Library icons**: a square of their basis width, any label below.
- ✅ **Right-angled connectors between ends facing the same way**: they go
  round the further end, 4 × line width + 10 beyond it.

`KNOWN_GAPS` in the test is empty; a new gap goes there with its cause.

Measuring paths showed one difference that is not a gap: the editor's
path runs to the end point under an arrowhead that covers the line, while
ours stops where it begins; the comparison leaves that stretch out.

### Learnt from step 4

The report's pixel comparison found what the geometry could not:

- ✅ **Shape outlines** are dashed or else solid; the editor draws
  "dotted" and "none" solid.
- ✅ **Optical size**: the editor sets all text at an optical size of
  about 18, where the browser picks one from the font size (scaled text
  was 6% too narrow).
- ✅ **Weights**: bold 653, h1 and h2 medium, the other headings semibold.
- ✅ **Space round headings**, which the engine adds above and below them.
- ✅ **Wrapped lines** keep the space they wrap at, which moves right- and
  centre-aligned lines by a space or half of one.
- ✅ **Side labels** were on the wrong side and too close; their point on
  a curve is by the curve's parameter. Label boxes are now in the
  geometry comparison too.

And in the capture: the Confluence page's own title bar floats over the
board's frame, and the pointer left over the canvas highlights what it
rests on; both are now kept out of the images, and two snapshots in a row
differ by at most one colour level in a pixel column.

The pixel score counts a pixel as different only when nothing within a
pixel of it in the other image is close, so anti-aliasing and the
sub-pixel placement of glyphs do not count. Warn means more than 15% of
drawn pixels differ.

### How the geometry is read

`wb2canvas/geometry_probe.js`, read-only. The editor runs an
entity-component system (Becsy); the probe finds its World class among
the page's modules and captures the running world on its next frame.
Drawn boxes come from the positioning engine, by element id; connector
paths are components, readable only while a system executes, so they are
read during one frame of the system that draws paths. Board elements are
matched back to the spec by their stored centres (the paste moves them
all by one offset) and connectors and labels by what they attach to, so
the references are keyed by cell and survive a rebuild.

### Learnt from step 1

- A paste keeps everything the spec sets: basis boxes, anchors, line
  ends, routing, waypoint axes, label proportions and sides. Every
  element moves by one offset, to where the canvas was clicked; captions
  find the cells again.
- **Growth is not stored on paste.** The editor grows a shape to fit its
  text when it draws it, but writes the grown box back only when someone
  edits the text. A pasted board, or one made by any tool, stores no
  growth, so the stored data cannot be the reference for box sizes: the
  editor's drawn geometry must be read at runtime. The same gap affects
  exports of such boards: our static SVG draws an overflowing shape at its
  stored size (the HTML viewer grows it).

### Open questions

- Images need a real upload rather than a paste; how best to add them.
- How large one board can grow before the editor slows down; at 548
  elements it is still quick.

## Later

- Export a space straight into an Obsidian vault (`--vault`, `--vault-prefix`)
  and check the canvases there.
