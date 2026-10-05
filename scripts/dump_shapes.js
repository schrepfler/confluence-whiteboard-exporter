// Dump the whiteboard editor's shape definitions, for scripts/build_shape_data.py.
//
// Paste into the DevTools console of any open Confluence whiteboard. It reads
// the editor module the page has already loaded (import() of the same URL
// returns the cached instance, so nothing is re-run) and copies a JSON dump
// to the clipboard. Then: pbpaste | uv run scripts/build_shape_data.py
//
// Exports are found by shape, not name: the bundle's export names change with
// every deploy. Icon artwork ("advanced" shapes) is deliberately left out.
window.wb2canvasDumpShapes = async () => {
  const frame = [...document.querySelectorAll('iframe')].find((f) => (f.src || '').includes('/whiteboards/whiteboard/'));
  const win = frame ? frame.contentWindow : window;
  const urls = [
    ...[...win.document.querySelectorAll('script[type=module][src]')].map((s) => s.src),
    ...[...win.document.querySelectorAll('link[rel=modulepreload][href]')].map((l) => l.href),
  ].filter((u) => /\.js($|\?)/.test(u));
  let registry, enumObj;
  for (const url of urls) {
    const mod = await win.Function('u', 'return import(u)')(url);
    for (const value of Object.values(mod)) {
      if (value && typeof value === 'object') {
        if (value.Database === 13 && value.SharpRectangle === 0) enumObj = value;
        const first = value[0];
        if (first && typeof first === 'object' && first.rendererType && value[13] && value[13].key === 'database') registry = value;
      }
    }
    if (registry && enumObj) break;
  }
  if (!registry || !enumObj) throw new Error('shape definitions not found; is a whiteboard open?');

  const r = (v) => Math.round(v * 1e4) / 1e4;
  const point = (p) => [r(p.xPos()), r(p.yPos()), r(p.xOffset()), r(p.yOffset())];
  const segment = (s) => {
    switch (s.constructor.name) {
      case 'GraphicLine': return ['L', point(s.start), point(s.end)];
      case 'GraphicSubPathStart': return ['M', point(s.start)];
      case 'GraphicCubicBezier': return ['C', point(s.start), point(s.control1 ?? s.cp1), point(s.control2 ?? s.cp2), point(s.end)];
      default: throw new Error('unknown segment ' + s.constructor.name);
    }
  };
  const colour = (c) => (c ? { kind: c.constructor.name.replace('Graphic', '').replace('Color', '') } : null);
  const section = (sec) => ({ color: colour(sec.color), rule: sec.fillRule ?? null, segs: [...sec.segments].map(segment) });
  const probe = (fit) => {
    if (typeof fit !== 'function') return null;
    const out = { left: 0, top: 0, right: 0, bottom: 0 };
    fit(out, { left: 0, top: 0, right: 200, bottom: 300 }, { strokeThickness: undefined });
    return [out.left, out.top, out.right, out.bottom].map(r);
  };

  const dump = {};
  for (const name of Object.keys(enumObj).filter((k) => isNaN(+k))) {
    const kind = enumObj[name], def = registry[kind];
    if (!def) continue;
    const rec = {
      name, key: def.key, cat: def.category || null, renderer: def.rendererType,
      replicates: def.replicatedShape ?? null, exterior: def.exteriorTextArea || null,
      noText: !!def.textDisabled, dash: def.defaultStrokeStyle ?? null,
      fit: probe(def.boundingBoxToRenderBox), textFields: def.textFieldCount ?? null,
    };
    const graphic = def.graphic || def.graphicForSvgRendering;
    if (graphic && def.category !== 'advanced') {
      rec.natural = [graphic.naturalSize[0], graphic.naturalSize[1]];
      rec.fills = graphic.fills.map(section);
      rec.strokes = graphic.strokes.map(section);
      rec.text = graphic.textArea ? [point(graphic.textArea.topLeft), point(graphic.textArea.bottomRight)] : null;
    }
    dump[kind] = rec;
  }
  return JSON.stringify(dump);
};

window.wb2canvasDumpShapes().then((json) => {
  if (typeof copy === 'function') {
    copy(json); // DevTools console utility
    console.log(`copied the shape definitions (${json.length} bytes)`);
  } else {
    console.log(json);
  }
});
