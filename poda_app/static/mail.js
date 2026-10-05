/* PODA Mail screen: Apple Mail + Gmail IMAP connections, mailbox browser, reading pane, receipted actions, drafts,
   commitment extraction. Email bodies are rendered as escaped text only; nothing from a message is ever interpreted
   as markup or as an instruction. */
(function () {
  'use strict';
  const { $, $$, el, api, errorDetail, errorNode, toast, confirmDialog, skeleton, emptyState, pill, route, fmtTime, fmtBytes, navigate } = window.PODA;

  const state = {
    status: null, accountId: null, mailbox: 'INBOX', offset: 0, limit: 50, total: 0, unread: 0, messages: [], selected: null,
    unreadOnly: false, query: '', loadingList: false, receipts: [], extraction: null, mailboxes: [],
  };

  // ---------- helpers ----------
  function section(title, meta, ...children) {
    return el('section', { class: 'card stack' }, el('div', { class: 'card-head' }, el('h2', {}, title), meta ? el('span', { class: 'meta' }, meta) : null), ...children);
  }
  function kv(pairs) {
    const dl = el('dl', { class: 'kv' });
    for (const [k, v] of pairs) dl.append(el('dt', {}, k), el('dd', {}, v === null || v === undefined || v === '' ? el('span', { class: 'faint' }, 'none') : (v instanceof Node ? v : String(v))));
    return dl;
  }
  function fmtRelative(iso) {
    if (!iso) return '';
    const d = new Date(iso); if (Number.isNaN(d.getTime())) return String(iso);
    const now = new Date(); const sameDay = d.toDateString() === now.toDateString();
    if (sameDay) return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    const days = (now - d) / 86400000;
    if (days < 6) return d.toLocaleDateString(undefined, { weekday: 'short' });
    if (d.getFullYear() === now.getFullYear()) return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  }
  function initials(name, addr) { const s = String(name || addr || '?').trim(); const parts = s.split(/\s+/); return ((parts[0]?.[0] || '') + (parts[1]?.[0] || '')).toUpperCase() || '?'; }
  function hue(s) { let h = 0; for (const ch of String(s || '')) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return h % 360; }
  const URL_RE = /(https?:\/\/[^\s<>"')\]]+)/g;
  function bodyNode(text) {
    // Escape-first: text nodes only. URLs become inert spans (never anchors) so nothing in a message is clickable.
    const wrap = el('div', { class: 'mail-body' });
    const parts = String(text || '').split(URL_RE);
    parts.forEach((part, i) => { if (!part) return; if (i % 2 === 1) wrap.appendChild(el('span', { class: 'mail-url', title: 'Link shown as text. PODA never opens links from email.' }, part)); else wrap.appendChild(document.createTextNode(part)); });
    return wrap;
  }
  function receiptChip(rc) {
    const ok = ['success', 'succeeded', 'verified', 'ok'].includes(String(rc?.outcome || '').toLowerCase());
    return el('span', { class: 'receipt-chip', title: rc?.detail || '' }, pill(rc?.outcome || 'receipt', ok ? 'ok' : rc?.outcome === 'blocked' ? 'warn' : rc?.outcome === 'failed' ? 'danger' : 'accent'), el('span', {}, rc?.tool || rc?.action || 'action'), rc?.verification ? el('span', { class: 'mono' }, rc.verification) : null, rc?.id ? el('span', { class: 'mono' }, `#${String(rc.id).slice(0, 8)}`) : null);
  }
  // Apple Mail answers one AppleScript request at a time, so PODA issues mail requests one at a time too:
  // interactive loads (mailboxes, list, open message) jump the queue; snippet batches run in the background.
  const queue = { front: [], back: [], running: false };
  function enqueue(fn, { front = false } = {}) {
    return new Promise((resolve, reject) => { (front ? queue.front : queue.back).push({ fn, resolve, reject }); pump(); });
  }
  async function pump() {
    if (queue.running) return; queue.running = true;
    try {
      while (queue.front.length || queue.back.length) {
        const task = queue.front.length ? queue.front.shift() : queue.back.shift();
        try { task.resolve(await task.fn()); } catch (err) { task.reject(err); }
      }
    } finally { queue.running = false; }
  }
  const accountById = (id) => (state.status?.accounts || []).find((a) => a.id === id) || null;
  const currentAccount = () => accountById(state.accountId);

  // =====================================================================
  route('mail', async ({ view, inspector, setTitle, setInspectorTitle }) => {
    setTitle('Mail'); setInspectorTitle('Permissions and receipts'); $('#viewCrumb').textContent = 'Apple Mail and Gmail, read first, edit with receipts';

    const connBox = el('div', { class: 'stack-sm' });
    const boxesBox = el('div', { class: 'stack-sm' });
    const listHead = el('div', { class: 'mail-list-head' });
    const listBox = el('div', { class: 'mail-list', role: 'listbox', 'aria-label': 'Messages' });
    const readBox = el('div', { class: 'mail-read' });
    const permBox = el('div', { class: 'stack-sm' });
    const receiptsBox = el('div', { class: 'stack-sm' });
    const extractBox = el('div', { class: 'stack-sm' });

    view.appendChild(el('div', { class: 'screen mail-screen' },
      el('aside', { class: 'mail-left stack' }, section('Connections', 'read first; edit per account', connBox), section('Mailboxes', null, boxesBox)),
      el('div', { class: 'mail-center' }, el('section', { class: 'card mail-list-card' }, listHead, listBox), el('section', { class: 'card mail-read-card' }, readBox))));
    inspector.append(el('div', { class: 'stack-sm' }, el('h3', {}, 'Permissions'), permBox), el('div', { class: 'divider' }), el('div', { class: 'stack-sm' }, el('h3', {}, 'Extraction'), extractBox), el('div', { class: 'divider' }), el('div', { class: 'stack-sm' }, el('h3', {}, 'Recent mail receipts'), receiptsBox));
    extractBox.appendChild(el('p', { class: 'faint small' }, 'Open a message and press Extract commitments. Results are unconfirmed until you accept them.'));

    // ---------- status / connections ----------
    async function refreshStatus() {
      try { state.status = await api('/mail/status'); }
      catch (err) { state.status = { error: err, accounts: [], providers: {} }; }
      const accounts = state.status.accounts || [];
      if (!state.accountId || !accountById(state.accountId)) state.accountId = accounts.find((a) => a.enabled !== false)?.id || null;
      drawConnections(); drawPermissions(); drawListHead();
      loadReceipts();
      if (state.accountId) { await drawMailboxes(); loadMessages(true); }
      else { boxesBox.innerHTML = ''; boxesBox.appendChild(el('p', { class: 'faint small' }, 'Connect an account to list its mailboxes.')); listBox.innerHTML = ''; listBox.appendChild(emptyState('No mail account connected', 'Connect Apple Mail or a Gmail account on the left. PODA reads nothing until you do.')); drawRead(null); }
    }

    function providerCard(name, p, body) {
      return el('div', { class: 'mail-provider' }, el('div', { class: 'row-between' }, el('strong', {}, name), el('div', { class: 'row' }, ...body.pills)), ...body.children);
    }

    function drawConnections() {
      connBox.innerHTML = '';
      const st = state.status || {};
      if (st.error) { connBox.appendChild(errorNode(st.error)); connBox.appendChild(el('p', { class: 'faint small' }, 'The mail connector endpoints are not available yet. Everything below stays disabled until the backend responds.')); }
      const providers = st.providers || {};
      const apple = providers.apple_mail || {};
      const gmail = providers.gmail_imap || {};
      const accounts = st.accounts || [];

      // Apple Mail
      const detected = apple.accounts_detected || [];
      const connectedNames = new Set(accounts.filter((a) => a.provider === 'apple_mail').map((a) => a.account_name));
      const appleOut = el('div');
      const checks = detected.map((d) => el('label', { class: 'check mail-acct-check' }, el('input', { type: 'checkbox', name: 'apple-acct', value: d.account_name, checked: !connectedNames.has(d.account_name) && d.enabled !== false, disabled: connectedNames.has(d.account_name) }), el('span', {}, d.account_name, ' ', el('span', { class: 'faint small mono' }, d.address_masked || d.provider_type || ''), connectedNames.has(d.account_name) ? pill('connected', 'ok') : null)));
      const allowModify = el('label', { class: 'check' }, el('input', { type: 'checkbox', name: 'apple-modify' }), 'Allow edits (read state, flags, move, archive, drafts)');
      const allowSend = el('label', { class: 'check' }, el('input', { type: 'checkbox', name: 'apple-send' }), 'Allow sending (each send still asks you)');
      const appleBtn = el('button', { class: 'btn btn-primary btn-sm', disabled: !!st.error || !apple.available }, 'Connect selected accounts');
      appleBtn.onclick = async () => {
        const names = checks.filter((c) => c.querySelector('input').checked && !c.querySelector('input').disabled).map((c) => c.querySelector('input').value);
        appleBtn.disabled = true; appleOut.innerHTML = '';
        try {
          const r = await api('/mail/apple/connect', { method: 'POST', body: { account_names: names, allow_modify: allowModify.querySelector('input').checked, allow_send: allowSend.querySelector('input').checked, auto_launch: true } });
          toast(`Apple Mail connected (${(r.accounts || []).length} account${(r.accounts || []).length === 1 ? '' : 's'})`, 'ok'); await refreshStatus();
        } catch (err) {
          const d = errorDetail(err);
          appleOut.appendChild(errorNode(err));
          if (d.code === 'AUTOMATION_DENIED') appleOut.appendChild(el('div', { class: 'notice notice-warn' }, el('strong', {}, 'macOS blocked automation. '), 'Open System Settings, then Privacy & Security, then Automation, and allow the app that launched PODA (usually Terminal) to control Mail. Then press Connect again.'));
          if (d.code === 'MAIL_NOT_RUNNING') appleOut.appendChild(el('div', { class: 'notice notice-warn' }, 'Open the Mail app, then press Connect again.'));
        } finally { appleBtn.disabled = false; }
      };
      connBox.appendChild(providerCard('Apple Mail', apple, {
        pills: [pill(apple.available ? 'installed' : 'not found', apple.available ? 'ok' : 'danger'), pill(apple.authorized ? 'automation allowed' : 'automation unknown', apple.authorized ? 'ok' : 'warn'), apple.running ? pill('running', 'muted') : pill('not running', 'muted')],
        children: [
          el('p', { class: 'small muted' }, 'Uses the accounts already signed in to Mail (including Gmail) through macOS automation. No password is handled by PODA.'),
          apple.error ? el('div', { class: 'notice notice-warn small' }, String(apple.error)) : null,
          detected.length ? el('div', { class: 'stack-sm' }, ...checks) : el('p', { class: 'faint small' }, apple.available ? 'No accounts detected yet. Make sure Mail is running and automation is allowed.' : 'Apple Mail is not available on this Mac.'),
          allowModify, allowSend, el('div', { class: 'row' }, appleBtn), appleOut],
      }));

      // Gmail IMAP
      const addr = el('input', { class: 'input', type: 'email', placeholder: 'you@gmail.com', autocomplete: 'off', 'aria-label': 'Gmail address' });
      const pwd = el('input', { class: 'input', type: 'password', placeholder: '16-character app password', autocomplete: 'new-password', 'aria-label': 'Gmail app password' });
      const gModify = el('label', { class: 'check' }, el('input', { type: 'checkbox' }), 'Allow edits');
      const gSend = el('label', { class: 'check' }, el('input', { type: 'checkbox' }), 'Allow sending');
      const gOut = el('div');
      const gBtn = el('button', { class: 'btn btn-sm', disabled: !!st.error }, 'Connect Gmail directly');
      gBtn.onclick = async () => {
        const address = addr.value.trim(), app_password = pwd.value; if (!address || !app_password) { toast('Address and app password are required', 'danger'); return; }
        gBtn.disabled = true; gOut.innerHTML = '';
        try { await api('/mail/gmail/connect', { method: 'POST', body: { address, app_password, allow_modify: gModify.querySelector('input').checked, allow_send: gSend.querySelector('input').checked } }); toast('Gmail connected. App password stored in Keychain.', 'ok'); await refreshStatus(); }
        catch (err) { const d = errorDetail(err); gOut.appendChild(errorNode(err)); if (d.code === 'INVALID_CREDENTIALS') gOut.appendChild(el('div', { class: 'notice notice-warn small' }, 'Gmail IMAP needs an App Password (Google Account, then Security, then 2-Step Verification, then App passwords). Your normal password will not work.')); }
        finally { pwd.value = ''; gBtn.disabled = false; }
      };
      connBox.appendChild(providerCard('Gmail (IMAP)', gmail, {
        pills: [pill(gmail.configured ? 'configured' : 'not configured', gmail.configured ? 'ok' : 'muted'), gmail.address_masked ? pill(gmail.address_masked, 'muted') : null],
        children: [
          el('p', { class: 'small muted' }, 'Optional direct connection when Mail is not running. The app password goes to macOS Keychain and is never shown again.'),
          gmail.error ? el('div', { class: 'notice notice-warn small' }, String(gmail.error)) : null,
          el('div', { class: 'field' }, el('label', { class: 'label' }, 'Address'), addr), el('div', { class: 'field' }, el('label', { class: 'label' }, 'App password'), pwd),
          el('div', { class: 'row' }, gModify, gSend), el('div', { class: 'row' }, gBtn), gOut],
      }));

      // Connected accounts with permissions
      const list = el('div', { class: 'stack-sm' });
      if (!accounts.length) list.appendChild(emptyState('No accounts connected', 'PODA cannot read any mail until an account is connected above.'));
      for (const a of accounts) {
        const modSw = el('button', { class: 'switch', role: 'switch', 'aria-checked': a.allow_modify ? 'true' : 'false', 'aria-label': `Allow edits for ${a.account_name}` });
        const sendSw = el('button', { class: 'switch', role: 'switch', 'aria-checked': a.allow_send ? 'true' : 'false', 'aria-label': `Allow sending for ${a.account_name}` });
        const setPerm = async (allow_modify, allow_send) => { try { await api(`/mail/accounts/${encodeURIComponent(a.id)}/permissions`, { method: 'POST', body: { allow_modify, allow_send } }); toast('Permissions updated. Ledger refreshed.', 'ok'); await refreshStatus(); } catch (err) { toast(errorDetail(err).message, 'danger'); } };
        modSw.onclick = () => setPerm(!a.allow_modify, a.allow_send && !a.allow_modify ? false : a.allow_send);
        sendSw.onclick = async () => { if (!a.allow_send && !(await confirmDialog({ title: `Allow PODA to send from ${a.account_name}?`, body: 'Sending stays off by default. Even when allowed, every send requires an explicit confirmation and produces a receipt.', confirmLabel: 'Allow sending', danger: true }))) return; setPerm(a.allow_modify || !a.allow_send, !a.allow_send); };
        list.appendChild(el('div', { class: `mail-account ${a.id === state.accountId ? 'active' : ''}`, role: 'button', tabindex: 0, onclick: async (e) => { if (e.target.closest('button')) return; state.accountId = a.id; state.mailbox = 'INBOX'; state.selected = null; drawConnections(); drawPermissions(); drawListHead(); await drawMailboxes(); loadMessages(true); } },
          el('div', { class: 'row-between' }, el('div', { class: 'row' }, el('span', { class: 'mail-avatar', style: { '--h': hue(a.account_name) } }, initials(a.account_name)), el('div', {}, el('strong', {}, a.account_name), el('div', { class: 'faint small mono' }, `${a.provider === 'apple_mail' ? 'Apple Mail' : 'Gmail IMAP'} · ${a.address_masked || ''}`))), el('button', { class: 'btn btn-sm btn-ghost', onclick: async () => { if (!(await confirmDialog({ title: `Remove ${a.account_name}?`, body: 'PODA loses access immediately. Stored credentials for direct Gmail are deleted from Keychain.', confirmLabel: 'Remove', danger: true }))) return; try { await api(`/mail/accounts/${encodeURIComponent(a.id)}`, { method: 'DELETE' }); toast('Account removed', 'ok'); await refreshStatus(); } catch (err) { toast(errorDetail(err).message, 'danger'); } } }, 'Remove')),
          el('div', { class: 'mail-perms' }, el('span', { class: 'row small' }, pill('read', 'ok'), 'always on while connected'), el('span', { class: 'row small' }, modSw, 'edits'), el('span', { class: 'row small' }, sendSw, 'sending')),
          a.last_error ? el('div', { class: 'small', style: { color: 'var(--danger)' } }, String(a.last_error)) : null));
      }
      connBox.appendChild(el('div', { class: 'stack-sm' }, el('div', { class: 'panel-title' }, 'Connected accounts'), list));
      // The legacy arbitrary-host IMAP preview was retired. Apple Mail and the
      // Gmail connector enforce provider-specific egress and offline mode.

    }

    function drawPermissions() {
      permBox.innerHTML = '';
      const cap = state.status?.capability || {};
      const a = currentAccount();
      permBox.append(kv([['Accounts', (state.status?.accounts || []).length], ['Read', pill(cap.can_read ? 'yes' : 'no', cap.can_read ? 'ok' : 'danger')], ['Edit mailbox', pill(cap.can_modify_mailbox ? 'yes' : 'no', cap.can_modify_mailbox ? 'ok' : 'muted')], ['Send', pill(cap.can_send ? 'enabled' : 'off', cap.can_send ? 'warn' : 'muted')], ['Selected', a ? `${a.account_name} · ${a.allow_modify ? 'edits on' : 'read only'}${a.allow_send ? ' · sending on' : ''}` : 'none']]),
        el('p', { class: 'faint small' }, state.status?.permissions_note || 'Email content is treated as untrusted data. PODA never follows instructions found inside a message, never opens links, and never sends without your confirmation.'));
    }

    async function loadReceipts() {
      receiptsBox.innerHTML = ''; receiptsBox.appendChild(skeleton(2));
      try {
        const r = await api('/mail/receipts?limit=20'); state.receipts = r.receipts || [];
        receiptsBox.innerHTML = '';
        if (!state.receipts.length) { receiptsBox.appendChild(el('p', { class: 'faint small' }, 'No mail actions yet. Every change to a mailbox will be listed here with its verification.')); return; }
        receiptsBox.appendChild(el('div', { class: 'timeline' }, ...state.receipts.map((rc) => el('div', { class: 'tl-item compact' }, el('span', { class: 'when' }, fmtTime(rc.finished_at || rc.started_at)), el('div', { class: 'what' }, el('div', { class: 'row' }, receiptChip(rc)), rc.target ? el('span', { class: 'mono' }, String(rc.target).slice(0, 80)) : null)))));
      } catch (err) { receiptsBox.innerHTML = ''; receiptsBox.appendChild(el('p', { class: 'faint small' }, `Receipts unavailable: ${errorDetail(err).message}`)); }
    }

    // ---------- mailboxes ----------
    async function drawMailboxes() {
      boxesBox.innerHTML = '';
      if (!state.accountId) { boxesBox.appendChild(el('p', { class: 'faint small' }, 'Connect an account to list its mailboxes.')); return; }
      boxesBox.appendChild(skeleton(4));
      try {
        const r = await enqueue(() => api(`/mail/mailboxes?account_id=${encodeURIComponent(state.accountId)}`), { front: true }); state.mailboxes = r.mailboxes || [];
        boxesBox.innerHTML = '';
        if (!state.mailboxes.length) { boxesBox.appendChild(el('p', { class: 'faint small' }, 'No mailboxes returned.')); return; }
        const primary = ['INBOX', 'Inbox', 'Drafts', 'Sent', 'Sent Mail', 'Flagged', 'Starred', 'Important', 'Archive', 'All Mail', 'Trash', 'Junk', 'Spam'];
        const sorted = [...state.mailboxes].sort((a, b) => { const ia = primary.indexOf(a.name), ib = primary.indexOf(b.name); return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.name.localeCompare(b.name); });
        const list = el('div', { class: 'mailbox-list', role: 'listbox' });
        for (const mb of sorted) list.appendChild(el('button', { type: 'button', class: `mailbox ${mb.name === state.mailbox ? 'active' : ''}`, role: 'option', 'aria-selected': mb.name === state.mailbox ? 'true' : 'false', onclick: () => { state.mailbox = mb.name; state.selected = null; state.query = ''; $$('.mailbox', boxesBox).forEach((b) => { const on = b.textContent.startsWith(mb.name); b.classList.toggle('active', on); b.setAttribute('aria-selected', on ? 'true' : 'false'); }); drawListHead(); loadMessages(true); } },
          el('span', { class: 'mailbox-name' }, mb.name), mb.unread ? el('span', { class: 'mailbox-unread' }, mb.unread) : el('span', { class: 'faint small mono' }, mb.count ?? '')));
        boxesBox.appendChild(list);
      } catch (err) { boxesBox.innerHTML = ''; boxesBox.appendChild(errorNode(err)); }
    }

    // ---------- list ----------
    function drawListHead() {
      listHead.innerHTML = '';
      const accounts = state.status?.accounts || [];
      const search = el('input', { class: 'input mail-search', type: 'search', placeholder: 'Search this account', value: state.query, 'aria-label': 'Search mail' });
      let timer = 0; search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { state.query = search.value.trim(); state.selected = null; const head = listHead.querySelector('strong'); if (head) head.textContent = state.query ? `Search: ${state.query}` : state.mailbox; loadMessages(true); }, 320); });
      const unread = el('button', { type: 'button', class: 'btn btn-sm', 'aria-pressed': state.unreadOnly ? 'true' : 'false', onclick: () => { state.unreadOnly = !state.unreadOnly; drawListHead(); loadMessages(true); } }, state.unreadOnly ? 'Unread only' : 'All');
      const compose = el('button', { type: 'button', class: 'btn btn-sm btn-primary', disabled: !currentAccount()?.allow_modify, title: currentAccount()?.allow_modify ? 'Write a new draft' : 'Enable edits for this account to write drafts', onclick: () => openComposer({}) }, 'New draft');
      const acct = accounts.length > 1 ? el('select', { class: 'input mail-acct-select', 'aria-label': 'Account', onchange: async (e) => { state.accountId = e.target.value; state.mailbox = 'INBOX'; state.selected = null; drawConnections(); drawPermissions(); drawListHead(); await drawMailboxes(); loadMessages(true); } }, ...accounts.map((a) => el('option', { value: a.id, selected: a.id === state.accountId }, a.account_name))) : null;
      listHead.append(el('div', { class: 'row' }, el('strong', {}, state.query ? `Search: ${state.query}` : state.mailbox), el('span', { class: 'faint small mono', id: 'mailCount' }, '')), el('div', { class: 'row' }, acct, search, unread, el('button', { type: 'button', class: 'btn btn-sm btn-ghost', 'aria-label': 'Refresh', onclick: () => loadMessages(true) }, 'Refresh'), compose));
    }

    async function loadMessages(reset) {
      if (!state.accountId || state.loadingList) return;
      state.loadingList = true;
      if (reset) { state.offset = 0; state.messages = []; listBox.innerHTML = ''; snippetQueue.seen.clear(); if (state.query) listBox.appendChild(el('div', { class: 'mail-searching' }, el('span', { class: 'live' }), `Searching Mail for “${state.query}”… this walks INBOX and the archive and can take around ten seconds.`)); listBox.appendChild(skeleton(6)); }
      try {
        let r;
        if (state.query) { r = await enqueue(() => api(`/mail/search?q=${encodeURIComponent(state.query)}&account_id=${encodeURIComponent(state.accountId)}&limit=${state.limit}`), { front: true }); r.total = r.total ?? (r.messages || []).length; }
        else r = await enqueue(() => api(`/mail/messages?account_id=${encodeURIComponent(state.accountId)}&mailbox=${encodeURIComponent(state.mailbox)}&limit=${state.limit}&offset=${state.offset}${state.unreadOnly ? '&unread_only=true' : ''}`), { front: true });
        const batch = r.messages || [];
        state.messages = reset ? batch : state.messages.concat(batch);
        state.total = r.total ?? state.messages.length; state.unread = r.unread ?? state.unread; state.offset = state.messages.length;
        const count = $('#mailCount'); if (count) count.textContent = `${state.messages.length}${state.total > state.messages.length ? ` of ${state.total}` : ''}${state.unread ? ` · ${state.unread} unread` : ''}${r.elapsed_ms ? ` · ${r.elapsed_ms} ms` : ''}`;
        drawList(r);
        if (reset) drawRead(null);
      } catch (err) { listBox.innerHTML = ''; listBox.appendChild(errorNode(err)); }
      finally { state.loadingList = false; }
    }

    function drawList(r) {
      listBox.innerHTML = '';
      if (!state.messages.length) { listBox.appendChild(emptyState(state.query ? 'No matches' : 'Nothing here', state.unreadOnly ? 'No unread messages in this mailbox.' : 'This mailbox is empty or still loading in Mail.')); return; }
      for (const m of state.messages) listBox.appendChild(messageRow(m));
      if (state.total > state.messages.length && !state.query) listBox.appendChild(el('button', { type: 'button', class: 'btn btn-ghost mail-more', onclick: () => loadMessages(false) }, `Load more (${state.total - state.messages.length} remaining)`));
      loadSnippets();
    }

    // Apple Mail returns snippet:null from the list call (bodies are fetched lazily); fill them in progressively, 10 at a time.
    const snippetQueue = { running: false, seen: new Set() };
    async function loadSnippets() {
      if (snippetQueue.running) return;
      const accountId = state.accountId;
      const pending = state.messages.filter((m) => (m.snippet === null || m.snippet === undefined) && !snippetQueue.seen.has(m.id));
      if (!pending.length || !accountId) return;
      snippetQueue.running = true;
      try {
        const BATCH = 4; // Mail serializes body fetches (~2 s each); small batches keep the queue responsive
        for (let i = 0; i < pending.length; i += BATCH) {
          const batch = pending.slice(i, i + BATCH); batch.forEach((m) => snippetQueue.seen.add(m.id));
          if (state.accountId !== accountId || !batch.some((m) => listBox.querySelector(`.mail-row[data-id="${CSS.escape(m.id)}"]`))) break;
          let got = {};
          try { got = (await enqueue(() => api('/mail/snippets', { method: 'POST', body: { account_id: accountId, ids: batch.map((m) => m.id) } }))).snippets || {}; } catch { got = {}; }
          for (const m of batch) {
            const text = got[m.id] ?? '';
            m.snippet = text; const row = state.messages.find((x) => x.id === m.id); if (row) row.snippet = text;
            const node = listBox.querySelector(`.mail-row[data-id="${CSS.escape(m.id)}"] .mail-snippet`);
            if (node) { node.textContent = text; node.classList.remove('pending'); }
          }
        }
      } finally { snippetQueue.running = false; }
    }

    function messageRow(m) {
      const row = el('div', { class: `mail-row ${m.read ? '' : 'unread'} ${state.selected?.id === m.id ? 'active' : ''}`, role: 'option', tabindex: 0, 'aria-selected': state.selected?.id === m.id ? 'true' : 'false', dataset: { id: m.id },
        onclick: () => openMessage(m), onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openMessage(m); } } },
        el('span', { class: 'mail-dot', 'aria-hidden': 'true' }),
        el('span', { class: 'mail-avatar', style: { '--h': hue(m.from_address_masked || m.from_name) } }, initials(m.from_name, m.from_address_masked)),
        el('div', { class: 'mail-row-main' },
          el('div', { class: 'mail-row-top' }, el('span', { class: 'mail-from' }, m.from_name || m.from_address_masked || 'Unknown sender'), el('span', { class: 'mail-date' }, fmtRelative(m.date))),
          el('div', { class: 'mail-subject' }, m.subject || '(no subject)'),
          el('div', { class: `mail-snippet${m.snippet === null || m.snippet === undefined ? ' pending' : ''}` }, m.snippet || '')),
        el('div', { class: 'mail-row-icons' }, m.flagged ? el('span', { class: 'mail-flag', title: 'Flagged' }, '⚑') : null, m.has_attachments ? el('span', { class: 'mail-clip', title: 'Has attachments' }, '⌘') : null));
      return row;
    }

    // ---------- reading pane ----------
    async function openMessage(m) {
      state.selected = m; $$('.mail-row', listBox).forEach((r) => { const on = r.dataset.id === m.id; r.classList.toggle('active', on); r.setAttribute('aria-selected', on ? 'true' : 'false'); });
      readBox.innerHTML = ''; readBox.appendChild(skeleton(5, true));
      try {
        const full = await enqueue(() => api(`/mail/messages/${encodeURIComponent(m.id)}?account_id=${encodeURIComponent(state.accountId)}`), { front: true });
        state.selected = { ...m, ...full }; drawRead(state.selected);
      } catch (err) { readBox.innerHTML = ''; readBox.appendChild(errorNode(err)); }
    }

    function drawRead(m) {
      readBox.innerHTML = ''; state.extraction = null;
      if (!m) { readBox.appendChild(el('div', { class: 'mail-read-empty' }, el('strong', {}, 'No message selected'), el('p', { class: 'faint small' }, 'Pick a message to read it here. Bodies are shown as plain text; links are inert.'))); return; }
      const a = currentAccount(); const canModify = !!a?.allow_modify;
      const receiptsRow = el('div', { class: 'row mail-receipts' });
      const act = (label, action, extra = {}, opts = {}) => el('button', { type: 'button', class: `btn btn-sm ${opts.danger ? 'btn-danger' : ''}`, disabled: !canModify, title: canModify ? label : 'Enable edits for this account to change messages', onclick: () => runAction(m, action, extra, receiptsRow, opts) }, label);
      const toolbar = el('div', { class: 'mail-actions row' },
        act(m.read ? 'Mark unread' : 'Mark read', m.read ? 'mark_unread' : 'mark_read'),
        act(m.flagged ? 'Unflag' : 'Flag', m.flagged ? 'unflag' : 'flag'),
        act('Archive', 'archive'),
        el('button', { type: 'button', class: 'btn btn-sm', disabled: !canModify, onclick: () => pickMailbox(m, receiptsRow) }, 'Move…'),
        act('Trash', 'trash', {}, { danger: true, confirm: true }),
        el('span', { class: 'spacer' }),
        el('button', { type: 'button', class: 'btn btn-sm', disabled: !canModify, title: canModify ? 'Reply as a draft' : 'Enable edits to draft replies', onclick: () => openComposer({ replyTo: m }) }, 'Reply'),
        el('button', { type: 'button', class: 'btn btn-sm btn-primary', onclick: () => extract(m) }, 'Extract commitments'));
      const header = el('div', { class: 'mail-read-head' },
        el('div', { class: 'row' }, el('span', { class: 'mail-avatar lg', style: { '--h': hue(m.from_address_masked || m.from_name) } }, initials(m.from_name, m.from_address_masked)), el('div', { class: 'stack-sm', style: { gap: '2px' } }, el('h2', { class: 'mail-read-subject' }, m.subject || '(no subject)'), el('div', { class: 'small muted' }, el('strong', {}, m.from_name || ''), ' ', el('span', { class: 'mono faint' }, m.from_address_masked || '')), el('div', { class: 'faint small' }, `to ${m.to_masked || 'you'}${m.headers?.cc_masked ? ` · cc ${m.headers.cc_masked}` : ''} · ${fmtTime(m.date)} · ${m.mailbox || state.mailbox}`))),
        el('div', { class: 'row' }, m.read ? null : pill('unread', 'accent'), m.flagged ? pill('flagged', 'warn') : null, m.has_attachments ? pill(`${(m.attachments || []).length || ''} attachment${(m.attachments || []).length === 1 ? '' : 's'}`.trim(), 'muted') : null));
      const banner = el('div', { class: 'notice notice-warn small mail-untrusted' }, el('strong', {}, 'Untrusted content. '), 'PODA treats email as data, never as instructions. Links are shown as text and nothing here can run.');
      const attachments = (m.attachments || []).length ? el('div', { class: 'mail-attachments' }, ...m.attachments.map((at) => el('span', { class: 'chip', title: at.mime || '' }, at.name || 'attachment', ' ', el('b', {}, fmtBytes(at.size))))) : null;
      readBox.append(header, toolbar, receiptsRow, banner, attachments, bodyNode(m.body_text ?? m.snippet ?? ''));
    }

    async function runAction(m, action, extra, receiptsRow, opts = {}) {
      if (opts.confirm && !(await confirmDialog({ title: `Move "${m.subject || '(no subject)'}" to Trash?`, body: 'This changes your real mailbox. The message goes to the Trash folder of the account; PODA will re-read it to verify and record a receipt.', confirmLabel: 'Move to Trash', danger: true }))) return;
      const pending = el('span', { class: 'receipt-chip' }, el('span', { class: 'live' }), `${action}…`); receiptsRow.appendChild(pending);
      try {
        const r = await enqueue(() => api(`/mail/messages/${encodeURIComponent(m.id)}/actions`, { method: 'POST', body: { account_id: state.accountId, action, confirm: !!opts.confirm, ...extra } }), { front: true });
        pending.replaceWith(receiptChip(r.receipt));
        const after = r.state_after || {};
        Object.assign(m, after);
        const idx = state.messages.findIndex((x) => x.id === m.id);
        if (idx >= 0) { if (['archive', 'move', 'trash'].includes(action) && !state.query) { state.messages.splice(idx, 1); } else Object.assign(state.messages[idx], after); }
        drawList({}); if (['archive', 'move', 'trash'].includes(action)) drawRead(null); else drawRead(m);
        if (!['archive', 'move', 'trash'].includes(action)) readBox.querySelector('.mail-receipts')?.appendChild(receiptChip(r.receipt));
        toast(`${action.replace('_', ' ')} · ${r.receipt?.outcome || 'done'}`, r.receipt?.outcome === 'failed' ? 'danger' : 'ok'); loadReceipts(); drawMailboxes();
      } catch (err) {
        const d = errorDetail(err); pending.remove(); receiptsRow.appendChild(errorNode(err));
        if (d.code === 'MODIFY_NOT_ALLOWED') receiptsRow.appendChild(el('div', { class: 'notice notice-warn small' }, 'Edits are off for this account. Turn on the edits switch under Connected accounts to allow changes.'));
      }
    }

    async function pickMailbox(m, receiptsRow) {
      const dlg = el('dialog');
      const sel = el('select', { class: 'input' }, ...state.mailboxes.filter((b) => b.name !== (m.mailbox || state.mailbox)).map((b) => el('option', { value: b.name }, b.name)));
      const ok = el('button', { class: 'btn btn-primary' }, 'Move'); const cancel = el('button', { class: 'btn btn-ghost' }, 'Cancel');
      dlg.append(el('div', { class: 'dialog-body' }, el('h2', {}, 'Move message'), el('p', { class: 'muted small' }, m.subject || '(no subject)'), el('label', { class: 'label' }, 'Destination mailbox'), sel), el('div', { class: 'dialog-actions' }, cancel, ok));
      document.body.appendChild(dlg); dlg.showModal();
      const close = () => { dlg.close(); dlg.remove(); };
      cancel.onclick = close; dlg.oncancel = close;
      ok.onclick = () => { const mailbox = sel.value; close(); runAction(m, 'move', { mailbox }, receiptsRow); };
    }

    // ---------- composer ----------
    function openComposer({ replyTo }) {
      const a = currentAccount(); if (!a) return;
      const dlg = el('dialog', { class: 'mail-compose' });
      const to = el('input', { class: 'input', placeholder: 'recipient@example.com, another@example.com', value: replyTo ? (replyTo.headers?.reply_to || '') : '', 'aria-label': 'To' });
      const cc = el('input', { class: 'input', placeholder: 'optional', 'aria-label': 'Cc' });
      const subject = el('input', { class: 'input', value: replyTo ? (/^re:/i.test(replyTo.subject || '') ? replyTo.subject : `Re: ${replyTo.subject || ''}`) : '', 'aria-label': 'Subject' });
      const body = el('textarea', { class: 'input', rows: 10, 'aria-label': 'Body' });
      if (replyTo) body.value = `\n\nOn ${fmtTime(replyTo.date)}, ${replyTo.from_name || replyTo.from_address_masked || 'they'} wrote:\n${String(replyTo.body_text || replyTo.snippet || '').split('\n').map((l) => `> ${l}`).join('\n')}`;
      const out = el('div');
      const save = el('button', { class: 'btn btn-primary' }, 'Save draft');
      const send = a.allow_send ? el('button', { class: 'btn btn-danger' }, 'Send…') : null;
      const cancel = el('button', { class: 'btn btn-ghost' }, 'Close');
      const payload = () => ({ account_id: a.id, to: to.value.split(',').map((s) => s.trim()).filter(Boolean), cc: cc.value.split(',').map((s) => s.trim()).filter(Boolean), subject: subject.value, body: body.value, reply_to_message_id: replyTo?.id || null });
      save.onclick = async () => { save.disabled = true; out.innerHTML = ''; try { const r = await api('/mail/drafts', { method: 'POST', body: payload() }); out.appendChild(el('div', { class: 'row' }, el('div', { class: 'notice notice-ok small' }, `Draft saved in ${a.account_name}${r.draft_id ? ` (${String(r.draft_id).slice(0, 12)})` : ''}. Nothing was sent.`), receiptChip(r.receipt))); loadReceipts(); } catch (err) { out.appendChild(errorNode(err)); } finally { save.disabled = false; } };
      if (send) send.onclick = async () => {
        const p = payload(); if (!p.to.length) { toast('Add at least one recipient', 'danger'); return; }
        if (!(await confirmDialog({ title: `Send this email from ${a.account_name}?`, body: el('div', { class: 'stack-sm small' }, el('div', {}, el('strong', {}, 'To: '), p.to.join(', ')), el('div', {}, el('strong', {}, 'Subject: '), p.subject || '(no subject)'), el('p', { class: 'muted' }, 'Sending cannot be undone. Type SEND to confirm.')), confirmLabel: 'Send now', danger: true, requireText: 'SEND' }))) return;
        send.disabled = true; out.innerHTML = '';
        try { const r = await api('/mail/send', { method: 'POST', body: { ...p, confirm: true } }); out.appendChild(el('div', { class: 'row' }, el('div', { class: 'notice notice-ok small' }, 'Sent. Receipt recorded.'), receiptChip(r.receipt))); loadReceipts(); }
        catch (err) { out.appendChild(errorNode(err)); } finally { send.disabled = false; }
      };
      dlg.append(el('div', { class: 'dialog-body' }, el('h2', {}, replyTo ? 'Reply as draft' : 'New draft'), el('p', { class: 'faint small' }, `From ${a.account_name}${a.allow_send ? '' : ' · sending is off for this account; drafts only'}`),
        el('label', { class: 'label' }, 'To'), to, el('label', { class: 'label' }, 'Cc'), cc, el('label', { class: 'label' }, 'Subject'), subject, el('label', { class: 'label' }, 'Body'), body, out),
        el('div', { class: 'dialog-actions' }, cancel, save, send));
      document.body.appendChild(dlg); dlg.showModal();
      const close = () => { dlg.close(); dlg.remove(); };
      cancel.onclick = close; dlg.oncancel = close;
      (replyTo ? body : to).focus();
    }

    // ---------- extraction → Notion ----------
    async function extract(m) {
      extractBox.innerHTML = ''; extractBox.appendChild(skeleton(3));
      $('#inspector').classList.add('open');
      try {
        const r = await enqueue(() => api(`/mail/messages/${encodeURIComponent(m.id)}/extract`, { method: 'POST', body: { account_id: state.accountId } }), { front: true });
        state.extraction = r; extractBox.innerHTML = '';
        const items = r.commitments || [];
        extractBox.appendChild(el('div', { class: 'row small faint' }, el('span', {}, `${items.length} candidate${items.length === 1 ? '' : 's'}`), r.model ? el('span', {}, `· ${r.model}`) : null, pill('unconfirmed', 'warn')));
        if (r.note) extractBox.appendChild(el('p', { class: 'faint small' }, r.note));
        if (!items.length) { extractBox.appendChild(el('p', { class: 'faint small' }, 'No commitments or dates were found in this message.')); return; }
        items.forEach((c, i) => extractBox.appendChild(commitmentCard(m, c, i)));
      } catch (err) { extractBox.innerHTML = ''; extractBox.appendChild(errorNode(err)); }
    }
    function commitmentCard(m, c, i) {
      const conf = Math.round((c.confidence || 0) * 100);
      const when = c.date_start ? `${c.date_start}${c.date_end ? ` → ${c.date_end}` : ''}${c.all_day ? ' · all day' : ''}${c.time_zone ? ` · ${c.time_zone}` : ''}${c.duration_minutes ? ` · ${c.duration_minutes} min` : ''}` : 'no date found';
      const out = el('div');
      const propose = el('button', { class: 'btn btn-sm btn-primary' }, 'Propose to Notion');
      propose.onclick = async () => {
        propose.disabled = true; out.innerHTML = ''; out.appendChild(skeleton(2));
        try {
          const r = await api(`/mail/messages/${encodeURIComponent(m.id)}/propose-notion`, { method: 'POST', body: { account_id: state.accountId, commitment: c } });
          out.innerHTML = '';
          const dup = r.duplicates || [];
          const confirm = el('button', { class: 'btn btn-sm btn-primary' }, 'Confirm and create in Notion');
          confirm.onclick = async () => { confirm.disabled = true; try { const cr = await api('/notion/events/commit', { method: 'POST', body: { proposal_id: r.proposal_id, confirm: true } }); out.appendChild(el('div', { class: 'row' }, el('div', { class: 'notice notice-ok small' }, `Created and re-read in Notion${cr.page_id ? ` (${String(cr.page_id).slice(0, 8)})` : ''}.`), cr.receipt ? receiptChip(cr.receipt) : null)); } catch (err) { out.appendChild(errorNode(err)); confirm.disabled = false; } };
          out.append(el('div', { class: 'notice notice-accent small stack-sm' }, el('strong', {}, 'Preview of the Notion page'), el('pre', { class: 'json', style: { maxHeight: '160px' } }, JSON.stringify(r.payload_preview || r.preview || {}, null, 2)), dup.length ? el('div', { class: 'notice notice-warn small' }, `${dup.length} possible duplicate${dup.length === 1 ? '' : 's'}: ${dup.map((d) => d.title || d.page_id || d).join('; ')}`) : el('span', { class: 'faint small' }, 'No duplicates detected.'), el('div', { class: 'row' }, confirm)));
        } catch (err) {
          out.innerHTML = ''; const d = errorDetail(err); out.appendChild(errorNode(err));
          if (d.code === 'NOTION_NOT_READY') out.appendChild(el('button', { class: 'btn btn-sm', onclick: () => navigate('notion') }, 'Open the Notion tab'));
          propose.disabled = false;
        }
      };
      return el('div', { class: 'commit-card', dataset: { index: i } },
        el('div', { class: 'row-between' }, el('strong', {}, c.title || 'Commitment'), pill(c.kind || 'tentative', c.kind === 'confirmed' ? 'ok' : 'warn')),
        el('div', { class: 'small muted' }, when, c.location ? ` · ${c.location}` : ''),
        c.evidence_span ? el('blockquote', { class: 'mail-evidence' }, c.evidence_span) : null,
        el('div', { class: 'row', style: { alignItems: 'center' } }, el('span', { class: 'faint small' }, 'confidence'), el('div', { class: 'meter', style: { flex: '1', maxWidth: '140px', '--w': `${conf}%` } }, el('i')), el('span', { class: 'mono small' }, `${conf}%`)),
        (c.suggested_project || c.suggested_class) ? el('div', { class: 'row small faint' }, c.suggested_project ? el('span', { class: 'chip' }, 'project ', el('b', {}, c.suggested_project)) : null, c.suggested_class ? el('span', { class: 'chip' }, 'class ', el('b', {}, c.suggested_class)) : null) : null,
        el('div', { class: 'row' }, propose), out);
    }

    refreshStatus();
    return {};
  });
})();
