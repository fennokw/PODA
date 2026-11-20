/* Composer completion for single-word commands and explicit plugin mentions. */
(function () {
  'use strict';
  const {el, api} = window.PODA;
  const modes = [
    ['teach', 'Guide me step by step with questions, diagrams, and feedback'],
    ['build', 'Work on code and verify the result'],
    ['research', 'Investigate a question using sources'],
  ];

  function mount(input, getSessionId) {
    const id = 'chat-slash-options';
    const options = el('div', {id, role:'listbox', 'aria-label':'Chat commands and plugins'});
    const status = el('p', {class:'chat-slash-status', role:'status'});
    const panel = el('div', {class:'chat-slash-panel', hidden:true}, options, status);
    const field = el('div', {class:'chat-slash-anchor'}, input, panel);
    const trigger = el('button', {class:'btn btn-ghost btn-sm chat-slash-trigger', type:'button',
      'aria-label':'Browse chat slash commands', 'aria-controls':id, 'aria-expanded':'false', title:'Slash commands'}, '/');
    const pluginTrigger = el('button', {class:'btn btn-ghost btn-sm chat-slash-trigger', type:'button',
      'aria-label':'Browse chat plugins', 'aria-controls':id, 'aria-expanded':'false', title:'Mention a plugin'}, '@');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-controls', id);
    let request, suggestions = [], focused = -1, dismissed = false;

    function close() {
      panel.hidden = true;
      trigger.setAttribute('aria-expanded','false');
      pluginTrigger.setAttribute('aria-expanded','false');
      input.removeAttribute('aria-activedescendant');
      suggestions = [];
      focused = -1;
    }

    function reset() {
      request = null;
      dismissed = false;
      close();
    }

    function query() {
      const before = input.value.slice(0, input.selectionStart);
      const after = input.value.slice(input.selectionEnd);
      if (after && !/^\s/.test(after)) return null;
      const prefix = before.match(/^(\s*(?:\/(?:teach|build|research)(?:off)?\s+)?)([@/])([a-z0-9_-]*)$/i);
      if (prefix) return {kept:prefix[1], kind:prefix[2], query:prefix[3].toLowerCase(), after};
      const mention = before.match(/^(.*\s)@([a-z0-9_-]*)$/is);
      return mention ? {kept:mention[1], kind:'@', query:mention[2].toLowerCase(), after} : null;
    }

    function choose(row) {
      const current = query();
      if (!current) return;
      const replacement = current.kept + row.value + ' ';
      input.value = replacement + current.after.trimStart();
      input.setSelectionRange(replacement.length, replacement.length);
      dismissed = true;
      close();
      input.focus();
    }

    function rows(data, current) {
      const slash = [
        ...modes.flatMap(([mode, detail]) => [
          {value:'/' + mode, label:'/' + mode, detail},
          {value:'/' + mode + 'off', label:'/' + mode + 'off', detail:'Turn off ' + mode + ' mode'},
        ]),
        ...(data.task_name ? [{value:'/taskdone', label:'/taskdone', detail:'Complete ' + data.task_name}] : []),
        ...(data.goal_target ? [{value:'/taskprogress', label:'/taskprogress', detail:'Enter the completed count'}] : []),
      ];
      const candidates = current.kind === '/' ? slash : (data.plugins || []).map(item =>
        ({value:'@' + item.name, label:'@' + item.name, detail:item.label}));
      return candidates.filter(row => row.value.toLowerCase().startsWith(current.kind + current.query));
    }

    function draw(data, current, error = '') {
      suggestions = rows(data, current);
      focused = -1;
      input.removeAttribute('aria-activedescendant');
      options.replaceChildren(...suggestions.map((row, index) =>
        el('div', {role:'option', id:id+'-'+index, class:'chat-slash-option', 'aria-selected':'false',
          onmousedown:event=>event.preventDefault(), onclick:()=>choose(row)},
          el('strong', {}, row.label), el('span', {}, row.detail))));
      status.hidden = !error && !!suggestions.length;
      status.textContent = error || (current.kind === '/' ? 'No matching commands.' : 'No matching plugins.');
    }

    async function refresh() {
      const current = query();
      if (dismissed || !current || document.activeElement !== input) { close(); return; }
      panel.hidden = false;
      trigger.setAttribute('aria-expanded', String(current.kind === '/'));
      pluginTrigger.setAttribute('aria-expanded', String(current.kind === '@'));
      const session = getSessionId();
      if (!request || request.kind !== current.kind || request.session !== session) {
        const pending = {kind:current.kind, session, data:null, error:''};
        request = pending;
        draw({}, current, current.kind === '@' ? 'Loading plugins…' : '');
        try {
          pending.data = await api(current.kind === '@' ? '/chat/plugins' :
            '/project-manager/chat-commands' + (session ? '?session_id=' + encodeURIComponent(session) : ''));
        } catch {
          pending.error = current.kind === '@' ? 'Could not load plugins.' : '';
        }
        if (request !== pending) return;
      }
      const latest = query();
      if (!dismissed && !panel.hidden && latest && document.activeElement === input) draw(request.data || {}, latest, request.error);
    }

    input.addEventListener('input', () => { dismissed = false; if (request?.error) request = null; refresh(); });
    input.addEventListener('focus', () => { if (!dismissed) refresh(); });
    input.addEventListener('blur', close);
    input.addEventListener('click', () => { if (!dismissed) refresh(); });
    for (const [button, symbol] of [[trigger,'/'],[pluginTrigger,'@']]) {
      button.onmousedown = event => event.preventDefault();
      button.onclick = () => {
        if (!panel.hidden && query()?.kind === symbol) { dismissed = true; close(); return; }
        dismissed = false;
        input.focus();
        if (query()?.kind !== symbol) {
          const value = input.value;
          const mode = symbol === '@' ? value.match(/^\s*\/(?:teach|build|research)(?:off)?\s+/i)?.[0] || '' : '';
          input.value = mode + symbol + (value.slice(mode.length) ? ' ' + value.slice(mode.length) : '');
          input.setSelectionRange(mode.length + 1, mode.length + 1);
        }
        refresh();
      };
    }

    function handleKey(event) {
      if (panel.hidden || event.isComposing || event.altKey || event.ctrlKey || event.metaKey) return false;
      if (event.key === 'Escape') {
        dismissed = true;
        close();
      } else if (suggestions.length && ['ArrowDown','ArrowUp'].includes(event.key)) {
        focused = event.key === 'ArrowDown' ? (focused + 1) % suggestions.length
          : (focused <= 0 ? suggestions.length - 1 : focused - 1);
        [...options.children].forEach((node,index) => node.setAttribute('aria-selected',String(index === focused)));
        const active = options.children[focused];
        input.setAttribute('aria-activedescendant',active.id);
        active.scrollIntoView?.({block:'nearest'});
      } else if (suggestions.length && !event.shiftKey && ['Tab','Enter'].includes(event.key)) {
        const current = query();
        if (!current) return false;
        if (event.key === 'Enter' && focused < 0 && suggestions.some(row =>
          row.value !== '/taskprogress' && row.value === current.kind + current.query && !current.after.trim())) return false;
        choose(suggestions[Math.max(0, focused)]);
      } else return false;
      event.preventDefault();
      return true;
    }
    return {field, trigger, pluginTrigger, handleKey, reset};
  }
  window.PODAChatCommands = {mount};
})();
