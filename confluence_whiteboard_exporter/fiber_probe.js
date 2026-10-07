(() => {
  if (globalThis.__whiteboardExporter) return;

  const _state = { capturedClipboard: null };

  function getDocFromFiber() {
    const app = document.getElementById('app');
    if (!app) return null;
    const fkey = Object.keys(app).find((k) => k.startsWith('__reactContainer'));
    if (!fkey) return null;
    const root = app[fkey].stateNode?.current || app[fkey];
    let doc = null;
    const seen = new WeakSet();
    const isDoc = (o) =>
      o && typeof o === 'object' && typeof o.transact === 'function' && typeof o.getMap === 'function';
    const visit = (o, depth) => {
      if (doc || !o || typeof o !== 'object' || depth > 8 || seen.has(o)) return;
      seen.add(o);
      if (isDoc(o)) { doc = o; return; }
      for (const k of Object.keys(o)) {
        try { visit(o[k], depth + 1); if (doc) return; } catch (_e) {}
      }
    };
    const walk = (n, d) => {
      if (!n || d > 60 || doc) return;
      try {
        visit(n.memoizedState, 0);
        visit(n.memoizedProps, 0);
        visit(n.stateNode, 0);
      } catch (_e) {}
      walk(n.child, d + 1);
      walk(n.sibling, d);
    };
    walk(root, 0);
    return doc;
  }

  function docReady() {
    const doc = getDocFromFiber();
    if (!doc) return false;
    try {
      const board = doc.share && doc.share.get('board');
      return !!board;
    } catch (_e) { return false; }
  }

  function boardSize() {
    const doc = getDocFromFiber();
    if (!doc) return -1;
    const board = doc.share && doc.share.get('board');
    return board ? board.size : -1;
  }

  function imageCount() {
    const doc = getDocFromFiber();
    const board = doc && doc.share && doc.share.get('board');
    if (!board) return -1;
    let n = 0;
    board.forEach((el) => { if (el && typeof el.get === 'function' && el.get('t') === 'image') n++; });
    return n;
  }

  function dumpYDoc() {
    const doc = getDocFromFiber();
    if (!doc) throw new Error('Yjs Doc not found in React fiber');
    const board = doc.share.get('board');
    const dimensions = doc.share.get('dimensions');
    const zindex = doc.share.get('zindex');
    const meta = doc.share.get('meta');
    return {
      board: board ? board.toJSON() : {},
      dimensions: dimensions ? dimensions.toArray() : [],
      zindex: zindex ? zindex.toArray() : [],
      meta: meta ? meta.toJSON() : null,
    };
  }

  function installClipboardCapture() {
    _state.capturedClipboard = null;
    if (_state.handler) {
      document.removeEventListener('copy', _state.handler, false);
    }
    const handler = (ev) => {
      try {
        const html = ev.clipboardData ? ev.clipboardData.getData('text/html') : '';
        if (!html) return;
        const m = html.match(/data-canvas-clipboard="([^"]+)"/);
        if (!m) return;
        // atob returns a binary string (each char = one byte). The payload is
        // UTF-8 encoded, so we must re-decode bytes through TextDecoder; doing
        // JSON.parse on the binary string would mojibake any non-ASCII text
        // (e.g. typographic apostrophe -> "â€™").
        const bin = atob(m[1]);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const json = new TextDecoder('utf-8').decode(bytes);
        _state.capturedClipboard = JSON.parse(json);
      } catch (e) {
        _state.capturedClipboard = { __error: String(e && e.message) };
      }
    };
    document.addEventListener('copy', handler, false);
    _state.handler = handler;
  }

  function resetCapture() {
    _state.capturedClipboard = null;
  }

  function getCapturedClipboard() {
    return _state.capturedClipboard;
  }

  function dispatchSelectAllAndCopy() {
    const opts = (key, code) => ({
      key, code, bubbles: true, cancelable: true,
      ctrlKey: true, metaKey: true,
    });
    document.dispatchEvent(new KeyboardEvent('keydown', opts('a', 'KeyA')));
    document.dispatchEvent(new KeyboardEvent('keyup', opts('a', 'KeyA')));
    setTimeout(() => {
      document.dispatchEvent(new KeyboardEvent('keydown', opts('c', 'KeyC')));
      document.dispatchEvent(new KeyboardEvent('keyup', opts('c', 'KeyC')));
      try { document.execCommand('copy'); } catch (_e) {}
    }, 80);
  }

  function focusCanvas() {
    const c = document.getElementById('canvas-main');
    if (c && typeof c.focus === 'function') c.focus();
    if (document.body && typeof document.body.focus === 'function') document.body.focus();
  }

  globalThis.__whiteboardExporter = {
    getDocFromFiber,
    docReady,
    boardSize,
    imageCount,
    dumpYDoc,
    installClipboardCapture,
    resetCapture,
    getCapturedClipboard,
    focusCanvas,
    dispatchSelectAllAndCopy,
  };
})();
