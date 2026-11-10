/* Render assistant replies as inert, local previews. Downloads stay independent of the preview. */
(() => {
  'use strict';
  const { el } = window.PODA;
  const { esc, render: plain } = window.PODAMarkdown;
  const vendor = '/static/chat-vendor/';
  const MAX_SOURCE = 32000;
  const MAX_IMAGE = 16 * 1024 * 1024;
  const imageTypes = {
    png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif',
    webp: 'image/webp', avif: 'image/avif', svg: 'image/svg+xml',
  };
  const imageType = (filename) => Object.hasOwn(imageTypes, filename.split('.').pop().toLowerCase())
    ? imageTypes[filename.split('.').pop().toLowerCase()] : undefined;
  const sandboxURL = (filename) => '/agent/sandbox/download/' + encodeURIComponent(filename);
  const markup = window.markdownit?.({ html: false });
  const loads = new Map();
  const previous = new WeakMap();
  const imageURLs = new Map();
  let imageObserver;

  function loadAsset(name, stylesheet = false) {
    if (!loads.has(name)) {
      loads.set(name, new Promise((resolve, reject) => {
        const node = stylesheet ? el('link', { rel: 'stylesheet', href: vendor + name })
          : el('script', { src: vendor + name });
        node.onload = resolve;
        node.onerror = reject;
        document.head.append(node);
      }));
    }
    return loads.get(name);
  }

  function codeBlock(source, language, preview = '') {
    return `<div class="codeblock"${preview}>${language ? `<span class="lang">${esc(language)}</span>` : ''}<button type="button" class="btn btn-sm btn-ghost copy" data-copy>Copy</button><pre><code>${esc(source)}</code></pre></div>`;
  }

  function mathInline(state, silent) {
    const start = state.pos;
    const latex = state.src.startsWith('\\(', start);
    const dollar = state.src[start] === '$' && !/[\s$]/.test(state.src[start + 1] || ' ');
    if (!latex && !dollar) return false;
    const open = latex ? '\\(' : '$';
    const close = latex ? '\\)' : '$';
    let end = state.src.indexOf(close, start + open.length);
    while (end >= 0 && state.src[end - 1] === '\\') end = state.src.indexOf(close, end + close.length);
    if (end <= start + open.length || end + close.length > state.posMax) return false;
    const content = state.src.slice(start + open.length, end);
    if (content.includes('\n') || (dollar && (/\s$/.test(content) || /[\d$]/.test(state.src[end + 1] || '')))) return false;
    if (!silent) {
      const token = state.push('poda_math_inline', '', 0);
      token.content = content;
      token.markup = open + close;
    }
    state.pos = end + close.length;
    return true;
  }

  function mathBlock(state, start, end, silent) {
    if (state.sCount[start] - state.blkIndent >= 4) return false;
    const line = (n) => state.src.slice(state.bMarks[n] + state.tShift[n], state.eMarks[n]);
    const first = line(start);
    const open = first.startsWith('$$') ? '$$' : first.startsWith('\\[') ? '\\[' : '';
    if (!open) return false;
    const close = open === '$$' ? '$$' : '\\]';
    const parts = [];
    let last = start;
    let current = first.slice(open.length).trimEnd();
    while (!current.endsWith(close)) {
      if (current.includes(close) || ++last === end) return false;
      parts.push(current);
      current = line(last).trimEnd();
      if (/^(?:`{3,}|~{3,})/.test(current.trimStart())) return false;
    }
    parts.push(current.slice(0, -close.length));
    const content = parts.join('\n').trim();
    if (!content) return false;
    if (silent) return true;
    state.push('poda_math_block', '', 0).content = content;
    state.line = last + 1;
    return true;
  }

  function equation(source, display, fallback) {
    const tag = display ? 'div' : 'span';
    return `<${tag} class="chat-math${display ? ' chat-math-block' : ''}" data-preview="math" data-equation="${esc(source)}">${esc(fallback)}</${tag}>`;
  }

  function localImageURL(src) {
    const match = /^\/agent\/sandbox\/download\/([^/?#]+)$/.exec(src);
    if (!match) return null;
    try {
      const filename = decodeURIComponent(match[1]);
      return !/[\\/]/.test(filename) && imageType(filename) ? filename : null;
    } catch { return null; }
  }

  if (markup) {
    markup.inline.ruler.before('escape', 'poda_math_inline', mathInline);
    markup.block.ruler.before('fence', 'poda_math_block', mathBlock, { alt: ['paragraph'] });
    markup.renderer.rules.poda_math_inline = (tokens, i) => {
      const { content, markup: delimiters } = tokens[i];
      const middle = delimiters.length / 2;
      return equation(content, false, delimiters.slice(0, middle) + content + delimiters.slice(middle));
    };
    markup.renderer.rules.poda_math_block = (tokens, i) => equation(tokens[i].content, true, tokens[i].content);
    markup.renderer.rules.fence = (tokens, i, options, env) => {
      const token = tokens[i];
      const language = token.info.trim().split(/\s+/)[0].toLowerCase();
      if (['mermaid', 'svg', 'math', 'latex', 'tex'].includes(language)) {
        const closing = env.lines[token.map[1] - 1]?.trim().replace(/^(?:>\s*)+/, '');
        const delimiter = token.markup[0] === '`' ? '`' : '~';
        const closed = token.map[1] - token.map[0] > 1
          && new RegExp('^' + delimiter + '{' + token.markup.length + ',}$').test(closing || '');
        if (closed && ['math', 'latex', 'tex'].includes(language)) return equation(token.content, true, token.content);
        if (!closed && env.streaming) return codeBlock(token.content, language);
        if (language === 'mermaid' || language === 'svg') {
          return `<div class="chat-visual" data-preview="${language}"><div class="chat-visual-image" hidden></div><p class="chat-visual-status muted small" role="status">Preparing ${language === 'mermaid' ? 'diagram' : 'image'}…</p><details><summary>View source</summary>${codeBlock(token.content, language)}</details></div>`;
        }
      }
      return codeBlock(token.content, language, language ? ` data-preview="code" data-language="${esc(language)}"` : '');
    };
    markup.renderer.rules.image = (tokens, i, options, env, renderer) => {
      const token = tokens[i];
      const alt = renderer.renderInlineAsText(token.children || [], options, env);
      const filename = localImageURL(token.attrGet('src') || '');
      return filename
        ? `<span class="chat-visual chat-visual-inline" data-preview="image" data-filename="${esc(filename)}" data-alt="${esc(alt)}"><span class="chat-visual-image" hidden></span><span class="chat-visual-status small muted" role="status">Loading image…</span></span>`
        : `<span class="small muted">Image unavailable${alt ? ' · ' + esc(alt) : ''}</span>`;
    };
    markup.renderer.rules.link_open = (tokens, i, options, env, renderer) => {
      if (/^(?:https?:)?\/\//i.test(tokens[i].attrGet('href') || '')) {
        tokens[i].attrSet('target', '_blank');
        tokens[i].attrSet('rel', 'noopener noreferrer');
      }
      return renderer.renderToken(tokens, i, options);
    };
    markup.renderer.rules.table_open = () => '<div class="table-scroll" tabindex="0"><table>';
    markup.renderer.rules.table_close = () => '</table></div>';
  }

  let diagramFrame;
  let diagramSequence = 0;
  const pendingDiagrams = new Map();

  function startDiagramFrame() {
    const frame = el('iframe', { title: 'Diagram renderer', sandbox: 'allow-scripts', class: 'chat-render-frame',
      'aria-hidden': 'true', tabindex: -1 });
    // The opaque frame has its own CSP and cannot read application DOM or storage.
    frame.src = '/static/chat-diagram-frame.html';
    const ready = new Promise((resolve, reject) => {
      function finish(error) {
        window.removeEventListener('message', onReady);
        clearTimeout(timer);
        if (error) { frame.remove(); reject(error); }
        else resolve(frame);
      }
      function onReady(event) {
        if (event.source === frame.contentWindow && event.origin === 'null' && typeof event.data?.diagramReady === 'boolean') {
          finish(event.data.diagramReady ? null : new Error('Diagram renderer unavailable'));
        }
      }
      const timer = setTimeout(() => finish(new Error('Diagram renderer timed out')), 12000);
      window.addEventListener('message', onReady);
      document.body.append(frame);
    });
    window.addEventListener('message', (event) => {
      if (event.source !== frame.contentWindow || event.origin !== 'null') return;
      pendingDiagrams.get(event.data?.id)?.(event.data.svg);
    });
    return ready;
  }

  async function renderDiagram(source) {
    const frame = await (diagramFrame ||= startDiagramFrame());
    return new Promise((resolve, reject) => {
      const id = ++diagramSequence;
      const finish = (svg) => {
        clearTimeout(timer);
        pendingDiagrams.delete(id);
        if (typeof svg === 'string' && svg.length <= MAX_IMAGE) resolve(svg);
        else reject(new Error('Diagram source could not be rendered'));
      };
      const timer = setTimeout(() => finish(null), 15000);
      pendingDiagrams.set(id, finish);
      frame.contentWindow.postMessage({ id, diagram: source }, '*');
    });
  }

  function trackImageURL(image, url) {
    if (!imageObserver) {
      imageObserver = new MutationObserver((records) => {
        if (!records.some((record) => record.removedNodes.length)) return;
        for (const [img, objectURL] of imageURLs) {
          if (!img.isConnected) {
            URL.revokeObjectURL(objectURL);
            imageURLs.delete(img);
          }
        }
      });
      imageObserver.observe(document.body, { childList: true, subtree: true });
    }
    imageURLs.set(image, url);
  }

  function showImage(node, blob, alt, diagramWidth = 0) {
    return new Promise((resolve, reject) => {
      if (!node.isConnected) { resolve(); return; }
      const status = node.querySelector('.chat-visual-status');
      const host = node.querySelector('.chat-visual-image');
      const image = el('img', { alt, decoding: 'async' });
      if (diagramWidth) {
        image.width = Math.min(2400, Math.ceil(diagramWidth));
        host.tabIndex = 0;
      }
      const url = URL.createObjectURL(blob);
      image.onload = () => {
        image.onload = null;
        status.hidden = true;
        host.hidden = false;
        node.classList.add('preview-ready');
        node.dispatchEvent(new Event('poda:preview-ready', { bubbles: true }));
        resolve();
      };
      image.onerror = () => { image.remove(); reject(new Error('Invalid image')); };
      host.append(image);
      trackImageURL(image, url);
      image.src = url;
    });
  }

  async function enhance(node) {
    const type = node.dataset.preview;
    const code = node.querySelector('pre code');
    const source = code?.textContent || node.dataset.equation || '';
    try {
      if (type === 'math') {
        if (source.length > MAX_SOURCE) return;
        await Promise.all([loadAsset('katex.js'), loadAsset('katex.css', true)]);
        window.katex.render(source, node, { displayMode: node.classList.contains('chat-math-block'),
          trust: false, throwOnError: true, maxExpand: 500, maxSize: 20 });
        node.dispatchEvent(new Event('poda:preview-ready', { bubbles: true }));
        return;
      }
      if (type === 'code') {
        if (source.length > MAX_SOURCE) return;
        await Promise.all([loadAsset('highlight.js'), loadAsset('highlight.css', true)]);
        if (window.hljs.getLanguage(node.dataset.language)) {
          code.innerHTML = window.hljs.highlight(source, { language: node.dataset.language, ignoreIllegals: true }).value;
        }
        return;
      }
      if (type === 'image') {
        const { filename, alt } = node.dataset;
        const response = await fetch(sandboxURL(filename), { redirect: 'error' });
        if (!response.ok || Number(response.headers.get('content-length')) > MAX_IMAGE) throw new Error('Unavailable image');
        const bytes = await response.arrayBuffer();
        if (bytes.byteLength > MAX_IMAGE) throw new Error('Image is too large');
        await showImage(node, new Blob([bytes], { type: imageType(filename) }), alt || filename);
        return;
      }
      if (!source || source.length > MAX_SOURCE) throw new Error('Preview source is too large');
      const svg = type === 'mermaid' ? await renderDiagram(source) : source;
      const doc = new DOMParser().parseFromString(svg, 'image/svg+xml');
      const root = doc.documentElement;
      if (root.localName !== 'svg' || root.namespaceURI !== 'http://www.w3.org/2000/svg') throw new Error('Invalid SVG');
      const title = root.querySelector('title')?.textContent?.trim();
      const width = type === 'mermaid' ? Number(root.getAttribute('viewBox')?.trim().split(/[ ,]+/)[2]) : 0;
      await showImage(node, new Blob([svg], { type: 'image/svg+xml' }),
        title || (type === 'mermaid' ? 'Mermaid diagram. View source for the diagram text.' : 'Generated SVG image'),
        Number.isFinite(width) ? width : 0);
    } catch {
      const status = node.querySelector('.chat-visual-status');
      if (status) status.textContent = type === 'image' ? 'Image preview unavailable.' :
        `This ${type === 'mermaid' ? 'diagram' : 'image'} could not be rendered. You can still view its source.`;
      if (type === 'math' && !node.textContent) node.textContent = source;
    }
  }

  function renderInto(target, source, { streaming = false } = {}) {
    const state = previous.get(target);
    if (state?.source === source && state.streaming === streaming) return;
    const template = document.createElement('template');
    template.innerHTML = markup
      ? markup.render(source, { streaming, lines: source.replace(/\r\n?/g, '\n').split('\n') })
      : plain(source);
    const visuals = [...template.content.querySelectorAll('[data-preview]')].map((node, i) => {
      const signature = node.outerHTML;
      const existing = state?.visuals[i];
      if (existing?.signature === signature) {
        node.replaceWith(existing.node);
        return existing;
      }
      return { signature, node, started: false };
    });
    target.replaceChildren(template.content);
    previous.set(target, { source, streaming, visuals });
    for (const item of visuals) {
      if (!item.started) { item.started = true; void enhance(item.node); }
    }
  }

  function attachmentPreview(filename) {
    if (!imageType(filename)) return null;
    const preview = el('figure', { class: 'chat-visual chat-attachment', 'data-preview': 'image',
      'data-filename': filename, 'data-alt': filename },
    el('div', { class: 'chat-visual-image', hidden: true }),
    el('p', { class: 'chat-visual-status muted small', role: 'status' }, 'Loading image…'),
    el('figcaption', { class: 'small muted' }, filename));
    // Callers insert the figure synchronously, even when building a saved conversation.
    queueMicrotask(() => { if (preview.isConnected) void enhance(preview); });
    return preview;
  }

  window.PODAChatRendering = { renderInto, attachmentPreview };
})();
