/* The 3D memory browser and typed knowledge share the same source notes. */
(function () {
  'use strict';
  const { el, api, errorDetail, toast } = window.PODA;
  const note = text => el('p', { class: 'small muted' }, text);
  const button = (text, handler) => el('button', { type: 'button', class: 'btn btn-sm', onclick: handler }, text);

  function explorer(focus) {
    const question = el('input', { class: 'input', type: 'search', placeholder: 'Ask about a name or alias',
      'aria-label': 'Find knowledge relationships' });
    const results = el('div', { class: 'stack-sm', 'aria-live': 'polite' });
    const search = el('button', { type: 'submit', class: 'btn btn-sm' }, 'Follow relationships');
    const form = el('form', { class: 'stack-sm' }, question, search);
    form.onsubmit = async event => {
      event.preventDefault();
      if (!question.value.trim()) return;
      search.disabled = true;
      try {
        const { facts, graph_ms } = await api('/memory/knowledge?q=' + encodeURIComponent(question.value.trim()));
        results.replaceChildren(...facts.map(fact => el('div', { class: 'recall-item' },
          el('strong', {}, `${fact.subject_name} → ${fact.predicate.replaceAll('_', ' ')} → ${fact.object_name}`),
          fact.qualifier ? note(fact.qualifier) : null, note(fact.evidence),
          button(`Source · ${fact.source_title} · hop ${fact.hop}`, () => focus(fact.memory_id)))));
        results.prepend(note(facts.length ? `${facts.length} relationships · ${graph_ms} ms`
          : 'No committed relationships match that name or alias.'));
      } catch (error) { results.replaceChildren(note(errorDetail(error).message)); }
      finally { search.disabled = false; }
    };
    const queue = el('div', { class: 'stack-sm', 'aria-live': 'polite' });
    const review = button('Find memories to review', async () => {
      review.disabled = true;
      try {
        const { sources } = await api('/memory/knowledge/review-queue');
        queue.replaceChildren(...sources.map(source => el('div', { class: 'recall-item' },
          button(source.title || 'Untitled memory', () => focus(source.id)), note(source.preview))));
        if (!sources.length) queue.append(note('No active source memories are waiting for facts.'));
      } catch (error) { queue.replaceChildren(note(errorDetail(error).message)); }
      finally { review.disabled = false; }
    });
    return el('details', { class: 'thought-panel' }, el('summary', {}, 'Knowledge relationships'),
      note('Walk up to three links from a matching entity. Select a memory point to review its relationships.'),
      form, results, review, queue);
  }

  function editor(nodeId, hasSourceDraft) {
    const box = el('details', { class: 'thought-panel', 'data-knowledge-editor': '1' }, el('summary', {}, 'Knowledge facts'));
    const body = el('div', { class: 'stack-sm' });
    box.append(body);
    const url = '/memory/knowledge/source/' + encodeURIComponent(nodeId);
    let opened = false;
    box.ontoggle = async () => {
      if (!box.open || opened) return;
      opened = true;
      body.replaceChildren(note('Loading the committed memory…'));
      try { render(await api(url)); }
      catch (error) {
        opened = false;
        body.replaceChildren(note(errorDetail(error).message));
      }
    };

    function render(data) {
      body.replaceChildren();
      body.append(el('strong', {}, data.title || 'Source memory'));
      if (!data.source.trim()) {
        body.append(note('Add user-authored text to this memory before creating facts.'));
        return;
      }
      const cards = el('div', { class: 'stack-sm' });
      const source = el('details', { class: 'thought-panel' }, el('summary', {}, 'Committed source text'), note(data.source));
      const select = (label, choices, value) => {
        const control = el('select', { class: 'input', 'aria-label': label },
          ...choices.map(item => el('option', { value: item }, item.replaceAll('_', ' '))));
        control.value = value;
        return control;
      };
      const input = (label, value, length) => el('input', {
        class: 'input', 'aria-label': label, placeholder: label, value, maxlength: length,
      });
      const formRows = new Map();
      function add(fact = {}) {
        if (formRows.size >= 20) return;
        const entity = (label, value) => {
          const kind = select(label + ' type', data.kinds, value?.kind || 'person');
          const name = input(label + ' name', value?.name || '', 120);
          const aliases = input(label + ' aliases, comma separated', (value?.aliases || []).join(', '), 1000);
          return { controls: [kind, name, aliases], read: () => ({ kind: kind.value, name: name.value,
            aliases: aliases.value.split(',').map(item => item.trim()).filter(Boolean) }) };
        };
        const subject = entity('Subject', fact.subject);
        const object = entity('Object', fact.object);
        const predicate = select('Relationship', data.predicates, fact.predicate || 'references');
        const qualifier = input('Condition, date, or exception', fact.qualifier || '', 180);
        const evidence = el('textarea', { class: 'input', rows: 2, maxlength: 500,
          placeholder: 'Paste the exact evidence from the committed memory', 'aria-label': 'Source quote' });
        evidence.value = fact.evidence || '';
        const card = el('div', { class: 'recall-item stack-sm' }, ...subject.controls, predicate, ...object.controls,
          qualifier, evidence, button('Remove from draft', () => { formRows.delete(card); card.remove(); }));
        formRows.set(card, () => ({ subject: subject.read(), predicate: predicate.value, object: object.read(),
          qualifier: qualifier.value, evidence: evidence.value }));
        cards.append(card);
      }
      data.facts.forEach(add);
      const status = el('div', { class: 'small muted', role: 'status' });
      const draft = button('Draft facts with Ollama', async () => {
        if (hasSourceDraft()) { status.textContent = 'Commit memory changes first.'; return; }
        draft.disabled = save.disabled = true;
        status.textContent = 'Reading the source with your local model…';
        try {
          const result = await api(url + '/draft', { method: 'POST' });
          if (result.revision !== data.revision) { status.textContent = 'The source changed. Reopen the memory to refresh.'; return; }
          const seen = new Set([...formRows.values()].map(read => JSON.stringify(read())));
          const next = result.facts.filter(fact => !seen.has(JSON.stringify(fact))).slice(0, 20 - formRows.size);
          next.forEach(add);
          status.textContent = next.length ? `Review ${next.length} new draft facts before committing.` : 'No new supported facts found.';
        } catch (error) { status.textContent = errorDetail(error).message; }
        finally { draft.disabled = save.disabled = false; }
      });
      const save = button('Commit knowledge facts', async () => {
        if (hasSourceDraft()) { status.textContent = 'Commit memory changes first.'; return; }
        draft.disabled = save.disabled = true;
        try {
          render(await api(url, { method: 'PUT', body: { revision: data.revision, facts: [...formRows.values()].map(read => read()) } }));
          toast('Knowledge facts committed', 'ok');
        } catch (error) { status.textContent = errorDetail(error).message; }
        finally { draft.disabled = save.disabled = false; }
      });
      body.append(note('Facts use exact evidence from this memory. Review the direction, aliases and conditions before committing. Editing the source text clears its previous facts.'),
        data.active ? null : note('This source is inactive. Its facts are excluded from recall.'),
        source, cards, el('div', { class: 'row' }, button('Add fact', () => add()), draft, save), status);
    }
    return box;
  }

  window.PODAKnowledge = { explorer, editor };
})();
