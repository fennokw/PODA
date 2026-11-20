/* Render validated data as local, keyboard-operable practice activities. */
(function () {
  'use strict';
  const {el, api} = window.PODA;

  function richText(text, tag = 'div') {
    const node = el(tag, {class:'learning-text'});
    const renderer = window.PODAChatRendering;
    if (renderer) renderer.renderInto(node, text);
    else node.textContent = text;
    return node;
  }

  function questionCard(widget, next, messageId, index, answered) {
    const feedback = el('p', {role:'status', class:'small', hidden:!answered}, answered ? 'Answer submitted.' : '');
    const choices = widget.choices || [];
    const input = el('input', {class:'input', type:'text', 'aria-label':'Your answer', placeholder:'Type your answer', maxlength:2000, required:true});
    const send = el('button', {class:'btn btn-primary btn-sm', type:'submit'}, 'Answer');
    const form = el('form', {class:'learning-answer-form'}, input, send);
    const reveal = Number.isInteger(widget.correct) && choices[widget.correct] ? el('button',
      {class:'btn btn-ghost btn-sm', type:'button', onclick:() => {
        feedback.hidden = false;
        feedback.textContent = choices[widget.correct].label + '. ' + choices[widget.correct].feedback;
      }}, 'Reveal answer') : null;
    const selected = answered?.practice?.choice;
    const buttons = choices.map((choice, i) => el('button', {
      class:'btn btn-sm', type:'button', 'aria-pressed':String(i === selected), onclick:() => submit(choice.label, i)
    }, el('span', {class:'learning-option-letter'}, String.fromCharCode(65+i)), richText(choice.label, 'span')));
    function submit(value, choice = null) {
      const answer = value.trim();
      if (!answer || !next(answer, messageId ? {message_id:messageId, widget:index, choice} : null)) return;
      buttons.forEach((button, i) => button.setAttribute('aria-pressed', String(i === choice)));
      feedback.hidden = false;
      feedback.textContent = 'Answer submitted.';
    }
    form.onsubmit = event => { event.preventDefault(); submit(input.value); };
    return el('section', {class:'learning-card', 'aria-label':'Practice question'},
      richText(widget.question, 'strong'),
      buttons.length ? el('div', {class:'learning-choices'}, ...buttons) : form,
      feedback, reveal);
  }

  function sources(excerpts, parent) {
    if (!excerpts?.length) return;
    const group = el('div', {class:'chat-source-excerpts', 'aria-label':'Source excerpts'});
    for (const excerpt of excerpts) {
      const trace = [excerpt.file, excerpt.page ? 'page ' + excerpt.page : '', excerpt.ocr ? 'OCR' : ''].filter(Boolean).join(' · ');
      const card = el('figure', {class:'chat-source-card'}, el('figcaption', {}, trace));
      if (excerpt.text) card.append(el('blockquote', {}, excerpt.text));
      if (excerpt.image && /^[A-Za-z0-9+/]+=*$/.test(excerpt.image)) {
        card.append(el('img', {src:'data:image/png;base64,' + excerpt.image, alt:'Excerpt from ' + excerpt.file}));
      }
      group.append(card);
    }
    parent.append(group);
  }

  function sampledPlot(widget) {
    const points = widget.points;
    if (!Array.isArray(points) || points.length < 2) return null;
    const ymin = Math.min(...points.map(p => p[1]));
    const ymax = Math.max(...points.map(p => p[1]));
    const normalizeX = x => 35 + 330 * (x - points[0][0]) / (points.at(-1)[0] - points[0][0]);
    const normalizeY = y => ymax === ymin ? 76 : 135 - 116 * (y-ymin) / (ymax-ymin);
    const ns = 'http://www.w3.org/2000/svg';
    function graphic(name, values) {
      const node = document.createElementNS(ns, name);
      for (const [key,value] of Object.entries(values)) node.setAttribute(key, value);
      return node;
    }
    const svg = graphic('svg', {viewBox:'0 0 380 160', role:'img', 'aria-label':widget.title});
    const marker = graphic('circle', {r:5, fill:'#f2c66f'});
    svg.append(graphic('path',{d:'M35 15 V135 H365',fill:'none',stroke:'currentColor'}),
      graphic('polyline',{fill:'none',stroke:'#48baff','stroke-width':2,
        points:points.map(([x,y])=>normalizeX(x)+','+normalizeY(y)).join(' ')}), marker);
    const slider = el('input', {type:'range', min:0, max:points.length-1, step:1, value:0, 'aria-label':'Inspect a plotted point'});
    const label = el('output', {class:'small', 'aria-live':'polite'});
    function plot() {
      const [x,y] = points[Number(slider.value)];
      marker.setAttribute('cx', normalizeX(x));
      marker.setAttribute('cy', normalizeY(y));
      label.textContent = widget.x_label + ' = ' + x + ' · ' + widget.y_label + ' = ' + y;
    }
    slider.addEventListener('input', plot);
    plot();
    return el('section', {class:'learning-card', 'aria-label':'Interactive plot'}, el('strong', {}, widget.title),
      svg, el('label', {}, 'Inspect a point ', slider), label);
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

  function mount(widgets, parent, next, checklistParent, session, messageId, responses = []) {
    (widgets || []).forEach((widget, index) => {
      const answered = responses.find(m => m.practice.widget === index);
      if (widget.kind === 'quiz' || widget.kind === 'question') parent.append(questionCard(widget, next, messageId, index, answered));
      if (widget.kind === 'graph') parent.append(graph(widget));
      if (widget.kind === 'plot') {
        const chart = sampledPlot(widget);
        if (chart) parent.append(chart);
      }
      if (widget.kind === 'checklist' && checklistParent) {
        checklistParent.replaceChildren();
        checklist(session, checklistParent, widget.items);
      }
    });
  }
  window.PODALearning = {mount, checklist, sources};
})();
