# confluence-whiteboard-exporter

Export Atlassian Confluence Cloud whiteboards to [JSON
Canvas](https://jsoncanvas.org) (editable in Obsidian), a static SVG
replica, or an interactive HTML viewer.

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
the page loads them.

`convert` works offline from the saved dump, so you can re-render without
touching Confluence.

The editor grows shapes to fit their text, routes connectors and places
labels itself, writing little of it back. The export reproduces those rules
from the editor's own shape definitions, text engine and router, and a
[reference board](#the-reference-board) checks every element against what
the live editor draws.

## Setup

Requires [uv](https://docs.astral.sh/uv/), Python 3.11+ and Google Chrome.

```sh
uv sync
cp .env.example .env    # then fill in your values
```

`.env` holds `CONFLUENCE_BASE_URL`, `CONFLUENCE_EMAIL` and
`CONFLUENCE_API_TOKEN` and is gitignored; it is read from the directory you
run the command in. The same values can be given as `--base-url`, `--email`
and `--token`. Create a token at
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
exist; pass `--force` to re-extract or re-render. `-v` shows progress
detail, `--headed` shows the browser, `--no-media` skips images, and
`--strategy clipboard|fiber` forces one way of reading a board. Every
command has `--help`.

### Output formats

- **canvas**: editable in Obsidian. JSON Canvas has only rectangles, arrows
  and edges Obsidian routes itself: shapes become cards coloured like their
  fill or outline, labels join on their edge, line ends other than arrows
  are drawn plain, connectors with a loose end are left out, and dividers
  become thin cards. `--collision-fix` nudges overlapping cards apart.
- **svg**: a faithful static replica. Shapes are drawn from the editor's own
  definitions and grown to fit their text as the editor grows them; text is
  laid out with the editor's character widths, weights and spacing; colours
  are the editor's, in its light theme; connectors are routed the way the
  editor routes them, through their bend handles, with its line ends
  (arrows, diamonds, crow's feet, ...) and with labels on or beside the
  line. It contains no script, so it is safe to embed (`![[board.svg]]`) or
  attach.
- **html**: the same drawing with drag, pan and zoom; connectors and labels
  follow the shapes you move. Open it in a browser.

Text is drawn in Atlassian Sans where it is installed; elsewhere a system
font stands in, and line breaks can differ slightly. SVG and HTML reference
images relative to their own location, so keep them next to their `media/`
folder.

### What is drawn

All 89 shape kinds are drawn from the editor's definitions, except the 21
architecture icons (server, cloud, user, ...): their artwork is Atlassian's,
so they are drawn as placeholders with their label below. Library icons (AWS
and others) are named tiles for the same reason. Draw a kind as another with
`--shape-map 'server=database'` (svg and html).

Free text, shapes, connectors with their bends and labels, images and
freehand lines (drawn straight, end to end) are supported. Stickies,
sections, tables, mind maps, cards, Jira issues and the other element types
are left out for now. Anything a format cannot show is reported as a warning
rather than dropped silently. [docs/plan.md](docs/plan.md) lists what is
still missing, such as the dark theme.

## Output layout

```
out/<spaceKey>/_boards.json
out/<spaceKey>/<boardId>/dump.json
out/<spaceKey>/<boardId>/media/<fileId>.<ext>
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
clipboard format. `tests/test_viewer_browser.py` drives the HTML viewer in
Chrome (or Playwright's Chromium) and is skipped when neither is available.

### The reference board

To check the export against the live editor, the tests keep a reference: a
board generated from code with every element the export draws, in its
variations, and what the editor made of it.

- **The spec** (`confluence_whiteboard_exporter/reference.py`) describes a
  grid of 208 labelled cells: every shape kind, outlines, fills, colours,
  text alignment, sizes, headings and lists, free text, connectors of every
  routing, line end, weight and style, bend handles and labels.
- **The references** (`tests/reference/golden/`, committed) are the editor's
  drawn geometry for each cell (`geometry.json`: each element's box, each
  connector's path, each label's box) and an image of each cell at 100%
  (`images/`).
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
`tests/reference/golden/` and lists what changed; the image of a cell that
did not change is left alone, so capture noise does not churn the files.
The offline tests then show what the export must follow. `reference
snapshot` does the same update from the command line. Both only read the
board.

### Data taken from the editor

Three files hold what was read from the live editor; regenerate them after
an editor update:

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
| `extract.py` | `auth attach` login, and the `Extractor` browser session |
| `fiber_probe.js` | Injected into the board page to capture its content |
| `model.py` | Schema of the raw dump file |
| `board.py` | Normalises either kind of dump into one `Board`; text layout, box sizes and stable ids |
| `adf.py`, `palette.py` | Rich text (Atlassian Document Format) to HTML and Markdown; the editor's colours |
| `shapes.py`, `connectors.py` | The editor's shape drawings, dash layouts and content boxes; its connector router, line ends and where along a connector a label sits |
| `canvas.py`, `svg.py`, `html.py` | Renderers |
| `deploy.py`, `storage.py` | Writes outputs, optionally into an Obsidian vault; where files go |
| `reference.py`, `editor.py`, `geometry_probe.js` | The reference board's spec; pasting it into the editor and reading back what it draws |
| `snapshot.py` | Reading the reference board back, saving it as the references, and its drift from them |
| `compare.py`, `report.py` | Our export against the references: geometry, and the report |

[docs/confluence-whiteboard-model.md](docs/confluence-whiteboard-model.md)
documents the whiteboard data model and the editor's drawing rules: element
types, shape kinds, text layout, colours, connector routing, line ends and
the property dictionary. [docs/plan.md](docs/plan.md) has what is done, what
is left, and how the reference board was built.
