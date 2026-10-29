# confluence-whiteboard-exporter

Export Atlassian Confluence Cloud whiteboards to [JSON
Canvas](https://jsoncanvas.org) (editable in Obsidian), an SVG replica that
can be dragged, panned and zoomed when opened in a browser, or an HTML
viewer page. Comment threads come along, pinned where they are on the board.

## How it works

```
discover ─→ _boards.json ─→ extract ─→ dump.json + media/ ─→ convert ─→ .canvas / .svg / .html
 (REST)                     (browser)                       (offline)
```

Confluence has no API for whiteboard content; the canvas lives in a Yjs
document synced to the page. `extract` opens each board in a browser using
your saved session, selects everything and captures the canvas's own
copy/paste payload, which is a readable JSON description of every element.
If that fails it falls back to reading the Yjs document out of the page,
which gives geometry and colour but no text. Embedded images are saved as
the page loads them, and the artwork of the icons, stickers and stamps a
board uses is read from the editor.

Comment pins are not in the copy, so `extract` also reads them from the Yjs
document and fetches their threads (replies, authors, whether resolved)
from the Confluence API with the same browser session, saving each
author's picture with the board.

`convert` works offline from the saved dump, so you can re-render without
touching Confluence.

The editor grows shapes to fit their text, routes connectors and places
labels itself, writing little of it back. The export reproduces those rules
from the editor's own shape definitions, text engine and router, and a
[reference board](#the-reference-board) checks every element against what
the live editor draws.

## Architecture

```
extract (browser)                      convert (offline)

clipboard copy ─┐                                    ┌─→ canvas.py ──────────────────→ .canvas
Yjs document ───┼─→ dump.json + media/ ─→ board.py ──┤
REST threads ───┘                         (Board)    └─→ svg.py ─┬─→ html.py + viewer.js ─→ .svg, .html
                                                                 └─→ (--static-svg) ─────→ .svg
```

- **The dump** (`dump.json`, schema in `model.py`) is everything read from
  Confluence for one board: its elements in either format, the artwork it
  uses and its comment threads. Nothing after it needs Confluence.
- **The board model** (`board.py`) normalises either dump format into one
  `Board` of nodes, edges and comments, and lays it out the way the editor
  does: text, box sizes, growth, stable ids.
- **The renderers** draw a `Board`. `svg.py` is the faithful replica and
  carries no script; `canvas.py` maps the board onto JSON Canvas.
- **The viewer** (`viewer.js`) is the only script. It adds drag, pan, zoom
  and comment threads to an exported SVG, reading everything it needs from
  the SVG's own data attributes, so the drawing and the behaviour stay
  apart: `html.py` writes the SVG with the viewer attached to it, in a page
  or inside the SVG itself, and any other page or application can attach it
  the same way ([embedding](#embedding-the-viewer)).

## Setup

Requires [uv](https://docs.astral.sh/uv/), Python 3.11+ and Google Chrome.

```sh
uv sync
cp .env.example .env    # then fill in your values
```

`.env` holds `CONFLUENCE_BASE_URL`, `CONFLUENCE_EMAIL` and
`CONFLUENCE_API_TOKEN` and is gitignored; it is read from the directory you
run the command in. The same values can be given as `--base-url`, `--email`
and `--token`. The token is used to list a space's boards and to look up a
single board; reading boards and their comments uses the browser session.
Create a token at
<https://id.atlassian.com/manage-profile/security/api-tokens>. Tokens
expire; when Confluence rejects yours, the command says so.

Login uses your installed Google Chrome. Extraction uses it too, or, without
it, Playwright's own Chromium: `uv run playwright install chromium`.

## Usage

Log in once. This opens your own Chrome with a private profile; sign in as
usual and press Enter in the terminal to save the session:

```sh
uv run confluence-whiteboard-exporter auth attach
```

Driving the login through an automated browser is deliberately not offered:
single sign-on providers often reject it as a bot. Run `auth attach` again
whenever extraction reports that the session was rejected.

A whole space, in one go:

```sh
uv run confluence-whiteboard-exporter export <spaceKey> -f canvas -f svg -f html
```

Or step by step:

```sh
uv run confluence-whiteboard-exporter discover <spaceKey>          # lists its boards in out/<spaceKey>/_boards.json
uv run confluence-whiteboard-exporter extract 1000001              # one board, or --space <spaceKey> for all
uv run confluence-whiteboard-exporter convert out/<spaceKey>/1000001/dump.json -f svg -f html
uv run confluence-whiteboard-exporter convert --all <spaceKey> -f svg   # re-render every dump, offline
```

Straight into an Obsidian vault (with `export`, `extract` or `convert`):

```sh
uv run confluence-whiteboard-exporter export <spaceKey> --vault ~/Obsidian/Vault --vault-prefix Whiteboards -f canvas -f svg
```

Boards whose `dump.json` already exists are skipped, as are outputs that
exist; pass `--force` to re-extract or re-render. A board extracted by an
earlier version lacks what was added since (icon artwork, comments) until
it is extracted again with `--force`. `-v` shows progress detail,
`--headed` shows the browser, `--no-media` skips images (and comment
authors' pictures), `--strategy clipboard|fiber` forces one way of reading a
board, and `--static-svg` writes the SVG without the viewer. Every command
has `--help`.

### Output formats

- **canvas**: editable in Obsidian. JSON Canvas has only rectangles, arrows
  and edges Obsidian routes itself: shapes become cards coloured like their
  fill or outline, labels join on their edge, line ends other than arrows
  are drawn plain, connectors with a loose end are left out, and dividers
  become thin cards. Stickies are cards in their colour, sections are groups
  named by their title, and library icons, stickers and stamps point at
  their image files. Each comment thread is a card beside where its pin is,
  purple while open and green once resolved. `--collision-fix` nudges
  overlapping cards apart.
- **svg**: a faithful replica. Shapes are drawn from the editor's own
  definitions and grown to fit their text as the editor grows them; text is
  laid out with the editor's character widths, weights and spacing; colours
  are the editor's, in its light theme; connectors are routed the way the
  editor routes them, through their bend handles, with its line ends
  (arrows, diamonds, crow's feet, ...) and with labels on or beside the
  line. It carries the viewer: opened on its own in a browser it fills the
  window and can be dragged, panned and zoomed. Embedded (`![[board.svg]]`
  in Obsidian, an `<img>`) its script does not run and it is the static
  drawing. `--static-svg` leaves the script out, for places that block SVG
  carrying script (some mail filters); it still carries the viewer's data,
  so the viewer can be attached to it later.
- **html**: the same SVG in a page with the viewer attached. Open it in a
  browser.

In the viewer, dragging an element moves it with the connectors, labels,
stamps and comment pins on it, and dragging the board pans it; the wheel
zooms. Moves are not saved: reloading shows the board as exported.

Text is drawn in Atlassian Sans where it is installed; elsewhere a system
font stands in, and line breaks can differ slightly. SVG and HTML reference
images relative to their own location, so keep them next to their `media/`
folder.

### Comments

Comment pins are drawn where they are on the board, with their author's
picture or initials, and pins put on an element move with it. Resolved
threads are drawn too, greyed and ticked (the editor hides them). A pin
keeps its size on screen at every zoom, as in the editor. Its thread is its
tooltip; in the viewer a click opens it beside the pin (who wrote each post,
when, and its text, as plain text), and a click on the board closes it.

### What is drawn

All 89 shape kinds are drawn from the editor's definitions. The 21 icon
shapes (server, cloud, user, ...) and library icons (Atlassian, AWS, Azure,
Google Cloud) are Atlassian's and the clouds' artwork, which this repo does
not carry: when a board is extracted, the artwork of the icons it uses is
read from the live editor into its own `dump.json` and `media/` folder, so
the exports draw the real icons. Icons and packs added to the editor later
are read the same way, and so is the definition of any shape kind newer
than this repo's, so new shapes draw as the editor draws them. A board
extracted by an earlier version shows its icons as named placeholders until
it is extracted again. Draw a kind as another with `--shape-map
'server=database'` (svg and html).

Free text, shapes, stickies (at any font scale), sections, stickers and
stamps (their images read with the board, like the icons), connectors with
their bends and labels, images, freehand lines (drawn straight, end to end)
and comment threads are supported. Tables, mind maps, cards, Jira issues and
the other element types are left out for now. Anything a format cannot show
is reported as a warning rather than dropped silently.
[docs/plan.md](docs/plan.md) lists what is still missing, such as the dark
theme.

### Embedding the viewer

The viewer is one script, `confluence_whiteboard_exporter/viewer.js`, with
one entry point. It needs only an exported SVG, the `--static-svg` one too,
so a page or an application (such as an Obsidian plugin) can show boards
with the same behaviour as the exports, several to a page:

```html
<div id="board" style="height: 480px"><!-- an exported SVG, inlined --></div>
<script src="viewer.js"></script>
<script>
  const board = WhiteboardViewer.attach(document.getElementById('board'), { mode: 'embedded' });
  // board.reset(): every move undone, the whole board in view; board.destroy(): stop listening
</script>
```

| Option | Values | |
|---|---|---|
| `mode` | `'embedded'` (default), `'window'` | Embedded leaves the wheel to the page and zooms with Ctrl or ⌘ held, or a trackpad pinch; window is the board as the whole page, where the wheel zooms and a root `<svg>` is sized to the window |
| `threads` | `'overlay'`, `'inline'` | A thread's card as HTML over the board (the default in a page), or inside the SVG (the default for an SVG on its own) |

What it reads from the SVG is its contract with the exporter:

- the root's `data-wb-board` (the board id) and `data-wb-config` (JSON: the
  exporter's routing and dash constants and the drawings of the board's
  shape kinds, with a `version` that changes only when the contract does);
- each element's `g.wb-node` with `data-id`, its box (`data-x`, `data-y`,
  `data-w`, `data-h`), `data-kind` and `data-parent` (what a stamp is on);
- each connector's `path.wb-edge` with its ends (`data-src`, `data-tgt`), their anchors (`data-sa`,
  `data-ta`), `data-routing`, its bends (`data-wp`, `data-axes`) and `data-ends`;
- each pin's `g.wb-comment` with `data-x`, `data-y`, `data-parent` and
  `data-thread` (JSON: `resolved`, and each post's `author`, `when` and
  `text`, plain text only: cards are built from text, never markup).

The html page and the svg attach it with `mode: 'window'`.

## Output layout

```
out/<spaceKey>/_boards.json
out/<spaceKey>/<boardId>/dump.json
out/<spaceKey>/<boardId>/media/<fileId>.<ext>          # the board's images
out/<spaceKey>/<boardId>/media/{icon,sticker,stamp}-<id>.<ext>  # the editor's artwork the board uses
out/<spaceKey>/<boardId>/media/avatar-<accountId>.png  # comment authors' pictures
out/<spaceKey>/<boardId>/<boardId>.{canvas,svg,html}
```

Node ids in the outputs are the elements' real Confluence ids where the dump
reveals them, and content hashes otherwise, so they stay stable across
re-exports.

The browser session is kept outside the project, in the per-user config
directory (`~/Library/Application Support/confluence-whiteboard-exporter/`
on macOS, `~/.config/confluence-whiteboard-exporter/` on Linux), with the
reference board's details. Chrome's login profile and the cached editor font
are in the per-user cache directory.

## Development

```sh
uv sync          # includes the dev tools: pytest, Pillow
uv run pytest
```

The tests need no Confluence and no network.
`tests/fixtures/sample_dump.json` is a minimal hand-made board in the
clipboard format. `tests/test_viewer_browser.py` drives the viewer in
Chrome (or Playwright's Chromium) in each of its homes: the html page, the
svg opened on its own, and a page attaching it to several boards; it is
skipped when neither browser is available. `tests/test_comments.py` covers
reading comment pins and threads, with Yjs and REST data built in the test.

### The reference board

To check the export against the live editor, the tests keep a reference: a
board generated from code with every element the export draws, in its
variations, and what the editor made of it.

- **The spec** (`confluence_whiteboard_exporter/reference.py`) describes a
  grid of 264 labelled cells: every shape kind, outlines, fills, colours,
  text alignment, sizes, headings and lists, free text, connectors of every
  routing, line end, weight and style, bend handles and labels, stickies,
  sections, stickers and stamps. Comments are not on it: a paste cannot
  make them.
- **The references** (`tests/reference/golden/`, committed) are the editor's
  drawn geometry for each cell (`geometry.json`: each element's box, each
  connector's path, each label's box) and an image of each cell at 100%
  (`images/`). The cells that show the editor's artwork (icons, stickers,
  stamps) keep their images in the per-user cache instead, so the repo
  carries none of it; a clone without them still runs every test, on
  geometry.
- **The tests** (`tests/test_reference_geometry.py`) export the spec and
  compare it with the references, one test per cell: boxes within 1.5 board
  units, paths within 2. A cell we do not draw like the editor yet goes in
  `KNOWN_GAPS` with the reason.
- **The report** shows each cell as the editor draws it beside our render,
  with an overlay of what differs and the measured differences:

  ```sh
  uv run confluence-whiteboard-exporter reference report   # writes out/report/index.html
  ```

  It needs no Confluence. Our side is rendered in the editor's font, saved
  to the cache by `reference snapshot`; without it a system font stands in
  (`--no-font` forces that).

Checking against the live editor needs your own Confluence and a reference
board, built once with the same `.env` and browser session as an export:

```sh
uv run confluence-whiteboard-exporter reference create --space <spaceKey>   # e.g. your personal space, ~<account id>
```

`reference create` makes a whiteboard titled "confluence-whiteboard-exporter
reference" and pastes the spec into it; run again, it clears and refills the
same board. It only ever writes to that board, and keeps its id outside the
repo. Run it again when the spec changes (the tests say so). Then:

```sh
uv run pytest --live              # also reads your board and compares it with the references
uv run pytest --update-goldens    # makes your board the references (implies --live)
```

`--live` reads the board once per run (about a minute) and fails for each
cell the editor now draws differently: a box or path moved by more than
half a unit, an element gone or new, or more than 1% of the cell's drawn
pixels changed. Such a failure means the editor changed, not the export.
Once the change is understood, `--update-goldens` rewrites
`tests/reference/golden/` and lists what changed; the references of a cell
that did not change are left alone, so capture noise does not churn the
files.
The offline tests then show what the export must follow. `reference
snapshot` does the same update from the command line. Both only read the
board. `--live` also lists the icon packs the editor offers: it fails if one
cannot be read, or if one is new, so that it gets checked before it is added
to the known packs in `tests/test_reference_live.py`.

### Data taken from the editor

Three files hold what was read from the live editor; regenerate them after
an editor update (icon artwork is never among them: it is read per board):

| File | What | How |
|---|---|---|
| `shape_data.json` | The shape kinds' drawings | `scripts/dump_shapes.js` in the DevTools console of an open board, then `pbpaste \| uv run scripts/build_shape_data.py` |
| `palette.json` | The legacy palette in the editor's current theme | `scripts/dump_palette.js`, then `scripts/build_palette.py`, likewise |
| `text_metrics.json` | Each character's width in each text style | `uv run confluence-whiteboard-exporter reference metrics` |

### Code map

| Module | Role |
|---|---|
| `cli.py` | The command line |
| `discover.py` | Lists a space's whiteboards via the Confluence REST API; creates the reference board |
| `extract.py` | `auth attach` login, and the `Extractor` browser session: the copy, the artwork, the comment threads |
| `fiber_probe.js` | Injected into the board page to read its Yjs document (the fallback, and comment pins) |
| `model.py` | Schema of the dump file |
| `comments.py` | Comment pins from the board's Yjs document, placed on the clipboard copy, with their threads from the REST API |
| `board.py` | Normalises either kind of dump into one `Board`; text layout, box sizes and stable ids |
| `adf.py`, `palette.py` | Rich text (Atlassian Document Format) to HTML and Markdown; the editor's colours |
| `shapes.py`, `connectors.py` | The editor's shape drawings, dash layouts and content boxes; its connector router, line ends and where along a connector a label sits |
| `drawings.py`, `graphics_probe.js` | The editor's drawings as data; reading the artwork of a board's icons, stickers and stamps from the editor |
| `canvas.py`, `svg.py` | Renderers: JSON Canvas, and the SVG replica with the viewer's data |
| `viewer.js`, `html.py` | The viewer (drag, pan, zoom, comment threads), and the page and SVG that attach it to the board |
| `deploy.py`, `storage.py` | Writes outputs, optionally into an Obsidian vault; where files go |
| `reference.py`, `editor.py`, `geometry_probe.js` | The reference board's spec; pasting it into the editor and reading back what it draws |
| `snapshot.py` | Reading the reference board back, saving it as the references, and its drift from them |
| `compare.py`, `report.py` | Our export against the references: geometry, and the report |

[docs/confluence-whiteboard-model.md](docs/confluence-whiteboard-model.md)
documents the whiteboard data model and the editor's drawing rules: element
types, shape kinds, text layout, colours, connector routing, line ends,
comment pins and the property dictionary. [docs/plan.md](docs/plan.md) has
what is done, what is left, and how the reference board was built.
