/* Runs only in the opaque, sandboxed diagram frame. */
(() => {
  'use strict';
  const parentOrigin = new URL(document.baseURI).origin;
  let nextId = 0;
  const engine = window.mermaid;
  if (engine) {
    engine.initialize({
      startOnLoad: false, securityLevel: 'strict', theme: 'base', htmlLabels: false,
      themeVariables: {
        darkMode: true, background: '#0d1424', primaryColor: '#14253b', primaryTextColor: '#e5efff',
        primaryBorderColor: '#4a80ad', lineColor: '#97b3d1', secondaryColor: '#222b46',
        tertiaryColor: '#182235', fontFamily: 'system-ui, sans-serif',
      },
      maxTextSize: 32000, maxEdges: 500, suppressErrorRendering: true,
    });
  }
  // Preserve the model's original source. Retry a common flowchart syntax error:
  // Mermaid requires troublesome characters such as parentheses to be quoted in node labels.
  function quoteFlowchartLabels(text) {
    if (!/^\s*(?:flowchart|graph)\b/im.test(text)) return text;
    return text.replace(/\b([A-Za-z_][\w-]*)\[([^\[\]"\n]+)\]/g, (full, id, label) => {
      if (!/[(){}]/.test(label)) return full;
      return `${id}["${label.trim().replace(/&/g, '&amp;')}"]`;
    });
  }
  function readableClassColors(text) {
    return text.replace(/^(\s*classDef\s+[\w, -]+\s+)([^;\n]+)(;?)/gm, (full, prefix, style, suffix) => {
      if (/(?:^|,)\s*color\s*:/.test(style)) return full;
      const match = style.match(/(?:^|,)\s*fill\s*:\s*#([0-9a-f]{3}|[0-9a-f]{6})(?=\s*(?:,|$))/i);
      if (!match) return full;
      const hex = match[1].length === 3 ? [...match[1]].map(c => c + c).join('') : match[1];
      const rgb = [0, 2, 4].map(i => parseInt(hex.slice(i, i + 2), 16));
      const brightness = .2126 * rgb[0] + .7152 * rgb[1] + .0722 * rgb[2];
      return prefix + style + (brightness > 155 ? ',color:#111827' : ',color:#f3f6ff') + suffix;
    });
  }
  window.addEventListener('message', async ({ source, origin, data }) => {
    if (source !== window.parent || origin !== parentOrigin || !Number.isInteger(data?.id)
        || typeof data.diagram !== 'string' || data.diagram.length > 32000) return;
    let svg = null;
    try {
      const result = await engine.render('poda-diagram-' + (++nextId), readableClassColors(data.diagram));
      svg = result.svg;
    } catch {
      const repaired = quoteFlowchartLabels(data.diagram);
      if (repaired !== data.diagram) {
        try { svg = (await engine.render('poda-diagram-' + (++nextId), readableClassColors(repaired))).svg; }
        catch { /* Keep the source visible if a conservative repair cannot parse it. */ }
      }
    }
    source.postMessage({ id: data.id, svg }, origin);
  });
  window.parent.postMessage({ diagramReady: Boolean(engine) }, parentOrigin);
})();
