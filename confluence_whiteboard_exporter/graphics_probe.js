// Read the artwork a board's icons are drawn with, from the editor itself.
//
// Injected into the board's frame by extract.py. Read-only. The icon shapes
// (cloud, key, server, ...) and the library icons (Atlassian, AWS, Azure,
// Google Cloud) are Atlassian's and the clouds' artwork, which the repo does
// not carry: the extractor reads what a board uses into its own dump instead.
//
//   shapeDrawings(kinds)  -> {kind: definition, with its drawing if it has one}, as
//                            drawings.kind_entry reads it (the same form as
//                            scripts/dump_shapes.js)
//   libraryIcons(icons)   -> {"collection/category/iconId": SVG text}
//   libraryCatalogue(cs)  -> {collection: {category: [[iconId, SVG path]]}}
//   libraryCollections()  -> the packs the editor's icon loader offers
//
// Modules and exports are found by shape, not name: the bundle's export
// names change with every deploy.
(() => {
  if (globalThis.__whiteboardExporterGraphics) return;

  function moduleUrls() {
    return [...new Set([
      ...[...document.querySelectorAll('script[type=module][src]')].map((s) => s.src),
      ...[...document.querySelectorAll('link[rel=modulepreload][href]')].map((l) => l.href),
      ...performance.getEntriesByType('resource').map((e) => e.name),
    ])].filter((u) => /\.js($|\?)/.test(u));
  }

  let registry = null;
  async function shapeRegistry() {
    if (registry) return registry;
    for (const url of moduleUrls()) {
      let mod;
      try { mod = await import(url); } catch (_e) { continue; }
      for (const value of Object.values(mod)) {
        const first = value && typeof value === 'object' ? value[0] : null;
        if (first && typeof first === 'object' && first.rendererType && value[13] && value[13].key === 'database') {
          registry = value;
          return registry;
        }
      }
    }
    throw new Error("the editor's shape definitions were not found");
  }

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
  // A colour role (the element's fill or outline colour), or a colour of its own.
  const colour = (c) => {
    if (!c) return null;
    if (c.color && c.color.length >= 3) return { kind: 'Solid', rgba: Array.from(c.color) };
    return { kind: c.constructor.name.replace('Graphic', '').replace('Color', '') };
  };
  const section = (sec) => ({ color: colour(sec.color), rule: sec.fillRule ?? null, segs: [...sec.segments].map(segment) });
  const probe = (fit) => {
    if (typeof fit !== 'function') return null;
    const out = { left: 0, top: 0, right: 0, bottom: 0 };
    fit(out, { left: 0, top: 0, right: 200, bottom: 300 }, { strokeThickness: undefined });
    return [out.left, out.top, out.right, out.bottom].map(r);
  };

  async function shapeDrawings(kinds) {
    const reg = await shapeRegistry();
    const out = {};
    for (const kind of kinds) {
      const def = reg[kind];
      if (!def) continue;
      const rec = {
        key: def.key, cat: def.category || null, renderer: def.rendererType,
        replicates: def.replicatedShape ?? null, exterior: def.exteriorTextArea || null,
        noText: !!def.textDisabled, fit: probe(def.boundingBoxToRenderBox),
      };
      const graphic = def.graphic || def.graphicForSvgRendering;
      if (graphic) {  // a kind that reuses another's drawing has none of its own
        rec.natural = [graphic.naturalSize[0], graphic.naturalSize[1]];
        rec.fills = graphic.fills.map(section);
        rec.strokes = graphic.strokes.map(section);
        rec.text = graphic.textArea ? [point(graphic.textArea.topLeft), point(graphic.textArea.bottomRight)] : null;
      }
      out[kind] = rec;
    }
    return out;
  }

  // A collection's module (atlassian, aws, azure, gcp) maps each category's
  // icons to {svg, graphic, ...}; `svg` is the icon's own SVG file on the
  // site. The module is loaded once an icon of it is drawn, and otherwise
  // found where other modules name it. Modules named alike (the atlassian-*
  // themes) are told apart by what they export.
  function asCollection(mod, collection) {
    const value = mod && mod[collection];
    const isCollection = value && typeof value === 'object'
      && Object.values(value).some((category) => category && category.items instanceof Map);
    return isCollection ? value : null;
  }

  async function collectionModule(collection) {
    const own = new RegExp(`/${collection}-[A-Za-z0-9_-]+\\.js($|\\?)`);
    const named = new RegExp(`["'\`]\\./(${collection}-[A-Za-z0-9_-]+\\.js)["'\`]`, 'g');
    const tried = new Set();
    const attempt = async (url) => {
      if (tried.has(url)) return null;
      tried.add(url);
      try { return asCollection(await import(url), collection); } catch (_e) { return null; }
    };
    for (const url of moduleUrls().filter((u) => own.test(u))) {
      const found = await attempt(url);
      if (found) return found;
    }
    for (const u of moduleUrls()) {
      let text;
      try { text = await (await fetch(u)).text(); } catch (_e) { continue; }
      for (const m of text.matchAll(named)) {
        const found = await attempt(new URL(m[1], u).href);
        if (found) return found;
      }
    }
    return null;
  }

  // The packs the editor's icon loader offers: the collection modules the
  // loader's module imports, which is where a new pack would be added.
  async function libraryCollections() {
    const isLoader = (v) => v && typeof v === 'object'
      && typeof v.getCollection === 'function' && typeof v.loadIconData === 'function';
    const holdsLoader = async (url) => {
      try { return Object.values(await import(url)).some(isLoader); } catch (_e) { return false; }
    };
    let url = null;
    for (const u of moduleUrls().filter((u) => /\/lazily-loaded-svg-/.test(u))) {
      if (await holdsLoader(u)) { url = u; break; }
    }
    if (!url) {
      for (const u of moduleUrls()) {
        let text;
        try { text = await (await fetch(u)).text(); } catch (_e) { continue; }
        const m = /["'`]\.\/(lazily-loaded-svg-[A-Za-z0-9_-]+\.js)["'`]/.exec(text);
        if (m && await holdsLoader(new URL(m[1], u).href)) { url = new URL(m[1], u).href; break; }
      }
    }
    if (!url) return null;
    const text = await (await fetch(url)).text();
    const names = [...text.matchAll(/import\(`\.\/([A-Za-z0-9_-]+)\.js`\)/g)]
      .map((m) => m[1].replace(/-[A-Za-z0-9_-]{8}$/, ''));
    return [...new Set(names)].sort();
  }

  // How many icons each collection has, and how many of them name an SVG file.
  async function libraryCatalogue(collections) {
    const out = {};
    for (const collection of collections) {
      const value = await collectionModule(collection);
      if (!value) { out[collection] = null; continue; }
      const categories = {};
      for (const [name, category] of Object.entries(value)) {
        if (!(category && category.items instanceof Map)) continue;
        categories[name] = [...category.items.entries()].map(([id, item]) => [id, typeof item.svg === 'string' ? item.svg : null]);
      }
      out[collection] = categories;
    }
    return out;
  }

  async function libraryIcons(icons) {
    const out = {};
    const collections = {};
    for (const { collection, category, iconId } of icons) {
      try {
        if (!(collection in collections)) collections[collection] = await collectionModule(collection);
        const item = collections[collection]?.[category]?.items?.get(iconId);
        if (!item || !item.svg) continue;
        const res = await fetch(new URL(item.svg, location.href).href);
        if (res.ok) out[`${collection}/${category}/${iconId}`] = await res.text();
      } catch (_e) { /* left as a placeholder */ }
    }
    return out;
  }

  globalThis.__whiteboardExporterGraphics = { shapeDrawings, libraryIcons, libraryCatalogue, libraryCollections };
})();
