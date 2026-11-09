/* Minimal, safe markdown for streamed assistant text. Everything is escaped first; only a small, known tag set is produced. */
(function () {
  'use strict';
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function inline(text) {
    let out = esc(text);
    out = out.replace(/`([^`\n]+)`/g, (_, c) => `<code>${c}</code>`);
    out = out.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    out = out.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, '$1<em>$2</em>');
    out = out.replace(/(^|[\s(])_([^_\n]+)_(?=[\s).,;:!?]|$)/g, '$1<em>$2</em>');
    return out;
  }

  function render(src) {
    const lines = String(src ?? '').replace(/\r\n?/g, '\n').split('\n');
    const html = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      const fence = line.match(/^```\s*([\w+-]*)\s*$/);
      if (fence) {
        const lang = fence[1] || '';
        const buf = [];
        i++;
        while (i < lines.length && !/^```\s*$/.test(lines[i])) { buf.push(lines[i]); i++; }
        i++; // closing fence (or EOF while streaming)
        html.push(`<div class="codeblock">${lang ? `<span class="lang">${esc(lang)}</span>` : ''}<button type="button" class="btn btn-sm btn-ghost copy" data-copy>Copy</button><pre><code>${esc(buf.join('\n'))}</code></pre></div>`);
        continue;
      }
      if (/^\s*([-*•]|\d+[.)])\s+/.test(line)) {
        const ordered = /^\s*\d+[.)]\s+/.test(line);
        const items = [];
        while (i < lines.length && /^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*([-*•]|\d+[.)])\s+/, '')); i++; }
        html.push(`<${ordered ? 'ol' : 'ul'}>${items.map((t) => `<li>${inline(t)}</li>`).join('')}</${ordered ? 'ol' : 'ul'}>`);
        continue;
      }
      const heading = line.match(/^(#{1,4})\s+(.*)$/);
      if (heading) { html.push(`<p><strong>${inline(heading[2])}</strong></p>`); i++; continue; }
      if (!line.trim()) { i++; continue; }
      const para = [line];
      i++;
      while (i < lines.length && lines[i].trim() && !/^```/.test(lines[i]) && !/^\s*([-*•]|\d+[.)])\s+/.test(lines[i]) && !/^#{1,4}\s/.test(lines[i])) { para.push(lines[i]); i++; }
      html.push(`<p>${inline(para.join(' '))}</p>`);
    }
    return html.join('');
  }

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-copy]');
    if (!btn) return;
    const code = btn.parentElement.querySelector('pre')?.textContent || '';
    navigator.clipboard?.writeText(code).then(() => { btn.textContent = 'Copied'; setTimeout(() => { btn.textContent = 'Copy'; }, 1400); });
  });

  window.PODAMarkdown = { render, inline, esc };
})();
