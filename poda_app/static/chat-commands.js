/* Suggestions for commands handled by the chat composer. */
(function () {
  'use strict';
  const {el, api} = window.PODA;

  function mount(input, getSessionId) {
    const listId = 'chat-slash-options';
    const options = el('div', {id:listId, role:'listbox', 'aria-label':'Slash commands'});
    const status = el('p', {class:'chat-slash-status', role:'status'});
    const panel = el('div', {class:'chat-slash-panel', hidden:true}, options, status);
    const field = el('div', {class:'chat-slash-anchor'}, input, panel);
    const trigger = el('button', {class:'btn btn-ghost btn-sm chat-slash-trigger', type:'button',
      'aria-label':'Browse chat slash commands', 'aria-controls':listId, 'aria-expanded':'false', title:'Slash commands'}, '/');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-controls', listId);
    let request = null;
    let suggestions = [];
    let focused = -1;
    let dismissed = false;

    function close() {
      panel.hidden = true;
      trigger.setAttribute('aria-expanded','false');
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
      const kept = before.match(/^(?:\s*\/(?:work|teach[ \t]+me|learn|build|research|act)[ \t]+)+(?=\/)/i)?.[0] || '';
      const match = before.slice(kept.length).match(/^\s*\/([^\s/]*)(?:[ \t]+([^\n]*))?$/u);
      if (!match) return null;
      const command = match[1].toLowerCase();
      const argument = match[2];
      if (argument !== undefined && !['project','task','teach'].includes(command)) return null;
      if (argument && /^"(?:[^"\\]|\\.)*"/u.test(argument)) return null;
      return {command, argument, after, kept};
    }

    function choose(row) {
      const current = query();
      if (!current) return;
      const tail = current.after.trimStart();
      const replacement = current.kept + row.value + (row.space || tail ? ' ' : '');
      input.value = replacement + (row.expand && tail ? ' ' : '') + tail;
      input.setSelectionRange(replacement.length, replacement.length);
      input.focus();
      input.dispatchEvent(new Event('input', {bubbles:true}));
      if (!row.expand) {
        dismissed = true;
        close();
      }
    }

    function rows(data, current) {
      const projects = data.projects || [];
      const tasks = data.tasks || [];
      if (current.argument !== undefined && current.command !== 'teach') {
        const search = current.argument.replace(/^"/,'').toLowerCase();
        const targets = current.command === 'project' ? projects : tasks;
        return targets.filter(target => target.name.toLowerCase().includes(search)
          || target.shortcut?.toLowerCase().includes(search))
          .map(target => ({value:target.value, label:target.name, description:target.value, space:true}));
      }
      const staticRows = [
        {value:'/project', label:'/project', description:'Choose a project for this chat', expand:true, space:true},
        ...(tasks.length ? [{value:'/task', label:'/task', description:'Choose a task in this project', expand:true, space:true}] : []),
        ...(data.task_name && !current.after.trim() ? [
          {value:'/taskdone', label:'/taskdone', description:'Complete '+data.task_name},
          ...(data.goal_target ? [{value:'/taskprogress', label:'/taskprogress', description:'Enter the completed count out of '+data.goal_target, space:true}] : []),
        ] : []),
        ...[
          ['/work', 'Start or save a Work Discussion'],
          ['/learn', 'Explore a topic with interactive practice'],
          ['/teach me', 'Work through your material step by step'],
          ['/build', 'Work on code and verify the result'],
          ['/research', 'Investigate a question'],
          ['/act', 'Use connected tools to do a task'],
        ].map(([value, description]) => ({value, label:value, description, space:true})),
      ];
      const aliases = [
        ...projects.map(target => ({...target, scope:'Project'})),
        ...tasks.map(target => ({...target, scope:'Task'})),
      ].filter(target => target.shortcut).map(target =>
        ({value:target.shortcut, label:target.shortcut, description:target.scope+' · '+target.name, space:true}));
      const prefix = '/' + current.command + (current.command === 'teach' && current.argument !== undefined ? ' ' + current.argument : '');
      return [...staticRows, ...aliases].filter(row => row.value.toLowerCase().startsWith(prefix));
    }

    function draw(data, current, error = '') {
      suggestions = rows(data, current);
      focused = -1;
      input.removeAttribute('aria-activedescendant');
      options.replaceChildren(...suggestions.map((row, index) =>
        el('div', {role:'option', id:listId+'-'+index, class:'chat-slash-option', 'aria-selected':'false',
          onmousedown:event=>event.preventDefault(), onclick:()=>choose(row)},
          el('strong', {}, row.label), el('span', {}, row.description))));
      status.hidden = !error && suggestions.length > 0;
      const emptyText = current.argument === undefined ? 'No matching slash commands.'
        : current.command === 'task' && !data.tasks?.length ? 'File this chat under a project with tasks first.'
        : 'No matching ' + current.command + ' names.';
      status.textContent = error || emptyText;
    }

    async function refresh() {
      const current = query();
      if (dismissed || !current || document.activeElement !== input) { close(); return; }
      panel.hidden = false;
      trigger.setAttribute('aria-expanded','true');
      const session = getSessionId();
      if (!request || request.session !== session) {
        const suffix = session ? '?session_id=' + encodeURIComponent(session) : '';
        const pending = {session, data:null, error:''};
        request = pending;
        // Render the project chooser while the local catalog loads.
        draw({}, current, 'Loading commands…');
        try {
          pending.data = await api('/project-manager/chat-commands' + suffix);
        } catch {
          pending.error = 'Could not load slash commands. Type / again to retry.';
        }
        if (request !== pending) return;
      }
      const latest = query();
      if (dismissed || panel.hidden || !latest || document.activeElement !== input) return;
      if (request.data || request.error) draw(request.data || {}, latest, request.error);
    }

    input.addEventListener('input', () => {
      dismissed = false;
      if (request?.error) request = null;
      refresh();
    });
    input.addEventListener('focus', () => { if (!dismissed) refresh(); });
    input.addEventListener('blur', close);
    input.addEventListener('click', () => { if (!dismissed) refresh(); });
    trigger.onclick = () => {
      if (!panel.hidden) { dismissed = true; close(); return; }
      dismissed = false;
      input.focus();
      if (!input.value.trimStart().startsWith('/')) {
        input.value = input.value ? '/ ' + input.value : '/';
        input.setSelectionRange(1, 1);
      }
      refresh();
    };

    function handleKey(event) {
      if (panel.hidden || event.isComposing || event.altKey || event.ctrlKey || event.metaKey) return false;
      if (event.key === 'Escape') {
        dismissed = true;
        close();
      } else if (suggestions.length && ['ArrowDown','ArrowUp'].includes(event.key)) {
        const last = suggestions.length - 1;
        focused = event.key === 'ArrowDown' ? (focused + 1) % suggestions.length
          : (focused <= 0 ? last : focused-1);
        [...options.children].forEach((node, index) => node.setAttribute('aria-selected',String(index === focused)));
        const active = options.children[focused];
        input.setAttribute('aria-activedescendant',active.id);
        active.scrollIntoView?.({block:'nearest'});
      } else if (suggestions.length && !event.shiftKey && ['Tab','Enter'].includes(event.key)) {
        const current = query();
        if (!current) return false;
        if (event.key === 'Enter' && focused < 0 && suggestions.some(row => current.kept.trimStart() + row.value === input.value.trim() && !row.expand && row.value !== '/taskprogress')) {
          return false;
        }
        choose(suggestions[Math.max(0, focused)]);
      } else {
        return false;
      }
      event.preventDefault();
      return true;
    }
    return {field, trigger, handleKey, reset};
  }
  window.PODAChatCommands = {mount};
})();
