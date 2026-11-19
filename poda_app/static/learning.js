/* Render validated data as local, keyboard-operable practice activities. */
(function () {
  'use strict';
  const {el, api} = window.PODA;

  function quiz(widget, next) {
    const feedback = el('p', {role:'status', class:'small'});
    const followUp = el('button', {class:'btn btn-sm btn-ghost', type:'button', hidden:true}, 'Try a follow-up');
    let picked;
    const choices = widget.choices.map((choice, index) =>
      el('button', {class:'btn btn-sm btn-ghost', type:'button', 'aria-pressed':'false', onclick:() => {
        picked = index;
        choices.forEach((button, i) => button.setAttribute('aria-pressed', String(index === i)));
        feedback.textContent = (index === widget.correct ? 'Correct. ' : 'Try again. ') + choice.feedback;
        followUp.hidden = false;
      }}, choice.label));
    followUp.onclick = () => next('For “' + widget.question + '” I chose “' + widget.choices[picked].label +
      '”. Give me a related practice question and help me check the reasoning.');
    const reveal = el('button', {class:'btn btn-sm', type:'button', onclick:() => {
      const answer = widget.choices[widget.correct];
      feedback.textContent = answer.label + '. ' + answer.feedback;
    }}, 'Reveal answer');
    return el('section', {class:'learning-card', 'aria-label':'Practice question'},
      el('strong', {}, widget.question), el('div', {class:'learning-choices'}, ...choices), feedback, reveal, followUp);
  }

  function graph(widget) {
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 380 160');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', 'Normalized step response over six seconds');
    const axes = document.createElementNS(ns, 'path');
    axes.setAttribute('d', 'M35 15 V130 H365');
    axes.setAttribute('fill', 'none');
    axes.setAttribute('stroke', 'currentColor');
    const line = document.createElementNS(ns, 'polyline');
    line.setAttribute('fill', 'none');
    line.setAttribute('stroke', '#48baff');
    line.setAttribute('stroke-width', '2');
    svg.append(axes, line);
    const slider = el('input', {type:'range', min:0.15,max:4,step:0.05,value:widget.rate, 'aria-label':'Pole magnitude'});
    const label = el('output', {class:'small', 'aria-live':'polite'});
    function plot() {
      const rate = Number(slider.value);
      line.setAttribute('points', Array.from({length:121}, (_, i) => {
        const t = i / 20;
        return (35 + 55 * t) + ',' + (130 - 105 * (1 - Math.exp(-rate * t)));
      }).join(' '));
      label.textContent = 'Pole = −' + rate.toFixed(2) + ' s⁻¹ · Time constant = ' + (1/rate).toFixed(2) + ' s';
    }
    slider.addEventListener('input', plot);
    plot();
    return el('section', {class:'learning-card', 'aria-label':'Interactive step response'},
      el('strong', {}, widget.title), svg, el('p', {class:'small'}, 'Horizontal axis: time (0–6 s). Vertical axis: normalized response (0–1).'),
      el('label', {}, 'Pole magnitude ', slider), label);
  }

  async function checklist(session, parent, suggestion = []) {
    if (!session) return;
    const url = '/chat/session/' + encodeURIComponent(session) + '/checklist';
    const card = el('section', {class:'learning-card', 'aria-label':'Homework progress'});
    let state, notice;
    try { state = await api(url); } catch { return; }
    if (!state.items.length && !suggestion.length) return;
    parent.append(card);
    let busy = false;
    async function save(change) {
      if (busy) return;
      busy = true;
      try {
        state = {...state, ...await api(url, {method:'PUT', body:{revision:state.revision,...change}})};
        notice = '';
      } catch (error) {
        notice = error.message;
        state = {...state, ...await api(url).catch(() => ({}))};
      } finally { busy = false; draw(); }
    }
    function draw() {
      const editor = el('textarea', {rows:4, 'aria-label':'One assignment requirement per line'});
      editor.value = (state.items.length ? state.items.map(item => item.label) : suggestion).join('\n');
      const edit = el('details', {open:!state.items.length}, el('summary', {}, 'Edit requirements'), editor,
        el('button', {class:'btn btn-sm', type:'button', onclick:() => save({items:editor.value.split('\n').map(x=>x.trim()).filter(Boolean)})}, 'Save requirements'));
      card.replaceChildren(el('strong', {}, (state.task_title || 'Homework') + ' · ' + state.progress + '%'),
        ...state.items.map(item => {
          const box = el('input', {type:'checkbox'});
          box.checked = item.done;
          box.onchange = () => save({item_id:item.id,completed:box.checked});
          return el('label', {class:'learning-requirement'}, box, ' ', item.label);
        }), edit, el('p', {class:'small', role:'status'}, notice || (state.calendar_error ? 'Calendar sync: ' + state.calendar_error : '')));
    }
    draw();
  }

  function mount(widgets, parent, next, checklistParent, session) {
    for (const widget of widgets || []) {
      if (widget.kind === 'quiz') parent.append(quiz(widget, next));
      if (widget.kind === 'graph') parent.append(graph(widget));
      if (widget.kind === 'checklist' && checklistParent) {
        checklistParent.replaceChildren();
        checklist(session, checklistParent, widget.items);
      }
    }
  }
  window.PODALearning = {mount, checklist};
})();
