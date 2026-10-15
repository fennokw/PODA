/* Workload & time personalization - separate from chat, backed by encrypted local storage. */
(function () {
  'use strict';
  const { route, el, api, toast, confirmDialog, promptDialog, errorDetail, pill } = window.PODA;
  const CATEGORIES = ['general', 'homework', 'study', 'writing', 'coding', 'research', 'admin', 'creative', 'reading'];
  const fmt = (minutes) => !Number.isFinite(Number(minutes)) ? '-' :
    (minutes < 60 ? minutes + 'm' : Math.floor(minutes / 60) + 'h ' + (minutes % 60 ? minutes % 60 + 'm' : '')).trim();
  const field = (label, node) => el('label', { class: 'field' }, el('span', { class: 'label' }, label), node);
  const input = (name, type = 'text', value = '', extra = {}) => el('input', { class: 'input', name, type, value: value ?? '', ...extra });
  const paragraph = (text) => el('p', { class: 'small muted' }, text);

  route('workload', async ({ view, inspector, setTitle, setInspectorTitle }) => {
    setTitle('Workload'); setInspectorTitle('Personal pacing');
    document.getElementById('viewCrumb').textContent = 'Tasks · effort · priorities · realistic time';
    const box = el('div', { class: 'screen stack workload-screen' });
    view.appendChild(box);
    inspector.append(
      paragraph('Estimates adapt to your own predicted and actual durations, weighted by work category and difficulty. For a specific item your entered time takes precedence.'),
      paragraph('The proposed work blocks check live Notion events when connected. No Notion events are ever created or moved without a separate approved action.'),
      paragraph('Completed time reports improve later estimates. At first, estimates are broad hypotheses, not measurements.'));
    const title = el('h2', {}, 'Workload & personal pace');
    const intro = paragraph('Make your work visible. Teach PODA your speed. Turn priorities and deadlines into draft sessions that fit the time you actually have.');
    box.append(title, intro);

    let editing = null;
    const form = el('form', { class: 'card stack' });
    const name = input('title', 'text', '', { required: true, placeholder: 'e.g. Physics PSET 6', maxlength: 200 });
    const project = input('project', 'text', '', { placeholder: 'Course / project / workstream' });
    const category = el('select', { class: 'input', name: 'category' },
      ...CATEGORIES.map((c) => el('option', { value: c }, c)));
    const difficulty = input('difficulty', 'number', 5, { min: 1, max: 10, required: true });
    const importance = input('importance', 'number', 5, { min: 1, max: 10, required: true });
    const deadline = input('deadline', 'date');
    const userMinutes = input('user_minutes', 'number', '', { min: 1, placeholder: 'Leave blank for PODA estimate' });
    const baseline = input('baseline_minutes', 'number', '', { min: 1, placeholder: 'Optional expert/base estimate' });
    const progress = input('progress', 'number', 0, { min: 0, max: 100 });
    const description = el('textarea', { class: 'input', name: 'description', rows: 4, placeholder: 'Paste instructions, assignment details, or explain what needs to be done.' });
    const status = el('select', { class: 'input', name: 'status' },
      ...['todo', 'active', 'paused', 'done'].map((n) => el('option', { value: n }, n)));
    const submit = el('button', { class: 'btn btn-primary', type: 'submit' }, 'Add work item');
    const reset = el('button', { class: 'btn btn-ghost', type: 'button' }, 'Clear');
    const suggestion = el('div', { class: 'small faint' }, 'PODA starts with a conservative estimate until you teach it your pace.');
    form.append(el('div', { class: 'card-head' }, el('h3', {}, 'Add or edit work'), el('span', { class: 'meta' }, 'Locally stored')),
      field('Task or deliverable', name), field('Project / course', project), field('Description or instructions', description),
      el('div', { class: 'grid-3' }, field('Category', category), field('Difficulty (1–10)', difficulty), field('Priority (1–10)', importance)),
      el('div', { class: 'grid-3' }, field('Deadline', deadline), field('Your time estimate (minutes)', userMinutes),
        field('Reference baseline (minutes)', baseline)),
      el('div', { class: 'grid-2' }, field('Progress %', progress), field('Status', status)),
      suggestion, el('div', { class: 'row' }, submit, reset));
    box.appendChild(form);

    const itemsBox = el('div', { class: 'stack-sm' });
    const intakeBox = el('div', { class: 'stack-sm' });
    const scheduleBox = el('div', { class: 'stack-sm' });
    const prefsBox = el('div', { class: 'stack-sm' });
    const upload = el('input', { class: 'input', type: 'file', accept: '.pdf,.docx,.txt,.md,.csv' });
    const uploadBtn = el('button', { class: 'btn', type: 'button' }, 'Analyze work document');
    box.append(
      el('section', { class: 'card stack' }, el('div', { class: 'card-head' }, el('h3', {}, 'Homework & to-do sheet intake'),
        el('span', { class: 'meta' }, 'PDF · DOCX · TXT · MD · CSV')),
        paragraph('Upload an assignment, checklist, or project brief. PODA extracts candidate tasks locally for your review; nothing is automatically saved.'),
        el('div', { class: 'row' }, upload, uploadBtn), intakeBox),
      el('section', { class: 'card stack' }, el('div', { class: 'card-head' }, el('h3', {}, 'Current workload'),
        el('span', { class: 'meta' }, 'Personal estimates, progress & deadlines')), itemsBox),
      el('section', { class: 'card stack' }, el('div', { class: 'card-head' }, el('h3', {}, 'Draft a realistic schedule'),
        el('span', { class: 'meta' }, 'Priority + deadlines + free time')),
        paragraph('Drafts reserve no time in Notion. Events are consulted read-only. Set your working window below, then generate a proposal.'),
        el('div', { class: 'row' },
          el('button', { class: 'btn btn-primary', type: 'button', onclick: () => drawPlan(14) }, 'Plan next 14 days'),
          el('button', { class: 'btn', type: 'button', onclick: () => drawPlan(7) }, 'Plan next 7 days')), scheduleBox),
      el('section', { class: 'card stack' }, el('div', { class: 'card-head' }, el('h3', {}, 'Your work availability'),
        el('span', { class: 'meta' }, 'Explicit personalized settings')), prefsBox));

    const controls = [name, project, category, difficulty, importance, deadline,
      userMinutes, baseline, progress, description, status];
    function clearForm() {
      form.reset(); editing = null; submit.textContent = 'Add work item';
      status.value = 'todo'; category.value = 'general'; difficulty.value = '5';
      importance.value = '5'; progress.value = '0';
      suggestion.textContent = 'Enter your predicted duration to teach PODA how quickly you expect this kind of work to go.';
    }
    reset.onclick = clearForm;

    let itemCache = [];
    const renderItems = async () => {
      itemsBox.textContent = 'Loading work items…';
      try {
        const data = await api('/workload');
        itemCache = data.items || [];
        itemsBox.innerHTML = '';
        if (!itemCache.length) {
          itemsBox.appendChild(paragraph('No work items yet. Add one manually or upload an assignment above.'));
          return;
        }
        const byProject = new Map();
        for (const item of itemCache) {
          const label = item.project?.trim() || 'Ungrouped work';
          const group = byProject.get(label) || { name: label, total: 0, completed: 0, remaining: 0, soonest: null };
          group.total++;
          if (item.status === 'done') group.completed++;
          group.remaining += Number(item.estimate?.remaining_minutes || 0);
          if (item.deadline && (!group.soonest || item.deadline < group.soonest)) group.soonest = item.deadline;
          byProject.set(label, group);
        }
        const overview = el('div', { class: 'work-project-overview' });
        for (const summary of [...byProject.values()].sort((a, b) => b.remaining - a.remaining)) {
          overview.appendChild(el('div', { class: 'work-project-card' },
            el('strong', {}, summary.name),
            el('span', { class: 'small muted' }, summary.completed + '/' + summary.total + ' tasks finished'),
            el('span', { class: 'mono' }, fmt(summary.remaining) + ' remaining'),
            summary.soonest ? el('span', { class: 'small faint' }, 'Next due: ' + summary.soonest) : null));
        }
        itemsBox.appendChild(overview);
        const sorted = [...itemCache].sort((a, b) => String(a.deadline || '9999').localeCompare(String(b.deadline || '9999'))
          || b.importance - a.importance);
        for (const item of sorted) {
          const est = item.estimate || {};
          const edit = el('button', { class: 'btn btn-sm', type: 'button' }, 'Edit');
          edit.onclick = () => {
            editing = item.id;
            for (const control of controls) control.value = item[control.name] ?? '';
            submit.textContent = 'Save changes';
            suggestion.textContent = 'Changes to your time estimate or actual duration recalibrate future predictions.';
            name.focus(); form.scrollIntoView({ behavior: 'smooth', block: 'start' });
          };
          const record = el('button', { class: 'btn btn-sm', type: 'button' }, 'Record time');
          record.onclick = async () => {
            const value = await promptDialog({ title: 'Record actual effort', body: 'Actual minutes spent in total; this will calibrate future estimates for similar work.', label: 'Actual minutes', value: String(item.actual_minutes || '') });
            if (value === null) return;
            const n = Number(value);
            if (!Number.isInteger(n) || n < 1) { toast('Enter a positive integer number of minutes', 'danger'); return; }
            try { await api('/workload/' + encodeURIComponent(item.id), { method: 'PUT', body: { ...item, actual_minutes: n } }); toast('Pace calibration updated', 'ok'); await renderItems(); }
            catch (err) { toast(errorDetail(err).message, 'danger'); }
          };
          const steps = el('button', { class: 'btn btn-sm', type: 'button' }, 'Break down');
          steps.onclick = async () => {
            try {
              const data = await api('/workload/' + encodeURIComponent(item.id) + '/analysis');
              const existing = row.querySelector('.work-breakdown');
              if (existing) existing.remove();
              const details = el('div', { class: 'work-breakdown stack-sm' },
                el('div', { class: 'small muted' }, data.mode === 'local_model' ? 'Locally analyzed by ' + data.model : 'Generic breakdown (model unavailable)'));
              for (const step of data.steps || []) {
                details.appendChild(el('div', { class: 'work-phase' },
                  el('div', { class: 'row-between' }, el('strong', {}, step.title),
                    el('span', { class: 'chip' }, fmt(step.minutes))),
                  step.details ? el('p', { class: 'small muted' }, step.details) : null));
              }
              row.appendChild(details);
            } catch (err) { toast(errorDetail(err).message, 'danger'); }
          };
          const remove = el('button', { class: 'btn btn-sm btn-danger', type: 'button' }, 'Delete');
          remove.onclick = async () => {
            if (!(await confirmDialog({ title: 'Delete this work item?', body: 'This deletes the local workload item and linked calibration examples. It does not change Notion.', confirmLabel: 'Delete', danger: true }))) return;
            await api('/workload/' + encodeURIComponent(item.id), { method: 'DELETE' }); await renderItems();
          };
          const row = el('div', { class: 'work-item' },
            el('div', { class: 'row-between' }, el('strong', {}, item.title),
              el('div', { class: 'row' }, pill(item.status, item.status === 'done' ? 'ok' : 'accent'),
                pill('P' + item.importance, item.importance >= 8 ? 'warn' : 'muted'))),
            el('div', { class: 'small muted' }, [item.project, item.category, 'difficulty ' + item.difficulty,
              item.deadline ? 'due ' + item.deadline : 'no deadline'].filter(Boolean).join(' · ')),
            el('div', { class: 'row' },
              el('span', { class: 'chip' }, 'Remaining ', el('b', {}, fmt(est.remaining_minutes))),
              el('span', { class: 'chip' }, 'Total ', el('b', {}, fmt(est.minutes))),
              el('span', { class: 'chip' }, 'Range ', el('b', {}, (est.range_minutes || []).map(fmt).join('–'))),
              pill(est.calibration?.confidence || 'low', est.calibration?.confidence === 'high' ? 'ok' : 'muted')),
            el('div', { class: 'small faint' }, est.source || 'heuristic', ' · ', est.calibration?.examples || 0, ' feedback examples'),
            el('div', { class: 'row' }, edit, record, steps, remove));
          itemsBox.appendChild(row);
        }
      } catch (err) { itemsBox.innerHTML = ''; itemsBox.appendChild(el('p', { class: 'notice notice-danger' }, errorDetail(err).message)); }
    };

    form.onsubmit = async (e) => {
      e.preventDefault();
      const values = Object.fromEntries(new FormData(form).entries());
      for (const key of ['importance', 'difficulty', 'progress', 'user_minutes', 'baseline_minutes']) {
        values[key] = values[key] === '' ? null : Number(values[key]);
      }
      if (!values.importance) values.importance = 5;
      if (!values.difficulty) values.difficulty = 5;
      if (!Number.isInteger(values.progress)) values.progress = 0;
      values.deadline = values.deadline || null;
      values.user_minutes = values.user_minutes || null;
      values.baseline_minutes = values.baseline_minutes || null;
      try {
        const path = editing ? '/workload/' + encodeURIComponent(editing) : '/workload';
        await api(path, { method: editing ? 'PUT' : 'POST', body: values });
        toast(editing ? 'Work item saved' : 'Task added', 'ok'); clearForm(); await renderItems();
      } catch (err) { toast(errorDetail(err).message, 'danger'); }
    };

    uploadBtn.onclick = async () => {
      const file = upload.files?.[0];
      if (!file) { toast('Choose a document first', 'danger'); return; }
      const data = new FormData(); data.append('file', file);
      uploadBtn.disabled = true; intakeBox.textContent = 'Extracting local document text…';
      try {
        const res = await fetch('/workload/intake', { method: 'POST', body: data, credentials: 'same-origin', cache: 'no-store' });
        const parsed = await res.json();
        if (!res.ok) throw new Error(parsed.detail || 'Document extraction failed');
        intakeBox.innerHTML = '';
        intakeBox.append(el('div', { class: 'small muted' }, parsed.filename + ' · ' + parsed.characters + ' characters · ' + parsed.suggestions.length + ' suggestions'),
          el('details', {}, el('summary', {}, 'Document preview'), el('pre', { class: 'json' }, parsed.preview)));
        for (const candidate of parsed.suggestions) {
          const btn = el('button', { class: 'btn btn-sm btn-primary', type: 'button' }, 'Add to workload');
          btn.onclick = async () => {
            btn.disabled = true;
            try {
              await api('/workload', { method: 'POST', body: { ...candidate, project: file.name } });
              btn.textContent = 'Added'; toast('Added to workload', 'ok'); await renderItems();
            } catch (err) { btn.disabled = false; toast(errorDetail(err).message, 'danger'); }
          };
          intakeBox.appendChild(el('div', { class: 'work-intake-row' }, el('span', { class: 'small' }, candidate.title), btn));
        }
      } catch (err) { intakeBox.innerHTML = ''; intakeBox.appendChild(el('p', { class: 'notice notice-danger' }, errorDetail(err).message)); }
      finally { uploadBtn.disabled = false; }
    };

    async function drawPlan(days) {
      scheduleBox.textContent = 'Checking local workload and available calendar time…';
      try {
        const result = await api('/workload/schedule?days=' + days + '&include_calendar=true');
        scheduleBox.innerHTML = '';
        scheduleBox.append(el('div', { class: 'notice' }, result.calendar),
          paragraph('Draft: ' + result.sessions.length + ' work blocks, ' + fmt(result.total_minutes) + ' planned. Nothing was written to Notion.'));
        if (result.unscheduled.length) {
          scheduleBox.append(el('div', { class: 'notice notice-warn' },
            'Not enough time for: ' + result.unscheduled.map((x) => x.title + ' (' + fmt(x.remaining_minutes) + ')').join('; ')));
        }
        const list = el('div', { class: 'stack-sm' });
        for (const block of result.sessions.slice(0, 90)) {
          const start = new Date(block.start);
          const end = new Date(block.end);
          list.appendChild(el('div', { class: 'work-session' },
            el('span', { class: 'mono small' }, start.toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })),
            el('strong', {}, block.title),
            el('span', { class: 'muted small' }, fmt(block.minutes) + ' · until ' + end.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }))));
        }
        scheduleBox.appendChild(list);
        if (!result.sessions.length) scheduleBox.appendChild(paragraph('No available sessions in this window. Check deadlines, available hours, and existing events.'));
      } catch (err) { scheduleBox.textContent = errorDetail(err).message; }
    }

    async function loadPrefs() {
      try {
        const prefs = await api('/workload/settings/preferences');
        prefsBox.innerHTML = '';
        const settingsForm = el('form', { class: 'stack-sm' });
        const settings = [
          ['start_hour', 'Start working (24h hour)', 0, 23],
          ['end_hour', 'Stop working (24h hour)', 1, 24],
          ['session_minutes', 'Preferred focus block (minutes)', 15, 180],
          ['break_minutes', 'Break after each block (minutes)', 0, 60],
          ['max_daily_minutes', 'Max planned focus time per day (minutes)', 30, 900]];
        for (const [key, label, min, max] of settings) {
          settingsForm.appendChild(field(label, input(key, 'number', prefs[key], { min, max })));
        }
        settingsForm.appendChild(field('Include weekends', el('input', { name: 'include_weekends', type: 'checkbox', checked: prefs.include_weekends })));
        const save = el('button', { class: 'btn btn-primary', type: 'submit' }, 'Save work preferences');
        settingsForm.appendChild(save);
        settingsForm.onsubmit = async (e) => {
          e.preventDefault();
          const changed = Object.fromEntries(new FormData(settingsForm).entries());
          for (const [key] of settings) changed[key] = Number(changed[key]);
          changed.include_weekends = Boolean(changed.include_weekends);
          try { await api('/workload/settings/preferences', { method: 'PUT', body: changed }); toast('Work preferences saved', 'ok'); }
          catch (err) { toast(errorDetail(err).message, 'danger'); }
        };
        prefsBox.appendChild(settingsForm);
      } catch (err) { prefsBox.textContent = errorDetail(err).message; }
    }

    await Promise.all([renderItems(), loadPrefs()]);
  });
})();
