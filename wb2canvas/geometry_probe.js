// Read the whiteboard editor's drawn geometry: the box each element is
// drawn in and the path each connector and line is drawn along.
//
// Injected into the board's frame by editor.py. Read-only. The stored data
// is not enough: the editor grows a shape to fit its text when it draws it,
// and routes connectors itself, writing neither back for pasted elements.
//
// The editor runs an entity-component system (Becsy). Its World class is
// found among the modules the page has loaded, by shape rather than by its
// minified export name; wrapping World.prototype.execute for one frame
// captures the running world. Boxes come from the positioning engine (kept
// by the bounding-box system), keyed by element id. Paths are components,
// which Becsy only lets a system read while it executes, so they are read
// during one frame of the system that draws paths.
(() => {
  if (globalThis.__wb2canvasGeometry) return;

  async function moduleUrls() {
    return [...new Set([
      ...[...document.querySelectorAll('script[type=module][src]')].map((s) => s.src),
      ...[...document.querySelectorAll('link[rel=modulepreload][href]')].map((l) => l.href),
    ])].filter((u) => /\.js($|\?)/.test(u));
  }

  async function captureWorld(timeoutMs = 5000) {
    if (globalThis.__wb2canvasWorld) return globalThis.__wb2canvasWorld;
    for (const url of await moduleUrls()) {
      let mod;
      try { mod = await import(url); } catch (_e) { continue; }
      for (const World of Object.values(mod)) {
        if (typeof World !== 'function' || World.name !== 'World' || !World.prototype
            || typeof World.prototype.execute !== 'function') continue;
        const original = World.prototype.execute;
        const world = await new Promise((resolve) => {
          World.prototype.execute = function (...args) { resolve(this); return original.apply(this, args); };
          setTimeout(() => resolve(null), timeoutMs);
        });
        World.prototype.execute = original;
        if (!world) throw new Error('the editor did not run a frame');
        globalThis.__wb2canvasWorld = world;
        return world;
      }
    }
    throw new Error("the editor's World was not found among the page's modules");
  }

  function system(world, name) {
    for (const s of world.__dispatcher.systems) {
      const sys = s.system || s;
      if (sys.constructor && sys.constructor.name === name) return sys;
    }
    throw new Error(`the editor has no ${name}`);
  }

  function componentTypes(world) {
    return Object.fromEntries(world.__dispatcher.registry.types.filter(Boolean).map((t) => [t.name, t]));
  }

  const xy = (v) => [Number(v[0]), Number(v[1])];

  function plainSegment(seg) {
    const out = { type: seg.type };
    for (const [k, v] of Object.entries(seg)) {
      if (k === 'type' || k === 'pathSegmentMetadata') continue;
      out[k] = v && typeof v === 'object' && v.length === 2 ? xy(v) : v;
    }
    return out;
  }

  async function readPaths(world, ids, timeoutMs = 5000) {
    const sys = system(world, 'RenderPathSystem');
    const T = componentTypes(world);
    return new Promise((resolve, reject) => {
      const original = sys.execute;
      const timer = setTimeout(() => { sys.execute = original; reject(new Error('no frame ran')); }, timeoutMs);
      sys.execute = function (...args) {
        sys.execute = original;
        clearTimeout(timer);
        const result = original.apply(this, args);
        try {
          const paths = {};
          for (const id of ids) {
            const e = this.getEntityById(id);
            if (!e || !e.has(T.PathComponent)) continue;
            paths[id] = {
              start: xy(e.read(T.PathStartComponent).start),
              end: xy(e.read(T.PathEndComponent).end),
              segments: Array.from(e.read(T.PathComponent).pathSegments, plainSegment),
            };
          }
          resolve(paths);
        } catch (err) {
          reject(err);
        }
        return result;
      };
    });
  }

  async function readAll() {
    const world = await captureWorld();
    const engine = system(world, 'SynchroniseBoundingBoxSystem').positioningEngine;
    const doc = globalThis.__wb2canvas.getDocFromFiber();
    const types = {};
    doc.share.get('board').forEach((el, id) => { types[id] = el.get('t'); });
    const boxes = {};
    for (const id of Object.keys(types)) {
      let b = null;
      try { b = engine.getNodeBounds(id); } catch (_e) { /* not positioned, e.g. a waypoint */ }
      if (b) boxes[id] = [b.left, b.top, b.right, b.bottom];
    }
    const pathIds = Object.keys(types).filter((id) => types[id] === 'connector' || types[id] === 'path');
    const paths = await readPaths(world, pathIds);
    return { types, boxes, paths };
  }

  globalThis.__wb2canvasGeometry = { readAll };
})();
