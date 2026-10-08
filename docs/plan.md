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
- Icon shapes and library icons (Atlassian, AWS, Azure, Google Cloud) drawn
  with their real artwork, read from the editor per board; the repo carries
  none of it.
- Stickies and sections, sized and drawn as the editor does; in JSON
  Canvas a coloured card and a group with the section's title.
- The Confluence API token: a clear message when it is rejected.

## Remaining

- **Right-angled connectors without bend handles** take a simpler route
  than the editor's, which also steers around other shapes.
- **Boards extracted before icon artwork was read** show icons as named
  placeholders until they are extracted again.
- **Images on connectors** whose picture cannot be downloaded are drawn as
  placeholders, with a warning.
- **The font** is Atlassian Sans only where it is installed; elsewhere a
  system font stands in, so line breaks can differ slightly.
- **Unsupported element types** (tables, mind maps, cards, Jira issues,
  …) are left out with a warning. Supporting one needs a sample board
  containing it, which a scratch board in a personal space can provide.
- **A sticky's author name**, shown when `isAuthorVisible` is set, is not
  drawn: the board stores only an account id.
- **Dark theme**: colours are resolved against the light theme only.
- **List bullets** are the browser's: a little larger than the editor's
  and about 4 units further from the text.
- **Text sits up to a pixel high** in our renders (more at small font
  scales), though the editor's engine and CSS put the baseline in the same
  place; most likely the browser rounding the font's ascent and descent.

## The reference board

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

1. **Spec** (`confluence_whiteboard_exporter/reference.py`): a grid of
   labelled cells, each holding one variation with a caption naming it:
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
3. **`confluence-whiteboard-exporter reference create --space KEY`**:
   creates an empty whiteboard through the REST API and pastes the payload
   into it in the logged-in browser. The editor measures the text and grows
   the boxes itself, which is exactly what the references should capture.
   The board's id, site and the spec's hash are kept outside the repo, in
   the user's config directory, so a changed spec shows the board is out of
   date. It only ever writes to the board it created.
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
7. **Report** (`confluence-whiteboard-exporter reference report`, writes
   `out/report/index.html`, not committed): one row per cell with the
   editor's image, our render, a diff overlay, the measured differences and
   pass, warn, known or fail; images embedded, so the page is
   self-contained. Our renders use Atlassian Sans saved from the live page
   by `reference snapshot` into the user cache, so text compares like for
   like; the font is never committed.

### Order of work

1. ✅ Spec, payload generator and `confluence-whiteboard-exporter reference
   create`: 208 cells, 548 elements, pasted and read back intact in about 30
   seconds.
2. ✅ Reading the editor's drawn geometry and comparing it per cell:
   `confluence-whiteboard-exporter reference snapshot` writes
   `tests/reference/golden/geometry.json`;
   `tests/test_reference_geometry.py` runs one test per cell, offline. 170
   cells matched at first; 40 were known gaps, listed below.
3. ✅ Close the gaps, one kind at a time, each flipping its cells to
   passing: all 208 cells now match the editor.
4. ✅ Reference images, our renders and the report: 185 cells pass,
   22 are known placeholders (icon artwork), one warns (list bullets)
   and none fail.
5. `--live` and `--update-goldens`; images and library icons on the board.
   - ✅ `pytest --live` reads the tester's board once per run (about a
     minute) and fails for each cell drawn differently from the
     references: a box or path moved by more than half a unit, an element
     gone or new, or more than 1% of the cell's drawn pixels changed.
     Boards built at different paste offsets agree to 0.05 units, and two
     captures of one board to a colour level, so these margins catch only
     real changes. All 208 cells still match.
   - ✅ `pytest --update-goldens` (and `reference snapshot`) rewrite the
     references and list what changed; images of cells that did not
     change are kept, so capture noise does not churn them.
   - Images and more library icons on the board.

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

`confluence_whiteboard_exporter/geometry_probe.js`, read-only. The editor
runs an entity-component system (Becsy); the probe finds its World class
among the page's modules and captures the running world on its next frame.
Drawn boxes come from the positioning engine, by element id; connector paths
are components, readable only while a system executes, so they are read
during one frame of the system that draws paths. Board elements are matched
back to the spec by their stored centres (the paste moves them all by one
offset) and connectors and labels by what they attach to, so the references
are keyed by cell and survive a rebuild.

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

## Stickies and sections

The two most common elements the export leaves out. Learnt from a scratch
board in a personal space, made through the editor's own toolbar and
copied back, and from the editor's code.

### What the editor stores

- **Sticky** (`sticky`): `position`, `size` (144 × 144 by default),
  `color`, `text` (ADF), `fontScale`, `alignment`, `verticalAlignment`,
  `basisSize`/`basisPosition`, `rotation`; also `createdBy`, `authorIds`
  and `isAuthorVisible` (account ids: never exported). Yjs keys: `c`,
  `tx`, `a`, `va`, `cb`, `aids`.
- **Section** (`section`): `position`, `size`, `color`, `title`,
  `titleWidth` (140, the placeholder's width; not the drawn tab's),
  `hasDropShadow`, `rotation`. Yjs keys: `c`, `ti`, `tw`, `ds`, `cf`.
- Neither records what is in a section: membership is geometric. A
  section is a frame drawn under what lies on it.

### How the editor draws them (to confirm cell by cell)

- **Stickies** grow downward to fit their text, as shapes do, and the
  growth is not stored either: a sticky of seven lines is
  `7 × 22 + 24 = 178` tall, so its text is set as a shape's, 12 units in.
  Colours resolve through the same palette as shapes (stored `#FFDEB8`
  is drawn `#FCE4A6`). They have a soft shadow and no outline.
- **Sections**: the fill resolves through the palette; the border and the
  title's colours come from the fill's palette group: a light fill (x100)
  gets a strong border (x300) and grey title text, a medium fill (x200,
  the default teal) the strongest (x600: teal is drawn `#2898BD`) and
  white text, a strong fill (x300) a darker one and white text; white
  gets a grey border. The title sits on a tab 24 tall whose middle is 18
  above the section's top edge, as wide as the semibold title plus 8 on
  each side, but no wider than the section.

### Steps

1. ✅ Read stickies and sections from both dump formats into the board
   model, and export them: JSON Canvas as a coloured card and a group
   with its title; SVG and HTML drawn as above.
2. ✅ Cells on the reference board: sticky colours, text lengths,
   alignments, sizes and scales; section colours from each palette group
   and white, short and long titles, a drop shadow, elements on a
   section, and one pasted before its section: 37 cells, 245 in all.
3. ✅ Match the editor's geometry cell by cell, then its drawing through
   the report: every cell matches; the report has 222 pass, 22 known (icon
   artwork) and 1 warn (list bullets).
4. ✅ Document the rules in confluence-whiteboard-model.md.

What the board showed that the code and the first scratch board did not:

- Stickies follow their own sizing strategy (the stored size scaled with
  the font, grown from the unscaled box's top, held to 144); sections are
  held to 160.
- A section is drawn in its stored z-order, not under everything: one
  pasted after a sticky covers it.
- A strong fill's border is the hue's darkest colour; white's is
  `#DDDEE1`; borders are 3 wide; a drop shadow replaces the border.
- The title tab is the title plus 13.6, at most half the section less 8.

Also found on the way: path drift compared sampled points 2 apart, which
could read 1.0 for identical lines; it now measures to the other line's
segments, and rebuilding the board changes no existing cell.

## Icons

The shape picker's "more shapes" (the 21 icon shapes) and the library icons
(Atlassian, AWS, Azure, Google Cloud) were placeholders: their artwork is
Atlassian's and the clouds', and the repo is public. Both are vector
drawings in the editor, so the extractor now reads the ones a board uses
into its dump:

- Icon shapes: from the editor's shape registry, the same drawing form as
  `shape_data.json`, kept in the dump's `drawings`.
- Library icons: from each collection's module, which maps an icon to its
  drawing and to its original SVG file on the site; the SVG goes into the
  board's `media/`, recorded in the dump's `icons`.

`reference snapshot` caches all of them outside the repo, and the report
draws the icon cells with it: 244 pass, 1 warn (list bullets), none known.
Comparing them showed one more rule: the icons, and the actor, are drawn
without the half-line-width inset the editor gives every other kind's
outline.

New icons and packs: the editor's icon loader imports one module per pack
(atlassian, aws, azure, gcp: 1,737 icons, all read and downloaded once);
the extractor looks icons up there by name, so new icons and packs that
follow the same form need no change. Shape kinds newer than
`shape_data.json` are read from the editor with the board, like the icons.
`pytest --live` lists the loader's packs and fails if one cannot be read or
is not yet among those checked.

## Later

- Export a space straight into an Obsidian vault (`--vault`, `--vault-prefix`)
  and check the canvases there.
