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
  window.addEventListener('message', async ({ source, origin, data }) => {
    if (source !== window.parent || origin !== parentOrigin || !Number.isInteger(data?.id)
        || typeof data.diagram !== 'string' || data.diagram.length > 32000) return;
    let svg = null;
    try {
      const result = await engine.render('poda-diagram-' + (++nextId), data.diagram);
      svg = result.svg;
    } catch { /* The chat shows the original source when a diagram is unavailable. */ }
    source.postMessage({ id: data.id, svg }, origin);
  });
  window.parent.postMessage({ diagramReady: Boolean(engine) }, parentOrigin);
})();
