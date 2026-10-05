/* PODA home shell: Ask, Plan, Notion, Files & Agent, Privacy, System. Vanilla, local-only, no remote assets. */
(function () {
  'use strict';
  const { $, $$, el, api, ApiError, errorDetail, errorNode, toast, confirmDialog, promptDialog, skeleton, emptyState, pill, statusPill, route, renderRoute, navigate, registerCommand, openPalette, loadBuild, fmtTime, fmtBytes } = window.PODA;
  const md = window.PODAMarkdown;

  // ---------- Navigation ----------
  $('#memoryBtn').addEventListener('click', () => { window.location.assign('/memory-viewer'); });
  $$('.nav-item').forEach((b) => { if (b.id !== 'memoryBtn') b.addEventListener('click', () => navigate(b.dataset.route)); });
  $('#paletteBtn').addEventListener('click', openPalette);
  $('#inspectorToggle').addEventListener('click', () => $('#inspector').classList.toggle('open'));
  $('#inspectorClose').addEventListener('click', () => $('#inspector').classList.remove('open'));
  window.addEventListener('hashchange', renderRoute);
  document.addEventListener('keydown', (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const tag = document.activeElement?.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || document.activeElement?.isContentEditable) return;
    const map = { 1: 'ask', 2: 'memory', 3: 'plan', 4: 'notion', 5: 'mail', 6: 'files', 7: 'privacy', 8: 'system', 9: 'personalize' };
    if (map[e.key]) { e.preventDefault(); if (map[e.key] === 'memory') window.location.assign('/memory-viewer'); else navigate(map[e.key]); }
  });

  const screens = [['ask', 'Ask', '1'], ['personalize', 'Personalize', '9'], ['plan', 'Plan', '3'], ['notion', 'Notion', '4'], ['mail', 'Mail', '5'], ['files', 'Files & Agent', '6'], ['privacy', 'Privacy', '7'], ['system', 'System', '8']];
  for (const [name, label, key] of screens) registerCommand({ label: `Go to ${label}`, kind: 'Screen', key, keywords: name === 'mail' ? 'email inbox gmail apple mail' : '', run: () => navigate(name) });
  registerCommand({ label: 'Open memory viewer', kind: 'Screen', key: '2', keywords: 'open3d 3d second brain', run: () => window.location.assign('/memory-viewer') });
  registerCommand({ label: 'Check capabilities', kind: 'Action', keywords: 'ledger status', run: async () => { const c = await api('/capabilities'); toast(`Ollama ${c.ollama?.reachable ? 'reachable' : 'unreachable'} · Notion ${c.notion?.status || 'DISCONNECTED'} · ${c.filesystem?.granted_roots?.length || 0} folder grants`); navigate('system'); } });
  registerCommand({ label: 'Create encrypted backup', kind: 'Action', keywords: 'privacy export', run: async () => { const r = await api('/privacy/backup', { method: 'POST' }); toast(`Backup written: ${r.path.split('/').pop()}`, 'ok'); } });
  registerCommand({ label: 'Toggle offline mode', kind: 'Action', keywords: 'network egress', run: async () => { const s = await api('/privacy/status'); const next = !s.egress?.offline_mode; await api('/privacy/offline', { method: 'POST', body: { enabled: next } }); toast(`Offline mode ${next ? 'enabled' : 'disabled'}`, 'ok'); } });
  registerCommand({ label: 'Refresh capability ledger', kind: 'Action', run: async () => { await api('/capabilities/ledger'); toast('Ledger refreshed from runtime', 'ok'); } });

  // ---------- Shared helpers ----------
  function section(title, meta, ...children) {
    return el('section', { class: 'card stack' }, el('div', { class: 'card-head' }, el('h2', {}, title), meta ? el('span', { class: 'meta' }, meta) : null), ...children);
  }
  function kv(pairs) {
    const dl = el('dl', { class: 'kv' });
    for (const [k, v] of pairs) { dl.append(el('dt', {}, k), el('dd', {}, v === null || v === undefined || v === '' ? el('span', { class: 'faint' }, 'none') : (v instanceof Node ? v : String(v)))); }
    return dl;
  }
  function yesNo(v) { return pill(v ? 'yes' : 'no', v ? 'ok' : 'danger'); }
  function jsonBox(obj) { return el('pre', { class: 'json' }, JSON.stringify(obj, null, 2)); }
  async function load(target, fn) {
    target.innerHTML = ''; target.appendChild(skeleton(3, true));
    try { const node = await fn(); target.innerHTML = ''; if (node) target.appendChild(node); }
    catch (err) { target.innerHTML = ''; target.appendChild(errorNode(err)); }
  }

  // =====================================================================
  // ASK
  // =====================================================================
  // Every page load is a fresh conversation (no session id is persisted); the transcript below only lives while this
  // page is open so switching screens and back keeps the current chat. Older conversations are never replayed here;
  // they live in the memory web and reach PODA only through semantic recall when they are relevant.
  const askState = { sessionId: null, mode: 'auto', thinking: 'balanced', useMemory: true, controller: null, transcript: [] };

  route('ask', async ({ view, inspector, setTitle, setInspectorTitle }) => {
    setTitle('Ask'); setInspectorTitle('Context');
    $('#viewCrumb').textContent = 'Grounded local chat';
    const messages = el('div', { class: 'messages', id: 'messages', 'aria-live': 'polite' });
    const textarea = el('textarea', { placeholder: 'Ask PODA. Shift+Enter for a new line.', rows: 1, 'aria-label': 'Message' });
    const sendBtn = el('button', { class: 'btn btn-primary', type: 'submit' }, 'Send');
    const stopBtn = el('button', { class: 'btn btn-danger', type: 'button', hidden: true }, 'Stop');
    const statusLine = el('div', { class: 'status-line' });
    const seg = (name, options, value, onChange) => {
      const wrap = el('div', { class: 'seg', role: 'group', 'aria-label': name });
      for (const [val, label] of options) {
        const b = el('button', { type: 'button', 'aria-pressed': val === value ? 'true' : 'false' }, label);
        b.onclick = () => { $$('button', wrap).forEach((x) => x.setAttribute('aria-pressed', 'false')); b.setAttribute('aria-pressed', 'true'); onChange(val); };
        wrap.appendChild(b);
      }
      return wrap;
    };
    const memoryToggle = el('label', { class: 'check' }, el('input', { type: 'checkbox', checked: askState.useMemory, onchange: (e) => { askState.useMemory = e.target.checked; } }), 'Use memory');
    const composer = el('form', { class: 'composer' }, textarea,
      el('div', { class: 'composer-bar' },
        seg('Model', [['auto', 'Auto'], ['fast', 'Fast'], ['advanced', 'Advanced'], ['deep', 'Deep']], askState.mode, (v) => { askState.mode = v; }),
        (() => { const w = seg('Thinking', [['fast', 'Quick'], ['balanced', 'Balanced'], ['deep', 'Deep think']], askState.thinking, (v) => { askState.thinking = v; }); const deep = $$('button', w)[2]; if (deep) deep.title = 'Also reads in-depth memory (the full question and answer pairs behind each surface memory)'; return w; })(),
        memoryToggle, el('span', { class: 'spacer' }), el('button', { class: 'btn btn-ghost', type: 'button', title: 'Start a fresh conversation. Nothing is deleted: this chat stays in your memory web and can be recalled later.', onclick: () => newChat() }, 'New chat'), stopBtn, sendBtn));
    view.appendChild(el('div', { class: 'screen ask' }, messages, el('div', { class: 'stack-sm' }, statusLine, composer)));

    // Inspector
    const recallBox = el('div', { class: 'stack-sm' }, el('p', { class: 'faint small' }, 'Send a message to see which memories were recalled and why.'));
    const sessionBox = el('div', { class: 'stack-sm' });
    inspector.append(el('div', { class: 'stack-sm' }, el('h3', {}, 'Why was this recalled?'), recallBox), el('div', { class: 'divider' }), el('div', { class: 'stack-sm' }, el('h3', {}, 'Last response'), sessionBox));

    textarea.addEventListener('input', () => { textarea.style.height = 'auto'; textarea.style.height = Math.min(textarea.scrollHeight, 180) + 'px'; });
    textarea.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); composer.requestSubmit(); } });

    function addMessage(role, content, meta) {
      const wrap = el('div', { class: `msg msg-${role}` });
      const bubble = el('div', { class: 'bubble' });
      if (role === 'user') bubble.textContent = content; else bubble.innerHTML = md.render(content);
      const metaRow = el('div', { class: 'msg-meta' }, el('span', {}, role === 'user' ? 'You' : 'PODA'), meta ? el('span', {}, fmtTime(meta)) : null);
      wrap.append(bubble, metaRow);
      messages.appendChild(wrap);
      messages.scrollTop = messages.scrollHeight;
      return { wrap, bubble, metaRow };
    }
    function showEmpty() {
      const suggestions = ['What can you do with files?', 'What is on my Notion calendar this week?', 'Summarize what you remember about my projects', 'Rank my current projects'];
      messages.appendChild(el('div', { class: 'hero-empty' },
        el('h2', {}, 'Private, grounded, local.'),
        el('p', { class: 'muted', style: { maxWidth: '54ch' } }, 'PODA answers from a verified runtime ledger, your visible memory graph, and tools you have explicitly authorized. Nothing leaves this Mac except allowlisted Notion calls.'),
        el('p', { class: 'faint small', style: { maxWidth: '54ch' } }, 'Each visit starts a fresh conversation. Earlier chats are not replayed here; they live in your memory web and are pulled in only when they are relevant to what you ask.'),
        el('div', { class: 'suggest' }, ...suggestions.map((s) => el('button', { class: 'btn btn-sm', type: 'button', onclick: () => { textarea.value = s; composer.requestSubmit(); } }, s)))));
    }

    // Current conversation only (kept while this page is open); no stored history is replayed.
    if (askState.transcript.length) for (const m of askState.transcript) addMessage(m.role, m.content, m.at);
    else showEmpty();
    messages.scrollTop = messages.scrollHeight;
    function newChat() {
      askState.controller?.abort();
      askState.sessionId = null; askState.transcript = [];
      messages.innerHTML = ''; showEmpty(); statusLine.innerHTML = ''; recallBox.innerHTML = ''; sessionBox.innerHTML = '';
      recallBox.appendChild(el('p', { class: 'faint small' }, 'Send a message to see which memories were recalled and why.'));
      toast('Fresh conversation started', 'ok'); textarea.focus();
    }

    function tierPill(r) {
      const tier = String(r.tier || (r.node_type === 'surface' ? 'surface' : r.memory_kind === 'durable' ? 'durable' : 'in-depth')).replace(/_/g, '-');
      return pill(tier, tier === 'surface' ? 'lav' : tier === 'durable' ? 'ok' : 'accent');
    }
    function recallItem(r, max) {
      const f = r.factors || {};
      const go = () => window.location.assign(`/memory-viewer#focus=${encodeURIComponent(r.id)}`);
      return el('div', { class: `recall-item ${r.tier === 'surface' ? 'is-surface' : ''}`, role: 'button', tabindex: 0, onclick: go, onkeydown: (e) => { if (e.key === 'Enter') go(); } },
        el('div', { class: 'row-between' }, el('div', { class: 't' }, r.title || 'Untitled'), tierPill(r)),
        el('div', { class: 'score-bar' + (r.tier === 'surface' ? ' lav' : ''), style: { '--w': `${Math.round((r.score / max) * 100)}%` } }, el('i')),
        el('div', { class: 'chips' },
          el('span', { class: 'chip' }, 'score ', el('b', {}, Number(r.score).toFixed(3))),
          el('span', { class: 'chip' }, 'text ', el('b', {}, Number(f.text_cosine ?? 0).toFixed(2))),
          el('span', { class: 'chip' }, 'lexical ', el('b', {}, Number(f.lexical_bm25 ?? 0).toFixed(2))),
          el('span', { class: 'chip' }, 'geometry ', el('b', {}, Number(f.geometry_cosine ?? 0).toFixed(2))),
          f.unified_context !== undefined && f.unified_context !== null ? el('span', { class: 'chip chip-lav', title: 'Unified context: this memory’s own cosine blended with its surface memory’s score' }, 'unified ', el('b', {}, Number(f.unified_context).toFixed(2))) : null,
          f.surface_score !== undefined && f.surface_score !== null && r.tier !== 'surface' ? el('span', { class: 'chip', title: 'Score of the surface memory this belongs to' }, 'surface ', el('b', {}, Number(f.surface_score).toFixed(2))) : null,
          f.trigger_boost ? el('span', { class: 'chip' }, 'trigger ', el('b', {}, `+${Number(f.trigger_boost).toFixed(2)}`)) : null,
          f.superseded ? pill('stale', 'warn') : null,
          r.member_count ? el('span', { class: 'chip' }, el('b', {}, r.member_count), ' memories') : null),
        el('div', { class: 'faint small' }, r.provenance || r.memory_kind || ''));
    }
    function renderRecall(data) {
      recallBox.innerHTML = '';
      const results = data.results || [];
      const depth = data.depth_used || 'auto';
      const surfacesRead = (data.surfaces || []).length ? data.surfaces : results.filter((r) => r.tier === 'surface');
      recallBox.appendChild(el('div', { class: 'row small faint' },
        el('span', {}, `mode ${data.mode || 'hybrid'}`),
        data.weights ? el('span', {}, `· text ${Math.round(data.weights.text * 100)}% / geometry ${Math.round(data.weights.geometry * 100)}%`) : null,
        pill(`Deep dive: ${depth === 'deep' ? 'on' : 'off'}`, depth === 'deep' ? 'lav' : 'muted')));
      if (!results.length && !surfacesRead.length) { recallBox.appendChild(el('p', { class: 'faint small' }, 'No memories scored above zero for this prompt.')); return; }
      const max = Math.max(...results.map((r) => r.score || 0), ...surfacesRead.map((r) => r.score || 0), 0.0001);
      if (surfacesRead.length) {
        recallBox.appendChild(el('div', { class: 'recall-group' }, el('span', { class: 'recall-group-title' }, 'Surface memories read first'), el('span', { class: 'faint small' }, 'distilled core meanings')));
        for (const sm of surfacesRead) {
          const full = results.find((r) => r.id === sm.id) || { ...sm, tier: 'surface', factors: sm.factors || {} };
          recallBox.appendChild(recallItem({ ...full, tier: 'surface', member_count: sm.member_count ?? full.member_count }, max));
        }
      }
      const rest = results.filter((r) => r.tier !== 'surface' && !surfacesRead.some((sm) => sm.id === r.id));
      if (rest.length) {
        recallBox.appendChild(el('div', { class: 'recall-group' }, el('span', { class: 'recall-group-title' }, depth === 'deep' ? 'In-depth and durable memories (deep dive)' : 'In-depth and durable memories'), el('span', { class: 'faint small' }, 'full question and answer pairs behind the surfaces')));
        for (const r of rest) recallBox.appendChild(recallItem(r, max));
      }
    }

    async function send(text, continuationOf) {
      if (askState.controller) return;
      if (!continuationOf) { const at = new Date().toISOString(); addMessage('user', text, at); askState.transcript.push({ role: 'user', content: text, at }); }
      const empty = $('.hero-empty', messages); if (empty) empty.remove();
      const { wrap, bubble, metaRow } = addMessage('assistant', '', null);
      bubble.classList.add('streaming');
      const thoughts = el('details', { class: 'thought-panel', hidden: true }, el('summary', {}, el('span', { class: 'live' }), 'Thought stream'), el('pre'));
      const badges = el('div', { class: 'msg-badges' });
      wrap.insertBefore(thoughts, bubble); wrap.appendChild(badges);
      let full = '', thoughtText = '', raf = 0, finished = false;
      const paint = () => { raf = 0; bubble.innerHTML = md.render(full); messages.scrollTop = messages.scrollHeight; };
      const schedule = () => { if (!raf) raf = requestAnimationFrame(paint); };
      const setStatus = (t, live) => { statusLine.innerHTML = ''; if (live) statusLine.appendChild(el('span', { class: 'live' })); statusLine.appendChild(el('span', {}, t)); };
      askState.controller = new AbortController();
      sendBtn.disabled = true; stopBtn.hidden = false; setStatus('Contacting PODA…', true);
      const body = { message: text, mode: askState.mode, thinking_level: askState.thinking, use_memory: askState.useMemory, session_id: askState.sessionId };
      if (continuationOf) body.continuation_of = continuationOf;
      try {
        const res = await api('/chat/stream', { method: 'POST', body, signal: askState.controller.signal, raw: true });
        if (!res.ok || !res.body) { let d = null; try { d = await res.json(); } catch { /* ignore */ } throw new window.PODA.ApiError(res.status, d?.detail || `HTTP ${res.status}`); }
        const reader = res.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const frames = buffer.split('\n\n'); buffer = frames.pop() || '';
          for (const frame of frames) {
            let event = 'message', dataText = '';
            for (const line of frame.split('\n')) { if (line.startsWith('event:')) event = line.slice(6).trim(); else if (line.startsWith('data:')) dataText += line.slice(5).trim(); }
            if (!dataText) continue;
            let data; try { data = JSON.parse(dataText); } catch { continue; }
            switch (event) {
              case 'session': if (data.session_id) askState.sessionId = data.session_id; break;
              case 'status': setStatus(data.message === 'routing' ? `Routing to ${data.model} (${data.profile || ''}, ${data.num_ctx ? data.num_ctx / 1024 + 'K ctx' : ''})` : data.message === 'recalling' ? 'Recalling memories…' : data.message === 'cancelled' ? 'Cancelled' : `${data.message} · ${data.model || ''}`, data.message !== 'cancelled'); break;
              case 'recall': renderRecall(data); badges.appendChild(pill(`${(data.surfaces || (data.results || []).filter((r) => r.tier === 'surface')).length ? `${(data.surfaces || (data.results || []).filter((r) => r.tier === 'surface')).length} surface · ` : ''}${(data.results || []).filter((r) => r.tier !== 'surface').length} memories recalled`, 'lav')); if (data.depth_used === 'deep') badges.appendChild(pill('deep dive', 'lav')); break;
              case 'thought': thoughtText += data.token || ''; thoughts.hidden = false; $('pre', thoughts).textContent = thoughtText.slice(-6000); break;
              case 'token': full += data.token || ''; schedule(); break;
              case 'tool': {
                const ok = ['success', 'succeeded', 'verified'].includes(String(data.outcome || ''));
                if (data.name === 'memory_dive') { badges.appendChild(el('span', { class: 'receipt-chip dive', title: data.args_summary || '' }, pill(ok ? 'read' : (data.outcome || 'ran'), ok ? 'lav' : 'danger'), el('span', {}, `Read in-depth memory: ${data.surface_title || data.args_summary || 'surface'}`), data.receipt_id ? el('span', { class: 'mono' }, `#${String(data.receipt_id).slice(0, 8)}`) : null)); break; }
                badges.appendChild(el('span', { class: 'receipt-chip', title: data.args_summary || '' }, pill(data.outcome || 'ran', ok ? 'ok' : data.outcome === 'failed' ? 'danger' : 'accent'), el('span', {}, data.name), data.verification ? el('span', { class: 'mono' }, data.verification) : null, data.receipt_id ? el('span', { class: 'mono' }, `#${String(data.receipt_id).slice(0, 8)}`) : null)); break;
              }
              case 'receipt': badges.appendChild(el('span', { class: 'receipt-chip' }, pill(data.outcome || 'receipt', data.outcome === 'success' ? 'ok' : 'accent'), el('span', {}, data.tool || 'action'), el('span', { class: 'mono' }, `#${String(data.id || data.receipt_id || '').slice(0, 8)}`))); break;
              case 'proposal': renderProposal(wrap, data); break;
              case 'done': {
                finished = true;
                const tps = data.eval_count && data.eval_duration_ms ? (data.eval_count / (data.eval_duration_ms / 1000)).toFixed(1) : null;
                metaRow.append(el('span', {}, fmtTime(new Date().toISOString())), pill(data.model || 'PODA', data.profile === 'gate' || data.profile === 'ledger' ? 'accent' : 'muted'), tps ? el('span', { class: 'mono' }, `${tps} tok/s`) : null);
                badges.prepend(pill(data.profile === 'gate' ? 'Action gate' : data.profile === 'ledger' ? 'Capability ledger' : `Model · ${data.profile || 'auto'}`, 'accent'));
                sessionBox.innerHTML = ''; sessionBox.appendChild(kv([['Model', data.model || ''], ['Profile', data.profile || ''], ['Output tokens', data.eval_count ?? 'n/a'], ['Speed', tps ? `${tps} tok/s` : 'n/a'], ['Prompt tokens', data.prompt_eval_count ?? 'n/a'], ['Session', askState.sessionId ? askState.sessionId.slice(0, 8) : 'new']]));
                setStatus(`Done · ${data.model || ''}`, false);
                askState.transcript.push({ role: 'assistant', content: full, at: new Date().toISOString() });
                break;
              }
              case 'error': full = full || ''; bubble.innerHTML = ''; bubble.appendChild(el('div', { class: 'notice notice-danger' }, data.message || 'PODA error')); setStatus('Error', false); finished = true; break;
              default: break;
            }
          }
        }
        if (raf) cancelAnimationFrame(raf);
        bubble.innerHTML = md.render(full) || bubble.innerHTML;
        if (!finished) setStatus('Stream ended', false);
      } catch (err) {
        if (err.name === 'AbortError') { bubble.innerHTML = md.render(full) + (full ? '' : '<p class="faint">Cancelled.</p>'); setStatus('Cancelled', false); }
        else { bubble.innerHTML = ''; bubble.appendChild(errorNode(err)); setStatus('Failed', false); }
      } finally {
        bubble.classList.remove('streaming'); askState.controller = null; sendBtn.disabled = false; stopBtn.hidden = true; messages.scrollTop = messages.scrollHeight;
        if (!thoughtText) thoughts.remove(); else $('.live', thoughts)?.remove();
      }
    }

    function renderProposal(wrap, data) {
      const card = el('div', { class: 'proposal', role: 'group', 'aria-label': 'Proposed actions' },
        el('div', { class: 'row-between' }, el('strong', {}, 'PODA proposes these actions'), pill('needs approval', 'lav')),
        data.rationale ? el('p', { class: 'small muted' }, data.rationale) : null,
        el('ol', {}, ...(data.steps || []).map((s) => el('li', {}, el('span', {}, s.summary || s.tool), ' ', el('span', { class: 'chip' }, s.tool), s.requires_confirmation ? pill('confirm', 'warn') : null))));
      const approve = el('button', { class: 'btn btn-primary btn-sm' }, 'Approve and run');
      const reject = el('button', { class: 'btn btn-ghost btn-sm' }, 'Reject');
      approve.onclick = async () => {
        approve.disabled = reject.disabled = true;
        try {
          const r = await api(`/agent/proposals/${encodeURIComponent(data.proposal_id)}/approve`, { method: 'POST' });
          card.appendChild(el('div', { class: 'row' }, ...((r.receipts || []).map((rc) => el('span', { class: 'receipt-chip' }, pill(rc.outcome || 'done', rc.outcome === 'success' ? 'ok' : 'danger'), rc.tool, el('span', { class: 'mono' }, `#${String(rc.id || '').slice(0, 8)}`))))));
          card.appendChild(pill('approved', 'ok'));
          await send('Report the outcome using the receipts.', data.proposal_id);
        } catch (err) { card.appendChild(errorNode(err)); approve.disabled = reject.disabled = false; }
      };
      reject.onclick = async () => { approve.disabled = reject.disabled = true; try { await api(`/agent/proposals/${encodeURIComponent(data.proposal_id)}/reject`, { method: 'POST' }); card.appendChild(pill('rejected', 'danger')); } catch (err) { card.appendChild(errorNode(err)); } };
      card.appendChild(el('div', { class: 'row' }, approve, reject));
      wrap.appendChild(card); messages.scrollTop = messages.scrollHeight;
    }

    composer.addEventListener('submit', (e) => { e.preventDefault(); const text = textarea.value.trim(); if (!text) return; textarea.value = ''; textarea.style.height = 'auto'; send(text); });
    stopBtn.addEventListener('click', () => askState.controller?.abort());
    textarea.focus();
    return { teardown: () => askState.controller?.abort() };
  });

  // =====================================================================
  // PERSONALIZE / IMPORT LLM HISTORY
  // =====================================================================
  route('personalize', async ({ view, inspector, setTitle, setInspectorTitle }) => {
    setTitle('Personalize'); setInspectorTitle('Import privacy');
    $('#viewCrumb').textContent = 'Bring your existing AI history into PODA';

    const fileInput = el('input', { class: 'input', type: 'file', accept: '.zip,application/zip', 'aria-label': 'ChatGPT or Claude data export ZIP' });
    const profileToggle = el('input', { type: 'checkbox', checked: true });
    const importBtn = el('button', { class: 'btn btn-primary', type: 'button' }, 'Import into PODA');
    const resultBox = el('div', { class: 'stack-sm' });
    const historyBox = el('div', { class: 'stack-sm' });

    const refreshHistory = async () => {
      historyBox.innerHTML = '';
      try {
        const r = await api('/imports/history');
        const items = r.imports || [];
        if (!items.length) { historyBox.appendChild(el('p', { class: 'faint small' }, 'No LLM archives imported yet.')); return; }
        const table = el('table', { class: 'table' }, el('thead', {}, el('tr', {}, ...['Provider','Archive','Conversations','Memories','Clouds','Imported'].map((h) => el('th', {}, h)))));
        const body = el('tbody');
        for (const item of items) body.appendChild(el('tr', {}, el('td', {}, item.provider || ''), el('td', {}, item.source_filename || ''), el('td', {}, String(item.conversation_count || 0)), el('td', {}, String(item.memory_count || 0)), el('td', {}, String(item.cloud_count || 0)), el('td', {}, fmtTime(item.created_at))));
        table.appendChild(body); historyBox.appendChild(table);
      } catch (err) { historyBox.appendChild(errorNode(err)); }
    };

    importBtn.onclick = async () => {
      const file = fileInput.files?.[0];
      if (!file) { toast('Choose a ChatGPT or Claude export ZIP first.', 'danger'); return; }
      if (!(await confirmDialog({ title: 'Import this private conversation archive?', body: 'PODA will parse it locally, convert conversations into visible second-brain memories, organize them into semantic topic clouds, and then delete the temporary ZIP. The raw ZIP is not retained. This can permanently personalize future answers.', confirmLabel: 'Import locally' }))) return;
      importBtn.disabled = true; importBtn.textContent = 'IMPORTING…'; resultBox.innerHTML = '';
      const form = new FormData(); form.append('file', file); form.append('apply_profile', profileToggle.checked ? 'true' : 'false');
      try {
        const res = await fetch('/imports/llm-export', { method: 'POST', body: form, credentials: 'same-origin', cache: 'no-store' });
        const text = await res.text(); let data = null; try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
        if (!res.ok) throw new ApiError(res.status, data?.detail ?? data);
        if (data.status === 'already_imported') {
          resultBox.appendChild(el('div', { class: 'notice notice-warn' }, `This exact archive was already imported. Existing import: ${data.conversations || 0} conversations, ${data.memories || 0} memories.`));
        } else {
          resultBox.appendChild(el('div', { class: 'notice notice-ok' }, `Imported ${data.conversations || 0} conversations / ${data.messages || 0} messages into ${data.memories || 0} visible memories and ${(data.clouds || []).length} topic clouds.`));
          if (data.profile?.summary) resultBox.appendChild(section('Personalization learned', data.profile.source || '', el('p', {}, data.profile.summary), el('p', { class: 'faint small' }, 'This derived profile is visible in the second brain and is supplied to PODA as fallible personalization context.')));
          resultBox.appendChild(el('div', { class: 'row' }, el('button', { class: 'btn', onclick: () => window.location.assign('/memory-viewer') }, 'Open second brain')));
          toast('LLM history imported locally', 'ok');
        }
        await refreshHistory();
      } catch (err) { resultBox.appendChild(errorNode(err)); }
      finally { importBtn.disabled = false; importBtn.textContent = 'Import into PODA'; }
    };

    view.appendChild(el('div', { class: 'screen stack' },
      section('Import your main AI data export', 'ChatGPT · Claude',
        el('div', { class: 'import-hero' },
          el('div', {}, el('h3', {}, 'Turn old AI conversations into your PODA second brain'), el('p', { class: 'muted' }, 'Upload the ZIP from the provider’s official data export. PODA parses it on this Mac, preserves user/assistant provenance, creates visible memory points, groups conversations into high-level semantic clouds, and can derive a bounded workflow/response profile from user-authored text.')),
          el('div', { class: 'field' }, el('label', { class: 'label' }, 'Data export ZIP'), fileInput),
          el('label', { class: 'check' }, profileToggle, 'Use user-authored history to personalize workflow and response style'),
          el('div', { class: 'row' }, importBtn))),
      resultBox,
      section('Import history', 'local metadata only', historyBox)));

    inspector.append(
      el('div', { class: 'notice notice-ok' }, 'Local processing only. The uploaded ZIP is staged with 0600 permissions and deleted after parsing.'),
      el('div', { class: 'stack-sm' }, el('h3', {}, 'What becomes memory'),
        el('p', { class: 'small muted' }, 'User + assistant turns become visible exchange nodes. Assistant text remains contextual rather than authoritative. Imported profile claims are derived only from user-authored messages.')),
      el('div', { class: 'stack-sm' }, el('h3', {}, 'Supported exports'),
        el('p', { class: 'small muted' }, 'Official ChatGPT exports with conversations.json and Claude exports containing conversations/chat_messages. Nested JSON exports are detected heuristically. Attachments are ignored.')));
    await refreshHistory();
  });

  // =====================================================================
  // PLAN
  // =====================================================================
  route('plan', async ({ view, inspector, setTitle, setInspectorTitle }) => {
    setTitle('Plan'); setInspectorTitle('How ranking works'); $('#viewCrumb').textContent = 'Explainable priorities';
    inspector.append(el('p', { class: 'small muted' }, 'Scores combine deadline pressure, importance, effort, estimated completion time, and progress. They are recommendations with a rationale, not objective truth. Nothing is scheduled or changed automatically.'),
      el('p', { class: 'small faint' }, 'Live Notion data joins the ranking only after read access is verified. Email-derived deadlines stay unconfirmed until you accept them.'));
    const rankBox = el('div', { class: 'stack-sm' });
    const tableBox = el('div');
    const form = el('form', { class: 'stack-sm' });
    const f = (name, label, type = 'text', extra = {}) => { const input = el('input', { class: 'input', name, type, ...extra }); return el('div', { class: 'field' }, el('label', { class: 'label', for: `p-${name}` }, label), Object.assign(input, { id: `p-${name}` })); };
    form.append(f('name', 'Project name', 'text', { required: true, placeholder: 'e.g. Thesis draft' }),
      el('div', { class: 'grid-3' }, f('importance', 'Importance (1-10)', 'number', { min: 1, max: 10, value: 5 }), f('difficulty', 'Difficulty (1-10)', 'number', { min: 1, max: 10, value: 5 }), f('hours_remaining', 'Hours remaining', 'number', { min: 0, step: 0.5, value: 5 })),
      el('div', { class: 'grid-2' }, f('deadline', 'Deadline', 'date'), f('progress', 'Progress %', 'number', { min: 0, max: 100, value: 0 })),
      el('div', { class: 'field' }, el('label', { class: 'label', for: 'p-notes' }, 'Notes'), el('textarea', { class: 'input', name: 'notes', id: 'p-notes', rows: 2 })),
      el('div', { class: 'row' }, el('button', { class: 'btn btn-primary', type: 'submit' }, 'Add project')));
    form.addEventListener('submit', async (e) => {
      e.preventDefault(); const fd = new FormData(form);
      try {
        await api('/projects', { method: 'POST', body: { name: fd.get('name'), importance: +fd.get('importance'), difficulty: +fd.get('difficulty'), hours_remaining: +fd.get('hours_remaining'), deadline: fd.get('deadline') || null, progress: +fd.get('progress'), notes: fd.get('notes') || '' } });
        form.reset(); toast('Project added', 'ok'); refresh();
      } catch (err) { toast(errorDetail(err).message, 'danger'); }
    });
    view.appendChild(el('div', { class: 'screen grid-main-side' }, el('div', { class: 'stack' }, section('Recommended order', 'ranked by the local planner', rankBox), section('Projects', null, tableBox)), section('Add a project', null, form)));

    async function refresh() {
      await Promise.all([
        load(rankBox, async () => {
          const r = await api('/projects/prioritize'); const ranked = r.ranked || [];
          if (!ranked.length) return emptyState('No projects yet', 'Add a project to see an explainable ranking.');
          return el('div', { class: 'stack-sm' }, ...ranked.map((p) => el('div', { class: `rank-item ${p.rank === 1 ? 'top' : ''}` }, el('div', { class: 'rank-n' }, `#${p.rank}`),
            el('div', { class: 'stack-sm' },
              el('div', { class: 'row-between' }, el('strong', {}, p.name), el('div', { class: 'row' }, pill(p.recommendation, p.rank === 1 ? 'accent' : 'muted'), el('span', { class: 'chip' }, 'score ', el('b', {}, p.score)))),
              el('div', { class: 'small muted' }, p.rationale),
              el('div', { class: 'row small faint' }, el('span', {}, `~${p.estimated_duration_hours}h`), p.days_left !== null && p.days_left !== undefined ? el('span', {}, `· ${p.days_left} days left`) : null, el('span', {}, `· ${p.data_source}`)),
              el('div', { class: 'row', style: { alignItems: 'center' } }, el('span', { class: 'faint small' }, 'confidence'), el('div', { class: 'meter', style: { flex: '1', maxWidth: '180px', '--w': `${Math.round((p.confidence || 0) * 100)}%` } }, el('i')), el('span', { class: 'mono small' }, `${Math.round((p.confidence || 0) * 100)}%`))))));
        }),
        load(tableBox, async () => {
          const r = await api('/projects'); const projects = r.projects || [];
          if (!projects.length) return emptyState('No project records', 'Projects are stored locally in the encrypted database.');
          const table = el('table', { class: 'table' }, el('thead', {}, el('tr', {}, ...['Name', 'Imp', 'Diff', 'Hours', 'Deadline', 'Progress', ''].map((h) => el('th', {}, h)))));
          const body = el('tbody');
          for (const p of projects) {
            body.appendChild(el('tr', {}, el('td', {}, el('strong', {}, p.name), p.notes ? el('div', { class: 'faint small' }, p.notes) : null), el('td', { class: 'num' }, p.importance), el('td', { class: 'num' }, p.difficulty), el('td', { class: 'num' }, p.hours_remaining), el('td', {}, p.deadline || el('span', { class: 'faint' }, 'none')), el('td', { class: 'num' }, `${p.progress}%`),
              el('td', {}, el('div', { class: 'row' },
                el('button', { class: 'btn btn-sm btn-ghost', onclick: async () => { const v = await promptDialog({ title: 'Update progress', label: `Progress % for ${p.name}`, value: String(p.progress) }); if (v === null) return; try { await api(`/projects/${p.id}`, { method: 'PUT', body: { ...p, progress: Math.max(0, Math.min(100, +v || 0)) } }); refresh(); } catch (err) { toast(errorDetail(err).message, 'danger'); } } }, 'Edit'),
                el('button', { class: 'btn btn-sm btn-ghost', onclick: async () => { if (!(await confirmDialog({ title: `Delete ${p.name}?`, body: 'This removes the local project record.', confirmLabel: 'Delete', danger: true }))) return; await api(`/projects/${p.id}`, { method: 'DELETE' }); refresh(); } }, 'Delete')))));
          }
          table.appendChild(body); return table;
        }),
      ]);
    }
    refresh();
  });

  // =====================================================================
  // NOTION
  // =====================================================================
  route('notion', async ({ view, inspector, setTitle, setInspectorTitle }) => {
    setTitle('Notion'); setInspectorTitle('Connection status'); $('#viewCrumb').textContent = 'Original database behind Notion Calendar';
    const statusHead = el('div', { class: 'stack-sm' });
    const stepsBar = el('div', { class: 'steps' });
    const tokenBox = el('div', { class: 'stack-sm' });
    const sourceBox = el('div', { class: 'stack-sm' });
    const schemaBox = el('div', { class: 'stack-sm' });
    const writeBox = el('div', { class: 'stack-sm' });
    const mapperBox = el('div', { class: 'stack-sm' });
    const previewBox = el('div', { class: 'stack-sm' });
    const rowsBox = el('div', { class: 'stack-sm' });
    const calendarBox = el('div', { class: 'stack-sm' });
    const calDetail = el('div', { class: 'stack-sm' });
    inspector.append(statusHead, el('div', { class: 'divider' }), el('div', { class: 'stack-sm' }, el('h3', {}, 'Selected calendar entry'), calDetail));
    calDetail.appendChild(el('p', { class: 'faint small' }, 'Click an entry in the calendar to see its properties here.'));
    view.appendChild(el('div', { class: 'screen stack' },
      el('div', { class: 'notice notice-accent' }, 'PODA connects to the original Notion database or data source, never to a linked view. Share the original source with the PODA connection from its page menu: ••• then Connections then Add connections. The token is stored in macOS Keychain and never shown again.'),
      stepsBar,
      section('Calendar', 'what Notion Calendar shows for this database · read-only', calendarBox),
      el('div', { class: 'grid-2' }, section('1. Integration token', null, tokenBox), section('2. Choose the data source', null, sourceBox)),
      el('div', { class: 'grid-2' }, section('3. Live schema', null, schemaBox), section('4. Write verification', null, writeBox)),
      section('Schema mapper', 'versioned; preview before trusting', mapperBox, previewBox),
      section('Live rows', 'read-only', rowsBox)));

    let status = null;
    function drawSteps(st) {
      const tokenOk = !!(st?.token_present ?? st?.capability?.token_storage);
      const connected = (st?.status || st?.capability?.status) === 'CONNECTED' || (st?.status || st?.capability?.status) === 'DEGRADED';
      const writeOk = !!(st?.capability?.can_insert_content && st?.capability?.can_update_content);
      const mapOk = !!(st?.capability?.mapping_active || st?.mapping);
      const steps = [['Token', tokenOk], ['Source', connected], ['Write test', writeOk], ['Mapping', mapOk]];
      stepsBar.innerHTML = '';
      let activeSet = false;
      steps.forEach(([t, done], i) => { const active = !done && !activeSet; if (active) activeSet = true; stepsBar.appendChild(el('div', { class: `step ${done ? 'done' : ''} ${active ? 'active' : ''}` }, el('span', { class: 'n' }, done ? 'done' : `step ${i + 1}`), el('span', { class: 't' }, t))); });
    }
    async function refreshStatus(verify = false) {
      try { status = await api(`/notion/status${verify ? '?verify=true' : ''}`); }
      catch (err) { status = { error: err }; }
      const cap = status?.capability || {};
      const st = status?.status || cap.status || 'DISCONNECTED';
      statusHead.innerHTML = '';
      statusHead.append(el('div', { class: 'row' }, statusPill(st), cap.workspace_name ? pill(cap.workspace_name, 'muted') : null),
        el('p', { class: 'small muted' }, status?.status_detail || cap.status_detail || (status?.error ? errorDetail(status.error).message : 'No Notion database is connected.')),
        kv([['Database', cap.database_title || cap.database_id], ['Data source', cap.data_source_name || cap.data_source_id], ['Read', yesNo(cap.can_read_content)], ['Insert', yesNo(cap.can_insert_content)], ['Update', yesNo(cap.can_update_content)], ['Mapping', yesNo(cap.mapping_active)], ['API version', cap.api_version], ['Last verified', fmtTime(status?.last_verified_at || status?.config?.last_verified_at)]]),
        el('div', { class: 'row' }, el('button', { class: 'btn btn-sm', onclick: () => refreshStatus(true) }, 'Verify now'), el('button', { class: 'btn btn-sm btn-danger', onclick: disconnect }, 'Disconnect')));
      drawSteps(status);
      drawCalendar(); drawToken(); drawSource(); drawSchema(); drawWrite(); drawMapper(); drawRows();
    }
    async function disconnect() {
      if (!(await confirmDialog({ title: 'Disconnect Notion?', body: 'The stored token is deleted from macOS Keychain and PODA loses all Notion capabilities immediately.', confirmLabel: 'Disconnect', danger: true }))) return;
      try { await api('/notion/disconnect', { method: 'POST' }); toast('Notion disconnected and token revoked', 'ok'); refreshStatus(); } catch (err) { toast(errorDetail(err).message, 'danger'); }
    }

    // ----- Calendar (month / week / agenda) over GET /notion/calendar -----
    const calState = (() => { try { return JSON.parse(sessionStorage.getItem('poda_notion_cal') || '{}'); } catch { return {}; } })();
    calState.view = calState.view || 'month';
    calState.cursor = calState.cursor ? new Date(calState.cursor) : new Date();
    const dayKey = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
    const startOfWeek = (d) => { const x = new Date(d); x.setHours(0, 0, 0, 0); x.setDate(x.getDate() - x.getDay()); return x; };
    const parseWhen = (iso) => { if (!iso) return null; if (!String(iso).includes('T')) { const [y, m, d] = String(iso).split('-').map(Number); return new Date(y, m - 1, d); } return new Date(iso); };
    const fmtClock = (d) => d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    const typeClass = (() => { const seen = new Map(); return (t) => { const key = String(t || 'untyped'); if (!seen.has(key)) seen.set(key, seen.size % 8); return `t${seen.get(key)}`; }; })();
    function calRange() {
      const c = calState.cursor;
      if (calState.view === 'week') { const s = startOfWeek(c); return { start: s, end: addDays(s, 6), label: `${s.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} – ${addDays(s, 6).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}` }; }
      if (calState.view === 'agenda') { const s = new Date(c); s.setHours(0, 0, 0, 0); return { start: s, end: addDays(s, 13), label: `${s.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} – ${addDays(s, 13).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}` }; }
      const first = new Date(c.getFullYear(), c.getMonth(), 1); const last = new Date(c.getFullYear(), c.getMonth() + 1, 0);
      return { start: startOfWeek(first), end: addDays(startOfWeek(last), 6), label: c.toLocaleDateString(undefined, { month: 'long', year: 'numeric' }), month: c.getMonth() };
    }
    function showEvent(ev) {
      calDetail.innerHTML = '';
      const s = parseWhen(ev.start); const e = parseWhen(ev.end);
      const when = ev.all_day ? `${s.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })}${e && dayKey(e) !== dayKey(s) ? ` → ${e.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })}` : ''} · all day`
        : `${s.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })} · ${fmtClock(s)}${e ? ` – ${fmtClock(e)}` : ''}`;
      calDetail.append(el('div', { class: 'row' }, el('strong', {}, ev.title), ev.type ? el('span', { class: `cal-type ${typeClass(ev.type)}` }, ev.type) : null),
        kv([['When', when], ['Status', ev.status], ['Commitment', ev.commitment], ['Area', (ev.area || []).join(', ')], ['Source', ev.source], ['Notes', ev.notes], ['Edited', fmtTime(ev.last_edited_time)]]),
        el('div', { class: 'row' }, ev.url ? el('a', { class: 'btn btn-sm', href: ev.url, target: '_blank', rel: 'noopener' }, 'Open in Notion') : null, ev.source_url ? el('a', { class: 'btn btn-sm btn-ghost', href: ev.source_url, target: '_blank', rel: 'noopener' }, 'Open source') : null));
    }
    function eventNode(ev, compact) {
      const s = parseWhen(ev.start);
      const node = el('button', { class: `cal-ev ${typeClass(ev.type)} ${ev.done ? 'done' : ''}`, type: 'button', title: `${ev.title}${ev.type ? ' · ' + ev.type : ''}${ev.commitment ? ' · ' + ev.commitment : ''}`, onclick: () => showEvent(ev) },
        ev.all_day ? null : el('span', { class: 'cal-time' }, fmtClock(s)), el('span', { class: 'cal-title' }, ev.title));
      if (!compact && (ev.status || ev.commitment)) node.appendChild(el('span', { class: 'cal-sub' }, [ev.status, ev.commitment].filter(Boolean).join(' · ')));
      return node;
    }
    function drawCalendar() {
      calendarBox.innerHTML = '';
      const cap = status?.capability || {};
      if (!cap.connected) { calendarBox.appendChild(emptyState('Not connected', 'The calendar reads the connected data source’s date property once a source is connected.')); return; }
      const range = calRange();
      const nav = el('div', { class: 'cal-nav' },
        el('div', { class: 'row' },
          el('button', { class: 'btn btn-sm', type: 'button', 'aria-label': 'Previous', onclick: () => { calState.cursor = calState.view === 'month' ? new Date(calState.cursor.getFullYear(), calState.cursor.getMonth() - 1, 1) : addDays(calState.cursor, calState.view === 'week' ? -7 : -14); persist(); drawCalendar(); } }, '‹'),
          el('button', { class: 'btn btn-sm', type: 'button', onclick: () => { calState.cursor = new Date(); persist(); drawCalendar(); } }, 'Today'),
          el('button', { class: 'btn btn-sm', type: 'button', 'aria-label': 'Next', onclick: () => { calState.cursor = calState.view === 'month' ? new Date(calState.cursor.getFullYear(), calState.cursor.getMonth() + 1, 1) : addDays(calState.cursor, calState.view === 'week' ? 7 : 14); persist(); drawCalendar(); } }, '›'),
          el('h3', { class: 'cal-label' }, range.label)),
        el('div', { class: 'row' },
          (() => { const wrap = el('div', { class: 'seg', role: 'group', 'aria-label': 'Calendar view' }); for (const [v, l] of [['month', 'Month'], ['week', 'Week'], ['agenda', 'Agenda']]) { const b = el('button', { type: 'button', 'aria-pressed': calState.view === v ? 'true' : 'false', onclick: () => { calState.view = v; persist(); drawCalendar(); } }, l); wrap.appendChild(b); } return wrap; })(),
          el('button', { class: 'btn btn-sm btn-ghost', type: 'button', onclick: () => drawCalendar() }, 'Refresh')));
      const body = el('div');
      calendarBox.append(nav, body);
      function persist() { try { sessionStorage.setItem('poda_notion_cal', JSON.stringify({ view: calState.view, cursor: calState.cursor.toISOString() })); } catch { /* ignore */ } }
      load(body, async () => {
        const r = await api(`/notion/calendar?start=${dayKey(range.start)}&end=${dayKey(range.end)}`);
        const events = r.events || [];
        const byDay = new Map();
        for (const ev of events) {
          const s = parseWhen(ev.start); const e = ev.end ? parseWhen(ev.end) : s;
          for (let d = new Date(s.getFullYear(), s.getMonth(), s.getDate()); d <= e && d <= range.end; d = addDays(d, 1)) { const k = dayKey(d); if (!byDay.has(k)) byDay.set(k, []); byDay.get(k).push(ev); }
        }
        const todayKey = dayKey(new Date());
        const legendTypes = [...new Set(events.map((e) => e.type).filter(Boolean))];
        const legend = el('div', { class: 'cal-legend row' }, el('span', { class: 'small faint' }, `${r.count} ${r.count === 1 ? 'entry' : 'entries'} · date property “${r.fields?.date}”${r.mapping_version ? ` · mapping v${r.mapping_version}` : ' · no mapping saved yet, using the obvious properties'} · shown in ${Intl.DateTimeFormat().resolvedOptions().timeZone}`),
          ...legendTypes.map((t) => el('span', { class: `cal-type ${typeClass(t)}` }, t)));
        if (calState.view === 'month') {
          const grid = el('div', { class: 'cal-grid' }, ...['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((d) => el('div', { class: 'cal-dow' }, d)));
          for (let d = new Date(range.start); d <= range.end; d = addDays(d, 1)) {
            const k = dayKey(d); const list = byDay.get(k) || [];
            const cell = el('div', { class: `cal-day ${d.getMonth() !== range.month ? 'other' : ''} ${k === todayKey ? 'today' : ''}` }, el('div', { class: 'cal-num' }, d.getDate() === 1 ? d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : String(d.getDate())));
            list.slice(0, 4).forEach((ev) => cell.appendChild(eventNode(ev, true)));
            if (list.length > 4) cell.appendChild(el('button', { class: 'cal-more', type: 'button', onclick: () => { calState.view = 'agenda'; calState.cursor = new Date(d); persist(); drawCalendar(); } }, `+${list.length - 4} more`));
            grid.appendChild(cell);
          }
          return el('div', { class: 'stack-sm' }, legend, grid);
        }
        if (calState.view === 'week') {
          const grid = el('div', { class: 'cal-grid week' });
          for (let d = new Date(range.start); d <= range.end; d = addDays(d, 1)) {
            const k = dayKey(d); const list = byDay.get(k) || [];
            const cell = el('div', { class: `cal-day tall ${k === todayKey ? 'today' : ''}` }, el('div', { class: 'cal-dow' }, d.toLocaleDateString(undefined, { weekday: 'short' })), el('div', { class: 'cal-num' }, String(d.getDate())));
            if (!list.length) cell.appendChild(el('span', { class: 'faint small' }, 'nothing'));
            list.forEach((ev) => cell.appendChild(eventNode(ev, false)));
            grid.appendChild(cell);
          }
          return el('div', { class: 'stack-sm' }, legend, grid);
        }
        const agenda = el('div', { class: 'cal-agenda' });
        let any = false;
        for (let d = new Date(range.start); d <= range.end; d = addDays(d, 1)) {
          const k = dayKey(d); const list = byDay.get(k) || [];
          if (!list.length) continue;
          any = true;
          agenda.appendChild(el('div', { class: `cal-agenda-day ${k === todayKey ? 'today' : ''}` }, el('div', { class: 'cal-agenda-date' }, el('strong', {}, d.toLocaleDateString(undefined, { weekday: 'long' })), el('span', { class: 'faint' }, d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }))), el('div', { class: 'stack-sm' }, ...list.map((ev) => eventNode(ev, false)))));
        }
        if (!any) agenda.appendChild(emptyState('Nothing scheduled', 'No pages fall in these two weeks.'));
        return el('div', { class: 'stack-sm' }, legend, agenda);
      });
    }

    function drawToken() {
      tokenBox.innerHTML = '';
      const present = !!(status?.token_present ?? status?.capability?.token_storage);
      const input = el('input', { class: 'input', type: 'password', placeholder: 'ntn_… or secret_…', autocomplete: 'off', 'aria-label': 'Notion integration token' });
      const out = el('div');
      const btn = el('button', { class: 'btn btn-primary' }, present ? 'Replace token' : 'Validate and store');
      btn.onclick = async () => {
        const token = input.value.trim(); if (!token) return; btn.disabled = true; out.innerHTML = '';
        try { const r = await api('/notion/token', { method: 'POST', body: { token } }); input.value = ''; out.appendChild(el('div', { class: 'notice notice-ok' }, `Token valid. Workspace ${r.workspace_name || 'unknown'} · bot ${r.bot_name || r.bot_id || ''}. Stored in Keychain.`)); refreshStatus(); }
        catch (err) { out.appendChild(errorNode(err)); } finally { btn.disabled = false; }
      };
      tokenBox.append(el('div', { class: 'row' }, present ? pill('token in Keychain', 'ok') : pill('no token', 'danger')),
        el('div', { class: 'field' }, el('label', { class: 'label' }, 'Internal integration token'), input, el('div', { class: 'help' }, 'Validated against the Notion identity endpoint. Only workspace and bot names are displayed; the token itself is never echoed or logged.')),
        el('div', { class: 'row' }, btn, present ? el('button', { class: 'btn btn-ghost', onclick: async () => { try { await api('/notion/token', { method: 'DELETE' }); toast('Token revoked', 'ok'); refreshStatus(); } catch (err) { toast(errorDetail(err).message, 'danger'); } } }, 'Revoke token') : null), out);
    }

    function drawSource() {
      sourceBox.innerHTML = '';
      const present = !!(status?.token_present ?? status?.capability?.token_storage);
      if (!present) { sourceBox.appendChild(emptyState('Store a token first', 'Discovery and diagnosis require a validated token.')); return; }
      const search = el('input', { class: 'input', placeholder: 'Search accessible data sources by name', 'aria-label': 'Search data sources' });
      const list = el('div', { class: 'stack-sm' });
      const url = el('input', { class: 'input', placeholder: 'Paste a Notion database URL, data source ID, or the ID from an error message', 'aria-label': 'Notion URL or ID' });
      const diag = el('div', { class: 'stack-sm' });
      async function discover() {
        await load(list, async () => {
          const r = await api(`/notion/discover?query=${encodeURIComponent(search.value.trim())}`);
          const items = r.data_sources || r.results || [];
          if (!items.length) return emptyState('No accessible data sources', 'Share the original database with the PODA connection, then search again.');
          return el('div', { class: 'stack-sm' }, ...items.map((ds) => el('div', { class: 'cand', role: 'button', tabindex: 0, onclick: () => connect(ds.id, ds.database_id), onkeydown: (e) => { if (e.key === 'Enter') connect(ds.id, ds.database_id); } },
            el('div', { class: 'stack-sm', style: { gap: '2px', minWidth: 0 } }, el('strong', {}, ds.name || ds.title || 'Untitled source'), el('span', { class: 'mono' }, `${ds.database_title ? ds.database_title + ' · ' : ''}${ds.id}`)), el('span', { style: { marginLeft: 'auto' } }, pill('data source', 'accent')))),
            r.has_more ? el('p', { class: 'faint small' }, 'More results exist; refine the search.') : null);
        });
      }
      async function diagnose() {
        const value = url.value.trim(); if (!value) return;
        await load(diag, async () => {
          const parsed = await api('/notion/parse-url', { method: 'POST', body: { value } });
          const d = await api('/notion/diagnose', { method: 'POST', body: { value } });
          const out = el('div', { class: 'stack-sm' });
          out.appendChild(el('div', { class: 'row' }, ...(parsed.candidates || []).map((c) => el('span', { class: 'chip', title: c.hint || '' }, pill(c.kind, c.kind === 'view_id' ? 'warn' : 'accent'), el('b', {}, c.id)))));
          for (const w of parsed.warnings || []) out.appendChild(el('div', { class: 'notice notice-warn' }, w));
          for (const item of d.diagnosis || []) out.appendChild(el('div', { class: `diag sev-${item.severity || 'info'}` }, el('span', { class: 'code' }, item.code || ''), el('div', {}, item.message), item.remedy ? el('div', { class: 'remedy' }, item.remedy) : null));
          if (d.resolved?.data_source_id) out.appendChild(el('div', { class: 'row' }, el('button', { class: 'btn btn-primary', onclick: () => connect(d.resolved.data_source_id, d.resolved.database_id) }, 'Connect to resolved source'), el('span', { class: 'mono small' }, d.resolved.data_source_id)));
          if (!(d.diagnosis || []).length) out.appendChild(el('p', { class: 'faint small' }, 'No diagnosis returned.'));
          return out;
        });
      }
      async function connect(dataSourceId, databaseId) {
        try { toast('Connecting and verifying read access…'); const r = await api('/notion/connect', { method: 'POST', body: { data_source_id: dataSourceId, database_id: databaseId || null } }); toast(`Connected to ${r.data_source_name || r.database_title || 'data source'}`, 'ok'); refreshStatus(); }
        catch (err) { const d = errorDetail(err); toast(d.message, 'danger'); diag.innerHTML = ''; diag.appendChild(errorNode(err)); }
      }
      search.addEventListener('keydown', (e) => { if (e.key === 'Enter') discover(); });
      url.addEventListener('keydown', (e) => { if (e.key === 'Enter') diagnose(); });
      sourceBox.append(el('div', { class: 'field' }, el('label', { class: 'label' }, 'Discover by name'), el('div', { class: 'row' }, search, el('button', { class: 'btn', onclick: discover }, 'Search'))), list,
        el('div', { class: 'divider' }),
        el('div', { class: 'field' }, el('label', { class: 'label' }, 'Or diagnose a pasted URL or ID'), el('div', { class: 'row' }, url, el('button', { class: 'btn', onclick: diagnose }, 'Diagnose')), el('div', { class: 'help' }, 'Distinguishes the path ID from a view ID (?v=), block fragments, and data source IDs, then checks what the token can actually reach.')), diag);
      discover();
    }

    function drawSchema() {
      schemaBox.innerHTML = '';
      const cap = status?.capability || {};
      if (!cap.connected) { schemaBox.appendChild(emptyState('Not connected', 'Connect a data source to load its live property schema.')); return; }
      load(schemaBox, async () => {
        const s = await api('/notion/schema');
        const props = s.properties || s.schema || {};
        const related = s.related || status?.config?.related || [];
        const table = el('table', { class: 'table' }, el('thead', {}, el('tr', {}, el('th', {}, 'Property'), el('th', {}, 'Type'), el('th', {}, 'Relation target'))));
        const body = el('tbody');
        for (const [name, p] of Object.entries(props)) {
          const rel = p.relation || p.relation_target || null;
          body.appendChild(el('tr', {}, el('td', {}, name), el('td', {}, el('span', { class: 'chip' }, p.type || '')), el('td', {}, rel ? el('span', { class: 'row' }, el('span', { class: 'mono small' }, rel.data_source_id || rel.database_id || rel.target || ''), p.relation_accessible === false ? pill('not shared', 'danger') : p.relation_accessible ? pill('accessible', 'ok') : null) : el('span', { class: 'faint' }, ''))));
        }
        table.appendChild(body);
        const out = el('div', { class: 'stack-sm' }, el('div', { class: 'row' }, el('span', { class: 'chip' }, 'fingerprint ', el('b', {}, String(s.fingerprint || cap.schema_fingerprint || '').slice(0, 12))), s.changed_since_connect ? pill('schema changed since connect', 'warn') : pill('schema unchanged', 'ok')), table);
        for (const r of related) if (r.accessible === false) out.appendChild(el('div', { class: 'notice notice-warn' }, `Related database for "${r.property}" is not shared with PODA. Relation values will be IDs only until you share the original related database.`));
        return out;
      });
    }

    function drawWrite() {
      writeBox.innerHTML = '';
      const cap = status?.capability || {};
      if (!cap.connected) { writeBox.appendChild(emptyState('Not connected', 'Read access must be verified before any write test.')); return; }
      const out = el('div');
      const btn = el('button', { class: 'btn btn-primary' }, 'Run safe write test');
      btn.onclick = async () => {
        if (!(await confirmDialog({ title: 'Create and archive one test page?', body: 'PODA creates a clearly named test page in the connected data source, re-reads it, then archives it. Real pages are never touched. If cleanup fails, the leftover page ID is shown here.', confirmLabel: 'Run test' }))) return;
        btn.disabled = true; out.innerHTML = '';
        try { const r = await api('/notion/verify-write', { method: 'POST' }); out.appendChild(el('div', { class: `notice ${r.write_verified ? 'notice-ok' : 'notice-warn'}` }, `Insert ${r.insert_verified ? 'verified' : 'failed'} · update ${r.update_verified ? 'verified' : 'failed'} · test page ${r.test_page_archived ? 'archived' : 'NOT archived'} (${r.test_page_id || 'no id'})`)); refreshStatus(); }
        catch (err) { out.appendChild(errorNode(err)); } finally { btn.disabled = false; }
      };
      writeBox.append(el('div', { class: 'row' }, pill(`insert ${cap.can_insert_content ? 'verified' : 'unverified'}`, cap.can_insert_content ? 'ok' : 'warn'), pill(`update ${cap.can_update_content ? 'verified' : 'unverified'}`, cap.can_update_content ? 'ok' : 'warn')), el('div', { class: 'row' }, btn), out);
      load(el('div'), async () => null);
      const leftovers = el('div');
      writeBox.appendChild(leftovers);
      load(leftovers, async () => { const r = await api('/notion/test-pages'); const pages = (r.pages || r.test_pages || []).filter((p) => !p.archived); return pages.length ? el('div', { class: 'notice notice-warn' }, `${pages.length} leftover test page(s): ${pages.map((p) => p.page_id).join(', ')}. Remove them in Notion.`) : el('p', { class: 'faint small' }, 'No leftover test pages.'); }).catch(() => {});
    }

    function drawMapper() {
      mapperBox.innerHTML = ''; previewBox.innerHTML = '';
      const cap = status?.capability || {};
      if (!cap.connected) { mapperBox.appendChild(emptyState('Not connected', 'The mapper reads the live schema of the connected data source.')); return; }
      load(mapperBox, async () => {
        const [schema, mapping] = await Promise.all([api('/notion/schema'), api('/notion/mapping').catch(() => ({}))]);
        const props = schema.properties || schema.schema || {};
        const current = mapping?.active?.mapping || {};
        const currentVersion = mapping?.active?.version;
        const names = Object.keys(props);
        const byType = (types) => names.filter((n) => !types || types.includes(props[n].type));
        const select = (key, label, options, allowNone = true) => {
          const s = el('select', { class: 'input', name: key, 'aria-label': label });
          if (allowNone) s.appendChild(el('option', { value: '' }, 'none'));
          for (const o of options) s.appendChild(el('option', { value: o, selected: current[key] === o }, `${o} (${props[o]?.type || ''})`));
          return el('div', { class: 'field' }, el('label', { class: 'label' }, label), s);
        };
        const form = el('form', { class: 'stack-sm' },
          el('div', { class: 'grid-3' }, select('title', 'Title property', byType(['title']), false), select('date', 'Event date property', byType(['date'])), select('status', 'Status property', byType(['status', 'select'])),
            select('type', 'Type property', byType(['select', 'multi_select', 'status'])), select('project_relation', 'Project Domain relation', byType(['relation'])), select('class_relation', 'Class relation', byType(['relation']))),
          el('div', { class: 'field' }, el('label', { class: 'label' }, 'External ID properties (comma separated)'), el('input', { class: 'input', name: 'external_ids', value: Object.values(current.external_ids || {}).join(', '), placeholder: 'Canvas Assignment ID, External Event ID' })),
          el('div', { class: 'field' }, el('label', { class: 'label' }, 'Status values that mean done (comma separated)'), el('input', { class: 'input', name: 'done_values', value: (current.status_done_values || []).join(', '), placeholder: 'Done, Complete' })),
          el('div', { class: 'row' }, el('button', { class: 'btn btn-primary', type: 'submit' }, 'Save mapping version'), currentVersion ? el('span', { class: 'chip' }, 'version ', el('b', {}, currentVersion)) : pill('no mapping saved', 'warn')));
        form.addEventListener('submit', async (e) => {
          e.preventDefault(); const fd = new FormData(form);
          const body = { title: fd.get('title'), date: fd.get('date') || null, status: fd.get('status') || null, type: fd.get('type') || null, project_relation: fd.get('project_relation') || null, class_relation: fd.get('class_relation') || null,
            external_ids: Object.fromEntries(String(fd.get('external_ids') || '').split(',').map((s) => s.trim()).filter(Boolean).map((name) => [name, name])), status_done_values: String(fd.get('done_values') || '').split(',').map((s) => s.trim()).filter(Boolean) };
          try { await api('/notion/mapping', { method: 'POST', body }); toast('Mapping saved', 'ok'); refreshStatus(); drawCalendar(); } catch (err) { toast(errorDetail(err).message, 'danger'); }
        });
        return form;
      });
      load(previewBox, async () => {
        const r = await api('/notion/mapping/preview?n=5');
        const pages = r.pages || [];
        if (!pages.length) return el('p', { class: 'faint small' }, 'Save a mapping to preview how five real pages would be interpreted.');
        const table = el('table', { class: 'table' }, el('thead', {}, el('tr', {}, ...['Title', 'Start', 'End', 'All day', 'Status', 'Type', 'Relations', 'External IDs'].map((h) => el('th', {}, h)))));
        const body = el('tbody');
        for (const p of pages) { const i = p.interpreted || {}; body.appendChild(el('tr', {}, el('td', {}, i.title || p.title), el('td', { class: 'mono' }, i.date_start || ''), el('td', { class: 'mono' }, i.date_end || ''), el('td', {}, i.all_day ? 'yes' : 'no'), el('td', {}, i.status || ''), el('td', {}, i.type || ''), el('td', { class: 'mono small' }, `${(i.project_ids || []).length} project · ${(i.class_ids || []).length} class`), el('td', { class: 'mono small' }, Object.entries(i.external_ids || {}).map(([k, v]) => `${k}=${v}`).join(' ')))); }
        table.appendChild(body); return el('div', { class: 'stack-sm' }, el('p', { class: 'small muted' }, 'Read-only interpretation of real pages under the active mapping. Nothing is written.'), table);
      });
    }

    function drawRows() {
      rowsBox.innerHTML = '';
      if (!status?.capability?.connected) { rowsBox.appendChild(emptyState('Not connected', 'Live rows appear after read access is verified.')); return; }
      load(rowsBox, async () => {
        const r = await api('/notion/calendar/items?limit=50'); const items = r.results || [];
        if (!items.length) return emptyState('No pages returned', 'The data source may be empty or filtered.');
        const table = el('table', { class: 'table' }, el('thead', {}, el('tr', {}, el('th', {}, 'Title'), el('th', {}, 'Properties'), el('th', {}, 'Edited'))));
        const body = el('tbody');
        for (const p of items.slice(0, 50)) { const props = Object.entries(p.properties || {}).filter(([, v]) => v !== null && v !== '' && !(Array.isArray(v) && !v.length)); body.appendChild(el('tr', {}, el('td', {}, el('a', { href: p.url || '#', target: '_blank', rel: 'noopener' }, p.title || 'Untitled')), el('td', {}, el('div', { class: 'row' }, ...props.slice(0, 6).map(([k, v]) => el('span', { class: 'chip' }, `${k} `, el('b', {}, typeof v === 'object' ? JSON.stringify(v).slice(0, 40) : String(v).slice(0, 40)))))), el('td', { class: 'small faint' }, fmtTime(p.last_edited_time)))); }
        table.appendChild(body); return el('div', { class: 'stack-sm' }, el('p', { class: 'small faint' }, `${r.count ?? items.length} pages read through the data source query endpoint with pagination.`), table);
      });
    }
    refreshStatus(false);
  });

  // =====================================================================
  // FILES & AGENT
  // =====================================================================
  route('files', async ({ view, inspector, setTitle, setInspectorTitle }) => {
    setTitle('Files & Agent'); setInspectorTitle('Grant model'); $('#viewCrumb').textContent = 'Scoped tools with receipts';
    inspector.append(el('p', { class: 'small muted' }, 'PODA can only touch folders you grant. Read mode lists, reads, and searches. Edit mode also creates and edits files with hash-validated patches and backups. Temporary grants expire automatically. Execution is a separate flag.'),
      el('p', { class: 'small faint' }, 'Revoking a grant removes the capability from the ledger immediately. Every write produces a receipt; a failed write never produces a success receipt.'));
    const grantsBox = el('div'); const toolsBox = el('div'); const receiptsBox = el('div'); const proposalsBox = el('div');
    const form = el('form', { class: 'stack-sm' },
      el('div', { class: 'field' }, el('label', { class: 'label', for: 'g-path' }, 'Folder path'), el('input', { class: 'input', id: 'g-path', name: 'root_path', placeholder: '~/Desktop/PODA-sandbox', required: true }), el('div', { class: 'help' }, 'Must be an existing folder. The whole home directory and system roots are refused.')),
      el('div', { class: 'grid-3 grant-grid' },
        el('div', { class: 'field' }, el('label', { class: 'label', for: 'g-mode' }, 'Mode'), el('select', { class: 'input', id: 'g-mode', name: 'mode' }, el('option', { value: 'read' }, 'Read only'), el('option', { value: 'edit' }, 'Persistent edit'), el('option', { value: 'temp' }, 'One-task temporary edit'))),
        el('div', { class: 'field' }, el('label', { class: 'label', for: 'g-label' }, 'Label'), el('input', { class: 'input', id: 'g-label', name: 'label', placeholder: 'optional' })),
        el('div', { class: 'field' }, el('label', { class: 'label', for: 'g-ttl' }, 'Temp TTL (minutes)'), el('input', { class: 'input', id: 'g-ttl', name: 'ttl_minutes', type: 'number', min: 5, value: 60 }))),
      el('label', { class: 'check' }, el('input', { type: 'checkbox', name: 'allow_execute' }), 'Allow running Python inside this folder (edit or temp mode only)'),
      el('div', { class: 'row' }, el('button', { class: 'btn btn-primary', type: 'submit' }, 'Grant folder')));
    form.addEventListener('submit', async (e) => {
      e.preventDefault(); const fd = new FormData(form);
      try { await api('/agent/grants', { method: 'POST', body: { root_path: fd.get('root_path'), mode: fd.get('mode'), label: fd.get('label') || null, allow_execute: fd.get('allow_execute') === 'on', ttl_minutes: +fd.get('ttl_minutes') || null } }); form.reset(); toast('Folder granted. Ledger updated.', 'ok'); refresh(); }
      catch (err) { toast(errorDetail(err).message, 'danger'); }
    });
    view.appendChild(el('div', { class: 'screen stack' },
      el('div', { class: 'grid-main-side' }, section('Folder grants', 'the only source of file permissions', grantsBox), section('Grant a folder', null, form)),
      el('div', { class: 'grid-2' }, section('Tool registry', 'what the agent can call', toolsBox), section('Pending proposals', 'awaiting your approval', proposalsBox)),
      section('Action receipts', 'the only evidence of a completed side effect', receiptsBox),
      softwarePanel()));

    async function refresh() {
      await Promise.all([
        load(grantsBox, async () => {
          const r = await api('/agent/grants'); const grants = r.grants || [];
          if (!grants.length) return emptyState('No folder grants', 'PODA cannot read, create, or edit any user files until you grant a specific folder.');
          return el('div', { class: 'stack-sm' }, ...grants.map((g) => el('div', { class: 'tl-item' }, el('span', { class: 'when' }, fmtTime(g.created_at)),
            el('div', { class: 'what' }, el('div', { class: 'row' }, el('strong', {}, g.label || g.root_path.split('/').pop()), pill(g.mode, g.mode === 'read' ? 'accent' : 'warn'), g.allow_execute ? pill('exec', 'lav') : null, g.expires_at ? pill(`expires ${fmtTime(g.expires_at)}`, 'muted') : null), el('span', { class: 'mono' }, g.root_path)),
            el('button', { class: 'btn btn-sm btn-danger', onclick: async () => { if (!(await confirmDialog({ title: 'Revoke this grant?', body: `${g.root_path} becomes inaccessible to PODA immediately.`, confirmLabel: 'Revoke', danger: true }))) return; await api(`/agent/grants/${g.id}`, { method: 'DELETE' }); toast('Grant revoked', 'ok'); refresh(); } }, 'Revoke'))));
        }),
        load(toolsBox, async () => {
          const r = await api('/agent/tools'); const tools = r.tools || [];
          if (!tools.length) return emptyState('No tools registered', 'The tool registry is empty.');
          return el('div', {}, ...tools.map((t) => el('div', { class: 'tool-row' }, el('div', { class: 'stack-sm', style: { gap: '2px', minWidth: 0 } }, el('div', { class: 'row' }, el('strong', { class: 'mono small' }, t.name), t.requires_confirmation ? pill('confirm', 'warn') : null, t.requires_grant ? pill('grant', 'accent') : null), el('span', { class: 'small muted' }, t.description || '')), el('div', { class: 'tool-state' }, pill(t.available ? 'available' : 'unavailable', t.available ? 'ok' : 'danger'), t.reason ? el('span', { class: 'faint small' }, t.reason) : null))));
        }),
        load(proposalsBox, async () => {
          const r = await api('/agent/proposals?status=pending'); const items = r.proposals || [];
          if (!items.length) return emptyState('Nothing pending', 'Proposals appear here when PODA wants to perform a side effect in chat.');
          return el('div', { class: 'stack-sm' }, ...items.map((p) => { const plan = typeof p.plan === 'object' ? p.plan : (() => { try { return JSON.parse(p.plan_json || '{}'); } catch { return {}; } })(); return el('div', { class: 'proposal' }, el('div', { class: 'row-between' }, el('strong', {}, plan.rationale || 'Proposed actions'), el('span', { class: 'faint small' }, fmtTime(p.created_at))), el('ol', {}, ...((plan.steps || []).map((s) => el('li', {}, s.summary || s.tool)))),
            el('div', { class: 'row' }, el('button', { class: 'btn btn-primary btn-sm', onclick: async () => { try { await api(`/agent/proposals/${p.id}/approve`, { method: 'POST' }); toast('Approved and executed', 'ok'); refresh(); } catch (err) { toast(errorDetail(err).message, 'danger'); } } }, 'Approve'), el('button', { class: 'btn btn-ghost btn-sm', onclick: async () => { await api(`/agent/proposals/${p.id}/reject`, { method: 'POST' }); refresh(); } }, 'Reject'))); }));
        }),
        load(receiptsBox, async () => {
          const r = await api('/agent/receipts?limit=50'); const items = r.receipts || [];
          if (!items.length) return emptyState('No actions recorded', 'When PODA performs a real action, its receipt (tool, target, outcome, verification) appears here.');
          return el('div', { class: 'timeline' }, ...items.map((rc) => el('div', { class: 'tl-item' }, el('span', { class: 'when' }, fmtTime(rc.finished_at || rc.started_at)),
            el('div', { class: 'what' }, el('div', { class: 'row' }, el('strong', { class: 'mono small' }, rc.tool), pill(rc.outcome, rc.outcome === 'success' ? 'ok' : rc.outcome === 'running' ? 'accent' : 'danger'), rc.verification ? el('span', { class: 'chip' }, rc.verification) : null, rc.approved_by_user ? pill('approved', 'lav') : null), rc.target ? el('span', { class: 'mono' }, rc.target) : null, rc.detail ? el('span', { class: 'small muted' }, String(rc.detail).slice(0, 220)) : null, rc.error ? el('span', { class: 'small', style: { color: 'var(--danger)' } }, rc.error) : null),
            el('span', { class: 'mono faint small' }, `#${String(rc.id).slice(0, 8)}`))));
        }),
      ]);
    }
    refresh();
  });


  // ---------- Software skills (CLI-Anything) ----------
  function splitArgs(text) {
    const out = []; let cur = ''; let q = null;
    for (const ch of String(text || '')) {
      if (q) { if (ch === q) q = null; else cur += ch; }
      else if (ch === '"' || ch === "'") q = ch;
      else if (/\s/.test(ch)) { if (cur) { out.push(cur); cur = ''; } }
      else cur += ch;
    }
    if (cur) out.push(cur);
    return out;
  }
  function softwarePanel() {
    const catalogBox = el('div'); const detailBox = el('div'); const receiptsBox = el('div'); const sourcesBox = el('div');
    const wrap = el('div', { class: 'stack' },
      el('div', { class: 'grid-main-side' }, section('Software skills (CLI-Anything)', 'drive other programs through agent harnesses, with grants and receipts', catalogBox),
        el('div', { class: 'stack' }, section('Selected harness', null, detailBox), section('Catalog sources', 'local checkouts of the CLI-Anything repo', sourcesBox))),
      section('Software receipts', 'installs and runs', receiptsBox));
    let selected = null;
    const outcomePill = (o) => pill(o, o === 'succeeded' ? 'ok' : o === 'blocked' ? 'warn' : o === 'running' ? 'accent' : 'danger');
    async function runForm(name, h) {
      const input = el('input', { class: 'input mono', placeholder: '--help   or   info   or   project export --out out.svg' });
      const approve = el('input', { type: 'checkbox' });
      const out = el('div');
      const go = async () => {
        const args = splitArgs(input.value); out.innerHTML = ''; out.appendChild(skeleton(2));
        try {
          const r = await api(`/software/${encodeURIComponent(name)}/run`, { method: 'POST', body: { args, approve: approve.checked, timeout_s: 60 } });
          out.innerHTML = '';
          out.append(el('div', { class: 'row' }, outcomePill(r.receipt?.outcome), el('span', { class: 'small faint mono' }, r.verification || '')),
            r.result?.json ? jsonBox(r.result.json) : el('pre', { class: 'json' }, (r.result?.stdout || '') + (r.result?.stderr ? '\n' + r.result.stderr : '')));
        } catch (err) { out.innerHTML = ''; out.appendChild(errorNode(err)); }
        refreshReceipts();
      };
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); go(); } });
      return el('div', { class: 'stack-sm' }, el('div', { class: 'label' }, 'Run a subcommand'), input,
        el('div', { class: 'row' }, el('label', { class: 'check' }, approve, 'Approve if mutating'), el('button', { class: 'btn btn-sm btn-primary', onclick: go }, 'Run'),
          el('span', { class: 'small faint' }, h.allow_mutating ? 'mutating allowed for this harness' : 'read-only subcommands only until "allow mutating" is on')), out);
    }
    async function showDetail(name) {
      selected = name; detailBox.innerHTML = ''; detailBox.appendChild(skeleton(4));
      try {
        const h = await api(`/software/${encodeURIComponent(name)}`);
        detailBox.innerHTML = '';
        const toggles = el('div', { class: 'row' },
          el('label', { class: 'check' }, el('input', { type: 'checkbox', checked: !!h.enabled, onchange: (e) => setGrant(name, e.target.checked, h.allow_mutating) }), 'Enabled'),
          el('label', { class: 'check' }, el('input', { type: 'checkbox', checked: !!h.allow_mutating, onchange: (e) => setGrant(name, h.enabled || e.target.checked, e.target.checked) }), 'Allow mutating commands'));
        const actions = el('div', { class: 'row' });
        if (!h.installed) actions.append(el('button', { class: 'btn btn-sm btn-primary', onclick: () => installHarness(h) }, h.local_harness ? 'Install (local checkout)' : 'Install (downloads)'));
        else actions.append(el('button', { class: 'btn btn-sm btn-danger', onclick: () => uninstallHarness(h) }, 'Uninstall'));
        const skill = el('details', { class: 'skill-drawer' }, el('summary', {}, 'View skill (SKILL.md)'), el('pre', { class: 'json skill-md' }, h.skill_md || '(no SKILL.md available)'));
        detailBox.append(el('div', { class: 'row' }, el('strong', {}, h.display_name || h.name), pill(h.installed ? 'installed' : 'not installed', h.installed ? 'ok' : 'warn'), ...(h.enabled ? [pill('enabled', 'accent')] : []), el('span', { class: 'chip' }, h.category || 'other')),
          el('p', { class: 'small muted' }, h.description || ''), ...(h.requires ? [el('p', { class: 'small faint' }, 'Requires: ' + h.requires)] : []),
          el('div', { class: 'small faint mono' }, h.executable || h.entry_point), toggles, actions, skill, h.installed ? await runForm(name, h) : el('p', { class: 'small faint' }, 'Install the harness to run it.'));
      } catch (err) { detailBox.innerHTML = ''; detailBox.appendChild(errorNode(err)); }
    }
    async function setGrant(name, enabled, allowMutating) {
      try { await api(`/software/${encodeURIComponent(name)}/grant`, { method: 'POST', body: { enabled, allow_mutating: allowMutating } }); toast(`${name}: ${enabled ? 'enabled' : 'disabled'}${allowMutating ? ', mutating allowed' : ''}`, 'ok'); }
      catch (err) { toast(errorDetail(err).message, 'danger'); }
      refreshCatalog(); showDetail(name);
    }
    async function installHarness(h) {
      const remote = !h.local_harness;
      const ok = await confirmDialog({ title: `Install ${h.display_name || h.name}?`, body: remote ? 'No local checkout exists: this downloads from GitHub/PyPI into PODA\'s separate software venv (logged in the egress audit).' : `Installs ${h.local_harness} into PODA's separate software venv (data/software-venv). Dependencies may be fetched from PyPI if not cached.`, confirmLabel: 'Install' });
      if (!ok) return;
      detailBox.appendChild(el('p', { class: 'small muted' }, 'Installing… (bounded output will appear here)'));
      try {
        const r = await api(`/software/${encodeURIComponent(h.name)}/install`, { method: 'POST', body: { confirm: true, allow_network: remote } });
        toast(r.ok ? `Installed ${h.name}` : `Install failed for ${h.name}`, r.ok ? 'ok' : 'danger');
        detailBox.append(el('pre', { class: 'json' }, (r.output || '').slice(-4000)));
      } catch (err) { toast(errorDetail(err).message, 'danger'); detailBox.appendChild(errorNode(err)); return; }
      refreshCatalog(); showDetail(h.name); refreshReceipts();
    }
    async function uninstallHarness(h) {
      if (!(await confirmDialog({ title: `Uninstall ${h.name}?`, body: 'Removes it from PODA\'s software venv.', confirmLabel: 'Uninstall', danger: true }))) return;
      try { await api(`/software/${encodeURIComponent(h.name)}/uninstall`, { method: 'POST', body: { confirm: true } }); toast(`Uninstalled ${h.name}`, 'ok'); } catch (err) { toast(errorDetail(err).message, 'danger'); }
      refreshCatalog(); showDetail(h.name); refreshReceipts();
    }
    async function refreshCatalog() {
      await load(catalogBox, async () => {
        const r = await api('/software');
        const all = Object.values(r.groups || {}).flat();
        const head = el('div', { class: 'row' }, pill(`${r.count} in catalog`, 'accent'), pill(`${r.installed.length} installed`, r.installed.length ? 'ok' : 'warn'), pill(`${r.usable.length} usable`, r.usable.length ? 'ok' : 'warn'),
          el('span', { class: 'small faint' }, r.capability?.detail || ''));
        if (!all.length) return el('div', { class: 'stack-sm' }, head, emptyState('No harnesses found', 'Point the catalog at a CLI-Anything checkout below.'));
        const filter = el('input', { class: 'input', placeholder: 'Filter by name, category, description…' });
        const list = el('div', { class: 'sw-list' });
        const render = () => {
          const q = filter.value.trim().toLowerCase();
          list.innerHTML = '';
          all.filter((h) => !q || [h.name, h.display_name, h.category, h.description].join(' ').toLowerCase().includes(q)).slice(0, 120).forEach((h) => {
            list.appendChild(el('button', { class: 'sw-row' + (selected === h.name ? ' active' : ''), onclick: () => { showDetail(h.name); render(); } },
              el('div', { class: 'row' }, el('strong', {}, h.display_name || h.name), el('span', { class: 'chip' }, h.category || 'other'),
                pill(h.installed ? 'installed' : 'not installed', h.installed ? 'ok' : 'faint'), ...(h.enabled ? [pill(h.allow_mutating ? 'enabled · mutating' : 'enabled · read-only', 'accent')] : [])),
              el('div', { class: 'small muted' }, h.description || ''), ...(h.requires ? [el('div', { class: 'small faint' }, 'Requires: ' + h.requires)] : [])));
          });
          if (!list.children.length) list.appendChild(emptyState('No match', 'Try another filter.'));
        };
        filter.addEventListener('input', render); render();
        return el('div', { class: 'stack-sm' }, head, filter, list);
      });
    }
    async function refreshReceipts() {
      await load(receiptsBox, async () => {
        const r = await api('/software/receipts?limit=30'); const items = r.receipts || [];
        if (!items.length) return emptyState('No software actions yet', 'Installs and harness runs appear here with their verification.');
        return el('div', { class: 'timeline' }, ...items.map((rc) => el('div', { class: 'tl-item' }, el('span', { class: 'when' }, fmtTime(rc.finished_at || rc.started_at)),
          el('div', { class: 'what' }, el('div', { class: 'row' }, el('strong', { class: 'mono small' }, rc.tool), outcomePill(rc.outcome), el('span', { class: 'small muted' }, rc.target || '')), el('div', { class: 'small faint' }, rc.verification || rc.error || '')))));
      });
    }
    async function refreshSources() {
      await load(sourcesBox, async () => {
        const r = await api('/software');
        const ta = el('textarea', { class: 'input mono', rows: 3 }, (r.sources || []).join('\n'));
        return el('div', { class: 'stack-sm' }, el('div', { class: 'small faint' }, `Software venv: ${r.venv} ${r.venv_exists ? '(exists)' : '(created on first install)'}`), ta,
          el('button', { class: 'btn btn-sm', onclick: async () => { try { await api('/software/sources', { method: 'POST', body: { paths: ta.value.split('\n').map((s) => s.trim()).filter(Boolean) } }); toast('Sources updated', 'ok'); refreshCatalog(); } catch (err) { toast(errorDetail(err).message, 'danger'); } } }, 'Save sources'));
      });
    }
    refreshCatalog(); refreshReceipts(); refreshSources();
    detailBox.appendChild(emptyState('Select a harness', 'Pick a program from the catalog to install, enable, read its skill, or run a command.'));
    return wrap;
  }

  // =====================================================================
  // PRIVACY
  // =====================================================================
  route('privacy', async ({ view, inspector, setTitle, setInspectorTitle }) => {
    setTitle('Privacy'); setInspectorTitle('Threat model'); $('#viewCrumb').textContent = 'What is protected, and what is not';
    const encBox = el('div'); const connBox = el('div'); const egressBox = el('div'); const backupBox = el('div'); const retentionBox = el('div');
    view.appendChild(el('div', { class: 'screen stack' },
      el('div', { class: 'grid-2' }, section('Encryption at rest', null, encBox), section('Connections and file roots', 'one button revokes each', connBox)),
      section('Outbound network audit', 'only metadata is logged, never bodies or tokens', egressBox),
      el('div', { class: 'grid-2' }, section('Backups and export', null, backupBox), section('Retention', null, retentionBox))));

    async function refresh() {
      let s;
      try { s = await api('/privacy/status'); } catch (err) { encBox.innerHTML = ''; encBox.appendChild(errorNode(err)); return; }
      inspector.innerHTML = '';
      inspector.append(el('div', { class: 'threat' }, el('h3', {}, 'Protected'), el('ul', { class: 'ok' }, ...(s.threat_model?.protected || []).map((t) => el('li', {}, t))), el('h3', {}, 'Not protected'), el('ul', { class: 'no' }, ...(s.threat_model?.not_protected || []).map((t) => el('li', {}, t)))),
        el('p', { class: 'small faint' }, 'Privacy from remote services and casual file access can be engineered. Invisibility to every program on the same Mac cannot be promised.'));
      const e = s.encryption || {};
      encBox.innerHTML = '';
      encBox.append(el('div', { class: 'row' }, pill(e.database_encrypted_on_disk ? 'SQLCipher encrypted on disk' : 'NOT encrypted on disk', e.database_encrypted_on_disk ? 'ok' : 'danger'), e.key_source ? pill(`key: ${e.key_source}`, 'muted') : null),
        kv([['SQLCipher module', yesNo(e.sqlcipher_module)], ['Key error', e.key_error], ['Policy', e.policy], ['File mode', e.file_mode], ['Data directory', s.data_dir], ['Directory mode', s.data_dir_mode], ['Messages', s.counts?.messages], ['Memory nodes', s.counts?.memory_nodes], ['Embeddings', s.counts?.memory_embeddings], ['Receipts', s.counts?.action_receipts]]));
      const notion = s.connections?.notion || {};
      connBox.innerHTML = '';
      connBox.append(el('div', { class: 'row-between' }, el('div', { class: 'row' }, el('strong', {}, 'Notion'), statusPill(notion.status), notion.database_title ? el('span', { class: 'small muted' }, notion.database_title) : null), el('button', { class: 'btn btn-sm btn-danger', disabled: !notion.token_storage, onclick: async () => { if (!(await confirmDialog({ title: 'Disconnect Notion and revoke its token?', confirmLabel: 'Revoke', danger: true }))) return; await api('/notion/disconnect', { method: 'POST' }); toast('Notion revoked', 'ok'); refresh(); } }, 'Revoke')),
        el('div', { class: 'small faint' }, `Scopes: read ${notion.can_read_content ? 'yes' : 'no'} · insert ${notion.can_insert_content ? 'yes' : 'no'} · update ${notion.can_update_content ? 'yes' : 'no'} · only api.notion.com is allowlisted`),
        el('div', { class: 'divider' }), (() => { const box = el('div', { class: 'stack-sm' }, el('strong', {}, 'Mail accounts')); api('/mail/status').then((ms) => { const accounts = ms.accounts || []; if (!accounts.length) { box.appendChild(el('p', { class: 'faint small' }, 'None connected. PODA cannot read any mail.')); return; } for (const a of accounts) box.appendChild(el('div', { class: 'row-between' }, el('div', { class: 'row' }, el('span', { class: 'small' }, a.account_name), pill(a.provider === 'apple_mail' ? 'Apple Mail' : 'Gmail IMAP', 'muted'), pill('read', 'ok'), a.allow_modify ? pill('edits', 'warn') : null, a.allow_send ? pill('send', 'danger') : null), el('button', { class: 'btn btn-sm btn-danger', onclick: async () => { if (!(await confirmDialog({ title: `Revoke mail access for ${a.account_name}?`, confirmLabel: 'Revoke', danger: true }))) return; await api(`/mail/accounts/${encodeURIComponent(a.id)}`, { method: 'DELETE' }); toast('Mail account revoked', 'ok'); refresh(); } }, 'Revoke'))); }).catch((err) => box.appendChild(el('p', { class: 'faint small' }, `Mail connector unavailable: ${errorDetail(err).message}`))); return box; })(),
        el('div', { class: 'divider' }), el('strong', {}, 'Authorized file roots'),
        (s.file_grants || []).length ? el('div', { class: 'stack-sm' }, ...s.file_grants.map((g) => el('div', { class: 'row-between' }, el('span', { class: 'mono small' }, g.root_path), el('div', { class: 'row' }, pill(g.mode, 'accent'), el('button', { class: 'btn btn-sm btn-ghost', onclick: async () => { await api(`/agent/grants/${g.id}`, { method: 'DELETE' }); refresh(); } }, 'Revoke'))))) : el('p', { class: 'faint small' }, 'None. PODA cannot touch user files.'),
        el('div', { class: 'divider' }), el('div', { class: 'row-between' }, el('div', {}, el('strong', {}, 'Offline mode'), el('div', { class: 'faint small' }, 'Blocks every external connector. Chat and memory keep working locally.')), (() => { const sw = el('button', { class: 'switch', role: 'switch', 'aria-checked': s.egress?.offline_mode ? 'true' : 'false', 'aria-label': 'Offline mode' }); sw.onclick = async () => { const next = sw.getAttribute('aria-checked') !== 'true'; await api('/privacy/offline', { method: 'POST', body: { enabled: next } }); sw.setAttribute('aria-checked', String(next)); toast(`Offline mode ${next ? 'on' : 'off'}`, 'ok'); }; return sw; })()));
      await load(egressBox, async () => {
        const r = await api('/privacy/egress?limit=60');
        const hosts = r.summary?.hosts || [];
        const out = el('div', { class: 'stack-sm' }, el('div', { class: 'row' }, ...Object.entries(r.summary?.allowlist || {}).map(([svc, hs]) => el('span', { class: 'chip' }, `${svc} `, el('b', {}, hs.join(', '))))));
        if (!hosts.length) out.appendChild(emptyState('No outbound requests recorded', 'PODA has not contacted any external service.'));
        else {
          const table = el('table', { class: 'table' }, el('thead', {}, el('tr', {}, el('th', {}, 'Host'), el('th', {}, 'Connector'), el('th', {}, 'Requests'), el('th', {}, 'Blocked'), el('th', {}, 'Last'))));
          table.appendChild(el('tbody', {}, ...hosts.map((h) => el('tr', {}, el('td', { class: 'mono' }, h.host), el('td', {}, h.service), el('td', { class: 'num' }, h.requests), el('td', { class: 'num' }, h.blocked || 0), el('td', { class: 'small faint' }, fmtTime(h.last_ts))))));
          out.appendChild(table);
          const recent = r.recent || [];
          if (recent.length) out.appendChild(el('details', { class: 'thought-panel' }, el('summary', {}, `Recent requests (${recent.length})`), el('pre', {}, recent.slice(0, 60).map((x) => `${x.ts}  ${x.method} ${x.host}${x.path}  ${x.status ?? 'blocked'}  ${x.duration_ms ?? ''}ms`).join('\n'))));
        }
        return out;
      });
      backupBox.innerHTML = '';
      const backups = s.backups || [];
      backupBox.append(el('div', { class: 'row' },
        el('button', { class: 'btn btn-primary', onclick: async () => { try { const r = await api('/privacy/backup', { method: 'POST' }); toast(`Encrypted backup written (${fmtBytes(r.bytes)})`, 'ok'); refresh(); } catch (err) { toast(errorDetail(err).message, 'danger'); } } }, 'Create encrypted backup'),
        el('button', { class: 'btn', onclick: async () => { if (!(await confirmDialog({ title: 'Export a plaintext copy?', body: 'The export is NOT encrypted. It is written with 0600 permissions into the backups folder for your own inspection. Delete it when done.', confirmLabel: 'Export plaintext', danger: true }))) return; try { const r = await api('/privacy/export', { method: 'POST' }); toast(`Plaintext export: ${r.path}`, 'ok', 7000); } catch (err) { toast(errorDetail(err).message, 'danger'); } } }, 'Export plaintext copy')),
        el('p', { class: 'small faint' }, 'Backups are AES-256-GCM files keyed by a separate Keychain entry (PODA.BackupKey), so they can be restored onto a fresh install.'),
        backups.length ? el('div', { class: 'stack-sm' }, ...backups.map((b) => el('div', { class: 'row-between', style: { padding: '6px 0', borderBottom: '1px solid var(--line-0)' } }, el('div', {}, el('div', { class: 'mono small' }, b.filename), el('div', { class: 'faint small' }, `${fmtBytes(b.bytes)} · v${b.version || ''} · ${b.created_at || ''}`)),
          el('button', { class: 'btn btn-sm btn-danger', onclick: async () => { if (!(await confirmDialog({ title: 'Restore this backup?', body: 'The live database is replaced. The current database is saved first as a safety copy. Type RESTORE to continue.', confirmLabel: 'Restore', danger: true, requireText: 'RESTORE' }))) return; try { const r = await api('/privacy/restore', { method: 'POST', body: { filename: b.filename, confirm: true } }); toast(`Restored ${r.memory_nodes} memory nodes. Reload PODA.`, 'ok', 8000); } catch (err) { toast(errorDetail(err).message, 'danger', 8000); } } }, 'Restore')))) : el('p', { class: 'faint small' }, 'No encrypted backups yet.'));
      retentionBox.innerHTML = '';
      const before = el('input', { class: 'input', type: 'date', 'aria-label': 'Delete messages before' });
      retentionBox.append(el('div', { class: 'field' }, el('label', { class: 'label' }, 'Delete chat messages and their linked memories created before'), before, el('div', { class: 'help' }, 'Removes the source messages and the visible memory nodes that mirror them. Durable memories you committed by hand are kept.')),
        el('div', { class: 'row' },
          el('button', { class: 'btn btn-danger', onclick: async () => { if (!before.value) return; if (!(await confirmDialog({ title: `Delete messages before ${before.value}?`, body: 'This cannot be undone except from a backup.', confirmLabel: 'Delete', danger: true }))) return; try { const r = await api('/privacy/retention', { method: 'POST', body: { delete_messages_before: before.value } }); toast(`Removed ${r.removed?.messages_and_linked_memories ?? 0} messages`, 'ok'); refresh(); } catch (err) { toast(errorDetail(err).message, 'danger'); } } }, 'Delete old messages'),
          el('button', { class: 'btn', onclick: async () => { try { await api('/privacy/retention', { method: 'POST', body: { delete_egress_log: true } }); toast('Egress log cleared', 'ok'); refresh(); } catch (err) { toast(errorDetail(err).message, 'danger'); } } }, 'Clear network log')));
    }
    refresh();
  });

  // =====================================================================
  // SYSTEM
  // =====================================================================
  route('system', async ({ view, inspector, setTitle, setInspectorTitle }) => {
    setTitle('System'); setInspectorTitle('Build'); $('#viewCrumb').textContent = 'Hardware, models, ledger';
    const hwBox = el('div'); const modelsBox = el('div'); const benchBox = el('div'); const ledgerBox = el('div');
    view.appendChild(el('div', { class: 'screen stack' }, section('This Mac', 'probed at request time', hwBox), section('Model profiles', 'selected only from installed models', modelsBox), section('Benchmarks', 'measured on this hardware', benchBox), section('Capability ledger', 'live and persisted', ledgerBox)));
    try { const b = await api('/system/build'); const st = await api('/system/startup').catch(() => null); inspector.append(kv([['Version', b.version], ['Build ID', el('span', { class: 'mono' }, b.build_id)], ['Memory viewer', b.memory_viewer], ['Database', el('span', { class: 'mono small' }, b.db_path)], ['Port', b.port]]), st ? el('details', { class: 'thought-panel' }, el('summary', {}, 'Startup report'), el('pre', {}, JSON.stringify(st, null, 2))) : null); } catch (err) { inspector.appendChild(errorNode(err)); }
    load(hwBox, async () => {
      const h = await api('/system/hardware');
      return el('div', { class: 'stack-sm' }, el('div', { class: 'stats' }, ...[[h.chip || 'unknown', 'chip'], [`${h.unified_memory_gb} GB`, 'unified memory'], [`${h.memory_available_estimate_gb} GB`, 'available estimate'], [`${h.disk_free_gb} GB`, 'disk free'], [h.metal || 'n/a', 'metal'], [h.ollama?.version || (h.ollama?.reachable ? 'reachable' : 'unreachable'), 'ollama']].map(([v, k]) => el('div', { class: 'stat' }, el('span', { class: 'v' }, v), el('span', { class: 'k' }, k)))),
        (h.ollama_resident || []).length ? el('div', { class: 'row' }, el('span', { class: 'faint small' }, 'Resident now:'), ...h.ollama_resident.map((m) => el('span', { class: 'chip' }, `${m.name} `, el('b', {}, `${fmtBytes(m.size_vram || m.size)} · ctx ${m.context_length || '?'}`)))) : el('p', { class: 'faint small' }, 'No model is resident in memory right now.'));
    });
    async function drawModels() {
      await load(modelsBox, async () => {
        const m = await api('/system/models');
        const profiles = el('table', { class: 'table' }, el('thead', {}, el('tr', {}, el('th', {}, 'Profile'), el('th', {}, 'Model'), el('th', {}, 'Context'), el('th', {}, 'Max output'), el('th', {}, 'Thinking'), el('th', {}, ''))));
        profiles.appendChild(el('tbody', {}, ...Object.entries(m.profiles || {}).map(([name, p]) => el('tr', {}, el('td', {}, el('strong', {}, name)), el('td', { class: 'mono' }, p.model || el('span', { class: 'faint' }, 'none installed')), el('td', { class: 'num' }, `${(p.num_ctx / 1024).toFixed(0)}K`), el('td', { class: 'num' }, p.num_predict), el('td', {}, p.think ? 'on' : 'off'),
          el('td', {}, p.model ? el('button', { class: 'btn btn-sm', onclick: () => bench(p.model, p.num_ctx) }, 'Benchmark') : null)))));
        const q = el('table', { class: 'table' }, el('thead', {}, el('tr', {}, el('th', {}, 'Qwen3.6 tag'), el('th', {}, 'Disk'), el('th', {}, 'Kind'), el('th', {}, 'Installed'), el('th', {}, 'Verdict on this Mac'))));
        q.appendChild(el('tbody', {}, ...(m.qwen36 || []).map((c) => el('tr', {}, el('td', { class: 'mono' }, c.tag), el('td', { class: 'num' }, `${c.disk_gb} GB`), el('td', {}, c.kind), el('td', {}, yesNo(c.installed)), el('td', {}, pill(c.verdict, c.memory_safe_32k ? 'ok' : c.memory_safe_8k ? 'warn' : 'danger'))))));
        return el('div', { class: 'stack' }, el('div', { class: 'row' }, el('span', { class: 'chip' }, 'installed ', el('b', {}, (m.installed || []).join(', ') || 'none')), el('span', { class: 'chip' }, 'embedding ', el('b', {}, `${m.embedding} ${m.embedding_installed ? '' : '(missing)'}`))), profiles,
          el('div', { class: 'stack-sm' }, el('h3', {}, 'Qwen3.6 assessment'), el('p', { class: 'small muted' }, `Unified memory ${m.unified_memory_gb} GB. Verdicts reserve headroom for macOS, PODA, and KV cache. PODA never pulls models silently.`), q));
      });
    }
    async function drawBench() {
      await load(benchBox, async () => {
        const m = await api('/system/models'); const rows = m.benchmarks || [];
        if (!rows.length) return emptyState('No benchmarks recorded', 'Run one from the model profiles above. Each run measures load, first token, tokens per second, and resident memory.');
        const table = el('table', { class: 'table' }, el('thead', {}, el('tr', {}, ...['Model', 'Context', 'Load', 'First token', 'Gen tok/s', 'Prompt tok/s', 'Memory', 'When'].map((h) => el('th', {}, h)))));
        table.appendChild(el('tbody', {}, ...rows.slice(0, 30).map((b) => el('tr', {}, el('td', { class: 'mono' }, b.model), el('td', { class: 'num' }, `${(b.num_ctx / 1024).toFixed(0)}K`), el('td', { class: 'num' }, `${b.load_s}s`), el('td', { class: 'num' }, `${b.first_token_s}s`), el('td', { class: 'num' }, b.gen_tok_s ?? ''), el('td', { class: 'num' }, b.prompt_tok_s ?? ''), el('td', { class: 'num' }, fmtBytes(b.memory_bytes)), el('td', { class: 'small faint' }, fmtTime(b.created_at))))));
        return table;
      });
    }
    async function bench(model, num_ctx) {
      const t = toast(`Benchmarking ${model} at ${num_ctx / 1024}K context…`, '', 60000);
      try { const r = await api('/system/benchmark', { method: 'POST', body: { model, num_ctx } }); t.remove(); toast(`${model}: ${r.gen_tok_s} tok/s, first token ${r.first_token_s}s, ${fmtBytes(r.memory_bytes)}`, 'ok', 8000); drawBench(); }
      catch (err) { t.remove(); toast(errorDetail(err).message, 'danger'); }
    }
    drawModels(); drawBench();
    load(ledgerBox, async () => { const l = await api('/capabilities/ledger'); return el('div', { class: 'stack-sm' }, el('div', { class: 'row' }, pill(`Ollama ${l.current?.ollama?.reachable ? 'reachable' : 'unreachable'}`, l.current?.ollama?.reachable ? 'ok' : 'danger'), statusPill(l.current?.notion?.status), pill(`${(l.current?.filesystem?.granted_roots || []).length} folder grants`, 'accent'), pill(l.current?.memory?.encrypted_at_rest ? 'encrypted at rest' : 'not encrypted', l.current?.memory?.encrypted_at_rest ? 'ok' : 'danger')), jsonBox(l.current)); });
  });

  // ---------- Boot ----------
  loadBuild().then(() => renderRoute());
})();
