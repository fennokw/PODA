/* Work-speed calibration is a personal preference, not a separate project list. */
(function () {
  'use strict';
  const {el,api,toast,confirmDialog,errorDetail}=window.PODA;
  const categories=['general','homework','study','writing','coding','research','admin','creative','reading'];
  const field=(label,node)=>el('label',{class:'field'},el('span',{class:'label'},label),node);
  const input=(name,value='',type='text',extra={})=>el('input',{class:'input',name,type,value,...extra});
  const note=(s)=>el('p',{class:'small muted'},s);

  async function mount(view,inspector) {
    const wrap=el('section',{class:'card stack pace-personalize'},
      el('div',{class:'card-head'},el('h2',{},'Personal work-speed calibration'),
        el('span',{class:'chip'},'Locally learned')),
      note('Teach PODA how long YOU need for comparable work. Every example adjusts category- and difficulty-weighted estimates across Project Manager.'));
    view.querySelector('.screen')?.append(wrap);
    const form=el('form',{class:'stack'});
    const title=input('title','','text',{required:true,maxlength:200,placeholder:'e.g. A 6-question calculus problem set'});
    const desc=el('textarea',{class:'input',rows:3,name:'description',placeholder:'Describe the work, or attach its actual instructions'});
    const cat=el('select',{class:'input',name:'category'},...categories.map(v=>el('option',{value:v},v)));
    const difficulty=input('difficulty',5,'number',{min:1,max:10});
    const expected=input('user_minutes','','number',{min:1,required:true,placeholder:'Your estimate in minutes'});
    const actual=input('actual_minutes','','number',{min:1,placeholder:'If finished, actual minutes'});
    const baseline=input('baseline_minutes','','number',{min:1,placeholder:'Optional independent baseline'});
    const file=el('input',{class:'input',name:'file',type:'file',accept:'.pdf,.docx,.txt,.md,.csv'});
    const save=el('button',{class:'btn btn-primary',type:'submit'},'Teach PODA my pace');
    form.append(field('Type of work',title),field('Description / scope',desc),
      el('div',{class:'grid-2'},field('Category',cat),field('Difficulty 1–10',difficulty)),
      el('div',{class:'grid-2'},field('My time estimate (minutes)',expected),field('Actual time (optional)',actual)),
      field('Independent baseline (optional)',baseline),
      field('Attach example work, assignment, or PDF (optional)',file),save);
    wrap.append(form);
    const feedback=el('div',{class:'stack-sm'}),history=el('div',{class:'stack-sm'});
    wrap.append(feedback,el('h3',{},'What PODA has learned'),history);
    const prefsCard=el('section',{class:'card stack pace-personalize'},el('h2',{},'Scheduling preferences'),
      note('Configure your normal work window and session length. These settings feed into the Project Manager scheduling engine.'));
    view.querySelector('.screen')?.append(prefsCard);
    async function refresh() {
      let data;
      try{data=await api('/workload/pace');}catch(err){history.textContent=errorDetail(err).message;return;}
      history.replaceChildren();
      if(!data.examples?.length)history.append(note('No examples yet. One honest estimate is a useful starting point, but actual completion times are much stronger evidence.'));
      for(const item of data.examples||[]){
        const row=el('div',{class:'pace-example'},
          el('div',{},el('strong',{},item.title),note(item.category+' · difficulty '+item.difficulty+
              ' · predicted '+item.user_minutes+' min'+(item.actual_minutes?' · actual '+item.actual_minutes+' min':''))),
          el('div',{class:'row'},
            item.filename?el('a',{class:'btn btn-sm',href:'/workload/pace/examples/'+encodeURIComponent(item.id)+'/download'},'Source document'):null,
            el('button',{class:'btn btn-sm',type:'button',onclick:async()=>{
              if(!(await confirmDialog({title:'Remove pace example?',body:'This removes its influence on future time estimates.',confirmLabel:'Remove'})))return;
              try{await api('/workload/pace/examples/'+encodeURIComponent(item.id),{method:'DELETE'});toast('Calibration example removed','ok');refresh();}
              catch(err){toast(errorDetail(err).message,'danger');}
            }},'Remove')));
        history.append(row);
      }
      const prefs=data.preferences||{};
      prefsCard.querySelector('form')?.remove();
      const pf=el('form',{class:'stack'});
      const controls=[];
      for(const [key,label,min,max] of [['start_hour','Workday begins (24h)',0,23],['end_hour','Workday ends (24h)',1,24],
          ['session_minutes','Focus block (minutes)',15,180],['break_minutes','Rest between blocks (minutes)',0,60],
          ['max_daily_minutes','Maximum work minutes per day',30,900]]){
        const control=input(key,prefs[key],'number',{min,max});
        controls.push([key,control]);pf.append(field(label,control));
      }
      const week=el('input',{type:'checkbox',checked:!!prefs.include_weekends});
      pf.append(el('label',{class:'check'},week,'Include weekends'));
      const savePrefs=el('button',{class:'btn btn-primary',type:'submit'},'Save schedule preferences');
      pf.append(savePrefs);
      pf.onsubmit=async e=>{
        e.preventDefault();
        const next=Object.fromEntries(controls.map(([k,v])=>[k,+v.value]));
        next.include_weekends=week.checked;
        try{await api('/workload/settings/preferences',{method:'PUT',body:next});
          toast('Planning preferences saved','ok');}
        catch(err){toast(errorDetail(err).message,'danger');}
      };
      prefsCard.append(pf);
      const calibrated=Object.entries(data.calibration||{}).filter(([k,v])=>v['5']?.examples)
        .map(([k,v])=>k+': '+v['5'].multiplier+'× at difficulty 5 ('+v['5'].examples+' examples)');
      feedback.replaceChildren(note(calibrated.join(' · ')||'Calibration starts at 1.0× until you add observations.'));
    }
    form.onsubmit=async e=>{
      e.preventDefault();save.disabled=true;
      const fd=new FormData();
      for(const control of [title,desc,cat,difficulty,expected,actual,baseline]){
        if(control.value!=='')fd.append(control.name,control.value);
      }
      if(file.files?.[0])fd.append('file',file.files[0]);
      try{
        const res=await fetch('/workload/pace/examples',{method:'POST',body:fd,credentials:'same-origin',cache:'no-store'});
        let data=await res.json();if(!res.ok)throw Error(data.detail||'Could not save pacing example');
        toast('PODA pace calibration updated','ok');form.reset();await refresh();
      }catch(err){toast(errorDetail(err).message,'danger');}
      finally{save.disabled=false;}
    };
    inspector.append(note('Your preferred work speed is calculated from your own estimates and actual completion durations; no cloud model is used to store this data.'));
    await refresh();
  }
  window.PODAPace={mount};
})();
