# Plan

Where the export stands against the live editor, and what is left. The
rules behind each item are in
[confluence-whiteboard-model.md](confluence-whiteboard-model.md).

## Done

Checked board by board, side by side with the live editor:

- Shapes, outlines and dashes from the editor's own shape definitions.
- Box sizes: shapes keep their basis width and grow only downward to fit
  their text; empty shapes are never grown.
- Text: the editor's type size, line height and font, inside each shape's
  content box. Every box holds its text and line breaks match.
- Colours: the stored legacy palette drawn in the editor's current theme.
- Connectors: curved, straight and right-angled routes, bend handles,
  rounded bends, line ends, and labels on the line.
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
   - the board extracted again after the paste: what the editor stored;
   - the editor's drawn geometry per cell, read from its runtime state:
     each element's box, each connector's path, each label's position;
   - one image per cell from the live canvas, at a fixed zoom, with
     images and fonts loaded and the editor's interface hidden;
   - the editor version they came from.
5. **Offline tests** (default `pytest`): export the golden dump and compare
   our geometry with the editor's per cell; render each cell and compare
   it with its reference image.
6. **Live tests** (`pytest --live`, needs the tester's Confluence through
   the same settings and browser login as an export): read the tester's
   reference board again and report any drift from the references, which
   means the editor changed; `--update-goldens` rewrites the references.
7. **Report** (`out/report/index.html`, not committed): one row per cell
   with the live image, our render, a diff overlay, the measured
   differences and pass, warn or fail; images embedded, so the page is
   self-contained. Our renders use Atlassian Sans fetched from the live
   page during a live run, so text compares like for like; the font is
   never committed.

### Order of work

1. ✅ Spec, payload generator and `wb2canvas reference create`: 208 cells,
   548 elements, pasted and read back intact in about 30 seconds.
2. Reading the editor's drawn geometry per cell, and the per-cell
   comparisons. Moved ahead of the golden dump: see below.
3. Golden dump and geometry; move the existing geometry tests onto them.
4. Reference images, our renders and the report.
5. `--live` and `--update-goldens`; images and library icons on the board.

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
