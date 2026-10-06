// Measure the whiteboard editor's font, for wb2canvas/text_metrics.json.
//
// Paste into the DevTools console of an open Confluence whiteboard (or run
// it in the board's frame). It measures the advance width of each printable
// ASCII character and some common typographic ones in the editor's font,
// regular and semibold, at 100px, and copies the table as JSON. Save it as
// wb2canvas/text_metrics.json. Only widths are kept, never the font.
window.wb2canvasDumpTextMetrics = async () => {
  const frame = [...document.querySelectorAll('iframe')].find((f) => (f.src || '').includes('/whiteboards/whiteboard/'));
  const doc = frame ? frame.contentDocument : document;
  const family = '"Atlassian Sans"';
  const chars = [];
  for (let c = 32; c < 127; c++) chars.push(String.fromCharCode(c));
  chars.push(...'’‘“”–—•…éèàüöä ');
  const ctx = doc.createElement('canvas').getContext('2d');
  const base = 11.6 / 0.75; // paragraph text; headings 13-27px; font scales 0.5-2
  const sizes = [base * 0.5, 11.6, 13, 14, base, 16, 18, 20, 23, base * 1.5, 27, base * 2, 40];
  const table = { family: 'Atlassian Sans', weights: {} };
  for (const weight of [400, 600]) {
    table.weights[weight] = {};
    for (const size of sizes) {
      await doc.fonts.load(`${weight} ${size}px ${family}`);
      ctx.font = `${weight} ${size}px ${family}`;
      const widths = {};
      for (const ch of chars) widths[ch] = Math.round(ctx.measureText(ch).width * 1000) / 1000;
      table.weights[weight][Math.round(size * 1000) / 1000] = widths;
    }
  }
  return JSON.stringify(table);
};

window.wb2canvasDumpTextMetrics().then((json) => {
  if (typeof copy === 'function') {
    copy(json); // DevTools console utility
    console.log(`copied the text metrics (${json.length} bytes)`);
  } else {
    console.log(json);
  }
});
