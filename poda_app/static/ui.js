/* PODA UI runtime: DOM helpers, API client, toasts, dialogs, router, command palette, build identity. */
(function () {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else if (k === 'html') node.innerHTML = v; // only ever receives output of markdown.render or escaped strings
      else if (k === 'style' && typeof v === 'object') Object.assign(node.style, v);
      else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === 'dataset') Object.assign(node.dataset, v);
      else node.setAttribute(k, v === true ? '' : v);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.appendChild(typeof child === 'string' || typeof child === 'number' ? document.createTextNode(String(child)) : child);
    }
    return node;
  }

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function fmtTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }
  function fmtBytes(n) {
    if (n === null || n === undefined) return 'n/a';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let v = Number(n), i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
  }

  // ---------- API ----------
  class ApiError extends Error {
    constructor(status, detail) {
      super(typeof detail === 'string' ? detail : (detail?.message || detail?.detail || `HTTP ${status}`));
      this.status = status; this.detail = detail;
    }
  }
  async function api(path, { method = 'GET', body, signal, raw = false } = {}) {
    const res = await fetch(path, {
      method, signal, credentials: 'same-origin', cache: 'no-store',
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (raw) return res;
    let data = null;
    const text = await res.text();
    try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text.slice(0, 500) }; }
    if (!res.ok) {
      if (res.status === 401) toast('Local session expired. Reload the page to start a new session.', 'danger');
      throw new ApiError(res.status, data?.detail ?? data ?? `HTTP ${res.status}`);
    }
    return data;
  }
  function errorDetail(err) {
    const d = err?.detail;
    if (d && typeof d === 'object') return { code: d.code || null, message: d.message || d.detail || err.message, remedy: d.remedy || null, extra: d };
    return { code: null, message: err?.message || String(err), remedy: null, extra: null };
  }
  function errorNode(err) {
    const d = errorDetail(err);
    return el('div', { class: 'notice notice-danger', role: 'alert' },
      d.code ? el('div', { class: 'mono small faint' }, d.code) : null,
      el('div', {}, d.message),
      d.remedy ? el('div', { class: 'small muted', style: { marginTop: '4px' } }, d.remedy) : null);
  }

  // ---------- Toasts / dialogs ----------
  function toast(message, kind = '', ttl = 4200) {
    const host = $('#toasts') || document.body.appendChild(el('div', { id: 'toasts', class: 'toasts', 'aria-live': 'polite' }));
    const t = el('div', { class: `toast ${kind ? 'toast-' + kind : ''}`, role: 'status' }, message);
    host.appendChild(t);
    setTimeout(() => { t.style.opacity = '0'; t.style.transition = 'opacity 200ms'; setTimeout(() => t.remove(), 220); }, ttl);
    return t;
  }
  function confirmDialog({ title, body, confirmLabel = 'Confirm', danger = false, requireText = null }) {
    return new Promise((resolve) => {
      const dlg = el('dialog', { 'aria-labelledby': 'dlg-title' });
      const input = requireText ? el('input', { class: 'input', placeholder: `Type ${requireText} to continue`, autocomplete: 'off' }) : null;
      const ok = el('button', { class: `btn ${danger ? 'btn-danger' : 'btn-primary'}`, disabled: !!requireText }, confirmLabel);
      const cancel = el('button', { class: 'btn btn-ghost' }, 'Cancel');
      if (input) input.addEventListener('input', () => { ok.disabled = input.value.trim() !== requireText; });
      dlg.append(
        el('div', { class: 'dialog-body' }, el('h2', { id: 'dlg-title' }, title), typeof body === 'string' ? el('p', { class: 'muted' }, body) : body, input),
        el('div', { class: 'dialog-actions' }, cancel, ok));
      document.body.appendChild(dlg);
      const close = (v) => { dlg.close(); dlg.remove(); resolve(v); };
      ok.onclick = () => close(true); cancel.onclick = () => close(false); dlg.oncancel = () => close(false);
      dlg.showModal();
    });
  }
  function promptDialog({ title, body, label = 'Value', placeholder = '', value = '', multiline = false }) {
    return new Promise((resolve) => {
      const dlg = el('dialog');
      const input = multiline ? el('textarea', { class: 'input', placeholder, rows: 5 }) : el('input', { class: 'input', placeholder, value });
      if (multiline) input.value = value;
      const ok = el('button', { class: 'btn btn-primary' }, 'Save');
      const cancel = el('button', { class: 'btn btn-ghost' }, 'Cancel');
      dlg.append(el('div', { class: 'dialog-body' }, el('h2', {}, title), body ? el('p', { class: 'muted small' }, body) : null, el('label', { class: 'label' }, label), input),
        el('div', { class: 'dialog-actions' }, cancel, ok));
      document.body.appendChild(dlg);
      const close = (v) => { dlg.close(); dlg.remove(); resolve(v); };
      ok.onclick = () => close(input.value); cancel.onclick = () => close(null); dlg.oncancel = () => close(null);
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !multiline) { e.preventDefault(); close(input.value); } });
      dlg.showModal(); input.focus();
    });
  }

  // ---------- Skeletons / empty ----------
  function skeleton(lines = 3, block = false) {
    const wrap = el('div', { class: 'stack-sm', 'aria-busy': 'true' });
    if (block) wrap.appendChild(el('div', { class: 'skel skel-block' }));
    for (let i = 0; i < lines; i++) wrap.appendChild(el('div', { class: 'skel skel-line', style: { width: `${92 - i * 14}%` } }));
    return wrap;
  }
  function emptyState(title, body, action) {
    return el('div', { class: 'empty' }, el('strong', {}, title), body ? el('p', { class: 'small' }, body) : null, action || null);
  }
  function pill(text, kind = '') { return el('span', { class: `pill ${kind ? 'pill-' + kind : ''}` }, text); }
  function statusPill(status) {
    const s = String(status || 'DISCONNECTED').toUpperCase();
    return pill(s, s === 'CONNECTED' ? 'ok' : s === 'DEGRADED' ? 'warn' : 'danger');
  }

  // ---------- Router ----------
  const routes = new Map();
  let current = null;
  function route(name, render) { routes.set(name, render); }
  function currentRoute() { return (location.hash.replace(/^#\/?/, '') || 'ask').split('?')[0]; }
  async function renderRoute() {
    const name = currentRoute();
    const fn = routes.get(name) || routes.get('ask');
    const view = $('#view');
    $$('.nav-item').forEach((b) => b.setAttribute('aria-current', b.dataset.route === name ? 'page' : 'false'));
    if (current?.teardown) { try { current.teardown(); } catch { /* ignore */ } }
    view.innerHTML = '';
    const inspector = $('#inspectorBody'); inspector.innerHTML = '';
    $('#inspectorTitle').textContent = 'Inspector';
    const ctx = { view, inspector, setTitle: (t) => { $('#viewTitle').textContent = t; }, setInspectorTitle: (t) => { $('#inspectorTitle').textContent = t; } };
    current = (await fn(ctx)) || {};
    view.scrollTop = 0;
  }
  function navigate(name) { location.hash = `#/${name}`; }

  // ---------- Command palette ----------
  const commands = [];
  function registerCommand(cmd) { commands.push(cmd); }
  function openPalette() {
    if ($('#palette')) return;
    const dlg = el('dialog', { id: 'palette', class: 'palette', 'aria-label': 'Command palette' });
    const input = el('input', { placeholder: 'Type a command or screen name', 'aria-label': 'Command', autocomplete: 'off' });
    const list = el('div', { class: 'palette-list', role: 'listbox' });
    let items = [], selected = 0;
    const draw = () => {
      const q = input.value.trim().toLowerCase();
      items = commands.filter((c) => !q || c.label.toLowerCase().includes(q) || (c.keywords || '').toLowerCase().includes(q)).slice(0, 14);
      selected = Math.min(selected, Math.max(0, items.length - 1));
      list.innerHTML = '';
      items.forEach((c, i) => list.appendChild(el('button', { class: 'palette-item', role: 'option', 'aria-selected': i === selected ? 'true' : 'false', onclick: () => run(c) },
        el('span', {}, c.label), el('span', { class: 'pal-kind' }, c.kind || ''), c.key ? el('span', { class: 'kbd' }, c.key) : null)));
      if (!items.length) list.appendChild(el('div', { class: 'faint small', style: { padding: '12px' } }, 'No matching command'));
    };
    const close = () => { dlg.close(); dlg.remove(); };
    const run = async (c) => { close(); try { await c.run(); } catch (e) { toast(errorDetail(e).message, 'danger'); } };
    input.addEventListener('input', () => { selected = 0; draw(); });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { e.preventDefault(); selected = (selected + 1) % Math.max(1, items.length); draw(); }
      if (e.key === 'ArrowUp') { e.preventDefault(); selected = (selected - 1 + items.length) % Math.max(1, items.length); draw(); }
      if (e.key === 'Enter' && items[selected]) { e.preventDefault(); run(items[selected]); }
    });
    dlg.oncancel = close; dlg.onclick = (e) => { if (e.target === dlg) close(); };
    dlg.append(input, list); document.body.appendChild(dlg); draw(); dlg.showModal(); input.focus();
  }
  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); }
  });

  // ---------- Build identity + cache busting ----------
  const build = { id: null, version: null };
  async function loadBuild() {
    try {
      const b = await api('/system/build');
      build.id = b.build_id; build.version = b.version;
      $$('[data-build]').forEach((n) => { n.textContent = `${b.version} · ${b.build_id}`; });
      const seen = sessionStorage.getItem('poda_build');
      if (seen && seen !== b.build_id) { sessionStorage.setItem('poda_build', b.build_id); location.replace(`${location.pathname}?b=${b.build_id}${location.hash}`); return b; }
      sessionStorage.setItem('poda_build', b.build_id);
    } catch { /* offline or not ready */ }
    return build;
  }
  const bust = (url) => build.id ? `${url}${url.includes('?') ? '&' : '?'}b=${build.id}` : url;

  window.PODA = { $, $$, el, esc, api, ApiError, errorDetail, errorNode, toast, confirmDialog, promptDialog, skeleton, emptyState, pill, statusPill,
    route, renderRoute, navigate, currentRoute, registerCommand, openPalette, loadBuild, build, bust, fmtTime, fmtBytes };
})();
