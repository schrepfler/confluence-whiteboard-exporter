# wb2canvas

Export Atlassian Confluence Cloud whiteboards to
[JSON Canvas](https://jsoncanvas.org) (editable in Obsidian), a static SVG
replica, or an interactive HTML viewer.

## How it works

```
discover ─→ _boards.json ─→ extract ─→ dump.json + media/ ─→ convert ─→ .canvas / .svg / .html
 (REST)                     (browser)                       (offline)
```

Confluence has no API for whiteboard content; the canvas lives in a Yjs
document synced to the page. `extract` opens each board in a browser using
your saved session, selects everything and captures the canvas's own
copy/paste payload, which is a readable JSON description of every element. If
that fails it falls back to reading the Yjs document out of the page, which
gives geometry and colour but no text. Embedded images are saved as the page
loads them.

`convert` works offline from the saved dump, so you can re-render without
touching Confluence.

[docs/confluence-whiteboard-model.md](docs/confluence-whiteboard-model.md)
documents the whiteboard data model: element types, shape kinds, line ends
and the property dictionary.

| Module | Role |
|---|---|
| `discover.py` | Lists a space's whiteboards via the Confluence REST API |
| `extract.py` | `auth attach` login, and the `Extractor` browser session |
| `fiber_probe.js` | Injected into the board page to capture its content |
| `model.py` | Schema of the raw dump file |
| `board.py` | Normalises either kind of dump into one `Board`; layout and stable ids |
| `canvas.py`, `svg.py`, `html.py` | Renderers; `shapes.py` holds stereotype geometry, `connectors.py` the editor's connector router and line ends |
| `deploy.py` | Writes outputs, optionally into an Obsidian vault |

## Setup

Requires [uv](https://docs.astral.sh/uv/), Python 3.11+ and Google Chrome.

```sh
uv sync
cp .env.example .env    # then fill in your values
```

`.env` holds `CONFLUENCE_BASE_URL`, `CONFLUENCE_EMAIL` and
`CONFLUENCE_API_TOKEN` and is gitignored. Create a token at
<https://id.atlassian.com/manage-profile/security/api-tokens>. Tokens expire;
a 403 "caller cannot access Confluence" usually means yours has.

The extractor uses your installed Chrome. Without it, run
`uv run playwright install chromium` to use Playwright's bundled build.

## Usage

Log in once. This opens your own Chrome with a private profile; sign in as
usual and press Enter in the terminal to save the session:

```sh
uv run wb2canvas auth attach
```

Driving the login through an automated browser is deliberately not offered:
SSO providers such as GoDaddy reject it as a bot. Run `auth attach` again
whenever extraction reports that the session was rejected.

One board:

```sh
uv run wb2canvas extract 1000001
uv run wb2canvas convert out/<spaceKey>/1000001/dump.json -f canvas -f svg -f html
```

A whole space, straight into an Obsidian vault:

```sh
uv run wb2canvas export <spaceKey> --vault ~/Obsidian/Vault --vault-prefix Whiteboards -f canvas -f svg
```

Boards whose `dump.json` already exists are skipped; pass `--force` to
re-extract or re-render. `-v` shows progress detail.

### Output formats

- **canvas**: editable in Obsidian. JSON Canvas cannot express dashed
  borders, shape stereotypes, freehand lines, connector bends or line ends
  other than arrows, so status is conveyed by colour alone and dividers
  become thin cards.
- **svg**: faithful static replica: shape stereotypes, dashed and dotted
  outlines, coloured free text, connectors routed the way the editor routes
  them (through their bend points), and the editor's line ends (arrows,
  diamonds, crow's feet, ...). Contains no script, so it is safe to embed
  (`![[board.svg]]`) or attach.
- **html**: the same drawing with drag, pan and zoom. Open it in a browser.

Anything a format cannot show is reported as a warning rather than dropped
silently: element types not supported yet (stickies, sections, tables, ...),
shape kinds without a stereotype, and line ends JSON Canvas lacks.

SVG and HTML reference images relative to their own location, so keep them
next to their `media/` folder.

### Shape stereotypes

Confluence stores a shape's kind as a number. All 89 kinds, and how each is
drawn, are listed in
[docs/confluence-whiteboard-model.md](docs/confluence-whiteboard-model.md).
The flowchart shapes have their own outlines; architecture icons and most UML
shapes are drawn as rectangles. Override a mapping by kind name or number,
e.g. `--shape-map 'database=hard-disk,60=ellipse'`. Stereotype names are
listed in `convert --help`.

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

## Development

```sh
uv run pytest
```

`tests/fixtures/sample_dump.json` is a minimal hand-made board in the
clipboard format. `tests/test_viewer_browser.py` drives the HTML viewer in
Chrome and is skipped when no browser is available.
