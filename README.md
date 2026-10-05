# wb2canvas

Export Atlassian Confluence Whiteboards to [JSON Canvas 1.0](https://jsoncanvas.org).

Three decoupled modules connected by JSON files on disk:

```
discover.py  → _boards.json   →  extract.py  → dump.json + media/  →  convert.py → <board>.canvas
```

- **discover** — REST (Confluence v2 + `atlassian-python-api`) lists whiteboards in a space.
- **extract** — Playwright headless browser opens each board, reads the live Yjs Doc via the page's own clipboard contract (`data-canvas-clipboard`), downloads embedded images.
- **convert** — pure offline mapping from the extractor's dump to the JSON Canvas spec.

## Status

Phase 0 scaffold. Stubs for all three modules; pydantic models for every boundary; pytest fixtures in place. The converter test is intentionally red-bar until `convert.dump_to_canvas` lands.

## Setup

Requires [`uv`](https://docs.astral.sh/uv/) and Python 3.12+.

```sh
uv sync
uv run playwright install chromium
```

## Configuration

Credentials are read from environment variables. The CLI auto-loads `.env` from the project root on startup, so the simplest setup is:

```sh
cp .env.example .env
# edit .env and fill in your CONFLUENCE_* values
```

`.env` is gitignored. Don't commit it. Generate an Atlassian API token at https://id.atlassian.com/manage-profile/security/api-tokens.

## Day-one flow

```sh
uv run wb2canvas auth attach
uv run wb2canvas extract 1000001
uv run wb2canvas convert out/<spaceKey>/1000001/dump.json
```

### Why `auth attach` (not `auth login`)?

Atlassian instances that federate to providers like GoDaddy, Okta, etc. run aggressive bot detection during the SSO step. A Playwright-driven browser, even with `channel="chrome"` and stealth tweaks, frequently gets flagged ("Your browser is a bit unusual…").

`auth attach` sidesteps this: it launches your **real Google Chrome** with `--remote-debugging-port=9222` and an isolated profile, navigates to Atlassian, and waits for you to log in as a human. After you press Enter, it connects via CDP (Chrome DevTools Protocol) to the running browser and snapshots cookies/localStorage to `storage_state.json`. The IDP only ever sees a regular human-driven Chrome session — no automation fingerprints to detect.

`auth login` (Playwright-driven, headed) is kept as a fallback for tenants without bot detection.

End-to-end against a whole space:

```sh
uv run wb2canvas export <spaceKey>
```

Open the resulting `<board>.canvas` in [Obsidian](https://obsidian.md) (or any JSON Canvas viewer) to verify visually.

## Layout

```
out/<spaceKey>/_boards.json
out/<spaceKey>/<boardId>/dump.json
out/<spaceKey>/<boardId>/<boardId>.canvas
out/<spaceKey>/<boardId>/media/<uuid>.<ext>
```

## Tests

```sh
uv run pytest
```

The converter test fixture (`tests/fixtures/sample_dump.json`) hand-crafts a minimal board (one shape, one text, one connector, one image) using the same JSON shape Atlassian writes to the clipboard. Use it to iterate on the converter without re-extracting from Confluence.
