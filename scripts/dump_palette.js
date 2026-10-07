// Dump the whiteboard editor's colour palette, for scripts/build_palette.py.
//
// Paste into the DevTools console of any open Confluence whiteboard. Boards
// store colours as RGB values from the legacy Atlassian palette; the editor
// looks each one up by name and paints it with the current theme's design
// token (stored #172B4D is drawn as color.text, #292A2E in the 2025 theme).
// This reads the editor's name -> RGB and name -> token maps from the module
// the page has already loaded, resolves every token against the page's own
// light theme and copies a JSON dump to the clipboard. Then:
//   pbpaste | uv run scripts/build_palette.py
//
// Maps are found by shape, not name: the bundle's export names change with
// every deploy.
window.whiteboardExporterDumpPalette = async () => {
  const frame = [...document.querySelectorAll('iframe')].find((f) => (f.src || '').includes('/whiteboards/whiteboard/'));
  const win = frame ? frame.contentWindow : window;
  const urls = [
    ...[...win.document.querySelectorAll('script[type=module][src]')].map((s) => s.src),
    ...[...win.document.querySelectorAll('link[rel=modulepreload][href]')].map((l) => l.href),
  ].filter((u) => /\.js($|\?)/.test(u));
  const isRgb = (v) => v && typeof v === 'object' && typeof v.x === 'number' && typeof v.z === 'number';
  let colours, tokens;
  for (const url of urls) {
    const mod = await win.Function('u', 'return import(u)')(url);
    for (const value of Object.values(mod)) {
      if (!value || typeof value !== 'object' || !('textDefaultColor' in value)) continue;
      // The element palette: every colour an element can be given, and no
      // UI-only colours such as textDisabled (which shares n400's RGB).
      if (isRgb(value.textDefaultColor) && 'none' in value && !('textDisabled' in value)) colours = value;
      if (value.textDefaultColor === 'color.text' && (!tokens || Object.keys(value).length > Object.keys(tokens).length)) tokens = value;
    }
    if (colours && tokens) break;
  }
  if (!colours || !tokens) throw new Error('palette not found; is a whiteboard open?');

  const style = getComputedStyle(win.document.documentElement);
  const cssVar = (token) => '--ds-' + token.replace(/^color\./, '').replace(/^elevation\./, '').replace(/\./g, '-');
  const dump = { theme: win.document.documentElement.getAttribute('data-color-mode'), colours: [] };
  for (const [name, rgb] of Object.entries(colours)) {
    if (!isRgb(rgb)) continue;
    const token = tokens[name] ?? null;
    dump.colours.push({
      name, rgb: [rgb.x, rgb.y, rgb.z], token,
      value: token ? style.getPropertyValue(cssVar(token)).trim() || null : null,
    });
  }
  return JSON.stringify(dump);
};

window.whiteboardExporterDumpPalette().then((json) => {
  if (typeof copy === 'function') {
    copy(json); // DevTools console utility
    console.log(`copied the palette (${json.length} bytes)`);
  } else {
    console.log(json);
  }
});
