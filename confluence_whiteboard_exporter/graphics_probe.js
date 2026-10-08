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
//   stickerImages()       -> {stickerId: its WebP's path on the site}
//   stampImages(ids)      -> {stampId: its image as a data URL}
//   download(paths)       -> {key: base64 of the file at paths[key]}
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

  // Each sticker's own image on the site, by sticker id. The editor draws
  // stickers from sprite sheets; the picker shows each one's WebP. Only some
  // packs are exported as such, so the full list is read from the source of
  // the module that defines them (`{id:`...`,name:...,webp:V}` with
  // `V=`/whiteboards/assets/....webp``), and checked against the exports.
  async function stickerImages() {
    const out = {};
    const isPack = (v) => v && typeof v === 'object' && Array.isArray(v.stickers)
      && v.stickers.some((s) => s && typeof s.id === 'string' && typeof s.webp === 'string');
    const take = (v) => {
      if (isPack(v)) v.stickers.forEach((s) => { if (s && typeof s.webp === 'string') out[s.id] = s.webp; });
      else if (Array.isArray(v)) v.forEach(take);
    };
    for (const url of moduleUrls()) {
      let mod;
      try { mod = await import(url); } catch (_e) { continue; }
      for (const key of Object.keys(mod)) {
        try { take(mod[key]); } catch (_e) { /* an export not yet initialised */ }
      }
      let text;
      try { text = await (await fetch(url)).text(); } catch (_e) { continue; }
      if (!text.includes('spritesheetId:')) continue;
      const paths = {};
      for (const m of text.matchAll(/([A-Za-z0-9_$]+)=`(\/whiteboards\/assets\/[^`]+\.webp)`/g)) paths[m[1]] = m[2];
      for (const m of text.matchAll(/\{id:`([^`]+)`,name:[^,{}]+,webp:([A-Za-z0-9_$]+)[,}]/g)) {
        if (paths[m[2]] && !(m[1] in out)) out[m[1]] = paths[m[2]];
      }
    }
    return out;
  }

  // Each stamp as the editor draws it, by stamp id, as a data URL. The
  // canvas draws stamps from a texture atlas (`stamps.<hash>.webp`), white
  // outline and shadow included, one square per stamp spread over its box;
  // the atlas's frame map (`stamps.data.<hash>.br`, MessagePack) gives each
  // square as [u0, u1, v0, v1], v up from the bottom. Without them a stamp
  // is its bare symbol (`stamp-<id>`) from the editor's icon sprite
  // (`icons.<hash>.svg`), as the picker shows it.
  async function stampImages(ids) {
    const out = {};
    try {
      const texels = await assetNamed(/\/whiteboards\/assets\/stamps\.data\.[A-Za-z0-9_-]+\.br/);
      const atlas = await assetNamed(/\/whiteboards\/assets\/stamps\.[A-Za-z0-9_-]+\.webp/);
      if (texels && atlas) Object.assign(out, await atlasStamps(texels, atlas, ids));
    } catch (_e) { /* the symbols stand in */ }
    const rest = ids.filter((id) => !(id in out));
    const sprite = rest.length ? await assetNamed(/\/whiteboards\/assets\/icons\.[A-Za-z0-9_-]+\.svg/) : null;
    if (!sprite) return out;
    const doc = new DOMParser().parseFromString(await (await fetch(sprite)).text(), 'image/svg+xml');
    for (const id of rest) {
      const symbol = doc.getElementById(`stamp-${id}`);
      if (!symbol) continue;
      const box = symbol.getAttribute('viewBox') || '0 0 46 46';
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${box}">${symbol.innerHTML}</svg>`;
      out[id] = 'data:image/svg+xml;base64,' + base64(new TextEncoder().encode(svg));
    }
    return out;
  }

  async function atlasStamps(texels, atlas, ids) {
    const frames = unpack(new Uint8Array(await (await fetch(texels)).arrayBuffer()));
    const sheet = await createImageBitmap(await (await fetch(atlas)).blob());
    const out = {};
    for (const id of ids) {
      const f = frames[id];
      if (!Array.isArray(f) || f.length !== 4) continue;
      const [u0, u1, v0, v1] = f;  // texel centres: the square reaches half a texel further
      const x = Math.round(u0 * sheet.width - 0.5), w = Math.round(u1 * sheet.width + 0.5) - x;
      const y = Math.round((1 - v1) * sheet.height - 0.5), h = Math.round((1 - v0) * sheet.height + 0.5) - y;
      const canvas = new OffscreenCanvas(w, h);
      canvas.getContext('2d').drawImage(sheet, x, y, w, h, 0, 0, w, h);
      const png = new Uint8Array(await (await canvas.convertToBlob({ type: 'image/png' })).arrayBuffer());
      out[id] = 'data:image/png;base64,' + base64(png);
    }
    return out;
  }

  // Just enough MessagePack for a frame map: maps, arrays, strings, numbers.
  function unpack(bytes) {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    let at = 0;
    const text = (n) => { const s = new TextDecoder().decode(bytes.subarray(at, at + n)); at += n; return s; };
    const items = (n) => { const a = []; for (let i = 0; i < n; i++) a.push(next()); return a; };
    const entries = (n) => { const o = {}; for (let i = 0; i < n; i++) { const k = next(); o[k] = next(); } return o; };
    const read = (n, get) => { at += n; return get.call(view, at - n); };
    function next() {
      const b = bytes[at++];
      if (b <= 0x7f) return b;
      if (b >= 0xe0) return b - 0x100;
      if ((b & 0xf0) === 0x80) return entries(b & 0x0f);
      if ((b & 0xf0) === 0x90) return items(b & 0x0f);
      if ((b & 0xe0) === 0xa0) return text(b & 0x1f);
      switch (b) {
        case 0xc0: return null;
        case 0xc2: return false;
        case 0xc3: return true;
        case 0xca: return read(4, view.getFloat32);
        case 0xcb: return read(8, view.getFloat64);
        case 0xcc: return read(1, view.getUint8);
        case 0xcd: return read(2, view.getUint16);
        case 0xce: return read(4, view.getUint32);
        case 0xd0: return read(1, view.getInt8);
        case 0xd1: return read(2, view.getInt16);
        case 0xd2: return read(4, view.getInt32);
        case 0xd9: return text(read(1, view.getUint8));
        case 0xda: return text(read(2, view.getUint16));
        case 0xdc: return items(read(2, view.getUint16));
        case 0xde: return entries(read(2, view.getUint16));
        default: throw new Error('unexpected MessagePack byte ' + b);
      }
    }
    return next();
  }

  // The first path on the site matching `re` that a module names.
  async function assetNamed(re) {
    for (const url of moduleUrls()) {
      let text;
      try { text = await (await fetch(url)).text(); } catch (_e) { continue; }
      const m = re.exec(text);
      if (m) return m[0];
    }
    return null;
  }

  function base64(bytes) {
    let binary = '';
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    return btoa(binary);
  }

  // Files on the site, as base64, by the key they were asked for under.
  async function download(paths) {
    const out = {};
    for (const [key, path] of Object.entries(paths)) {
      try {
        const res = await fetch(new URL(path, location.href).href);
        if (!res.ok) continue;
        out[key] = base64(new Uint8Array(await res.arrayBuffer()));
      } catch (_e) { /* left as a placeholder */ }
    }
    return out;
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

  globalThis.__whiteboardExporterGraphics = {
    shapeDrawings, libraryIcons, libraryCatalogue, libraryCollections, stickerImages, stampImages, download,
  };
})();
