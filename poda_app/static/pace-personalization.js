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
      note('Enter only sleep, meals, and free time. PODA infers open work blocks from connected calendar commitments.'));
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
      for(const [key,label,min,max] of [['sleep_hours','Sleep per night (hours)',4,12],['meal_minutes','Meals per day (minutes)',0,360],
          ['free_minutes','Free time per day (minutes)',0,600]]){
        const control=input(key,prefs[key],'number',{min,max});
        controls.push([key,control]);pf.append(field(label,control));
      }
      pf.append(note('Sleep provisionally begins at midnight; meals and free time are daily allowances, not fixed appointments.'));
      const savePrefs=el('button',{class:'btn btn-primary',type:'submit'},'Save schedule preferences');
      pf.append(savePrefs);
      pf.onsubmit=async e=>{
        e.preventDefault();
        const next=Object.fromEntries(controls.map(([k,v])=>[k,+v.value]));
        // Internal focus block defaults remain automatic.
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
    const skills=el('section',{class:'card stack'},el('h2',{},'Skills & harness reference uploads'),
      note('Markdown or ZIP containing SKILL.md. Encrypted locally, disabled initially; never executes uploaded scripts.'));
    view.querySelector('.screen')?.append(skills);
    const formSkills=el('form',{class:'stack'});
    const skillName=input('name','','text',{placeholder:'Optional skill name'});
    const skillCategory=el('select',{class:'input'},...['general','coding','scheduling','email','harness'].map(x=>el('option',{value:x},x)));
    const skillFile=el('input',{class:'input',type:'file',accept:'.md,.txt,.zip'});
    const skillList=el('div',{class:'stack-sm'});
    formSkills.append(field('Name',skillName),field('Category',skillCategory),field('SKILL.md / .txt / .zip',skillFile),
      el('button',{class:'btn btn-primary',type:'submit'},'Upload (inactive)'));
    skills.append(formSkills,skillList);
    const refreshSkills=async()=>{
      try{
        const data=await api('/personalize/skills');skillList.replaceChildren();
        for(const item of data.skills||[]){
          const toggle=el('input',{type:'checkbox',checked:!!item.enabled});
          toggle.onchange=async()=>{try{await api('/personalize/skills/'+encodeURIComponent(item.id),{method:'PUT',body:{enabled:toggle.checked}});
            toast('Skill updated','ok');}catch(e){toggle.checked=!toggle.checked;toast(errorDetail(e).message,'danger');}};
          const remove=el('button',{class:'btn btn-sm',type:'button',onclick:async()=>{
            if(!(await confirmDialog({title:'Delete skill?',body:'Remove this reference from PODA.',confirmLabel:'Delete'})))return;
            await api('/personalize/skills/'+encodeURIComponent(item.id),{method:'DELETE'});refreshSkills();
          }},'Delete');
          skillList.append(el('div',{class:'row'},el('label',{class:'check'},toggle,item.name+' ('+item.category+')'),remove));
        }
        if(!data.skills?.length)skillList.append(note('No custom skills.'));
      }catch(e){skillList.replaceChildren(note(errorDetail(e).message));}
    };
    formSkills.onsubmit=async e=>{
      e.preventDefault();if(!skillFile.files?.[0])return toast('Choose a file','danger');
      const fd=new FormData();fd.append('file',skillFile.files[0]);fd.append('name',skillName.value);fd.append('category',skillCategory.value);
      try{
        const resp=await fetch('/personalize/skills',{method:'POST',body:fd,credentials:'same-origin'});
        if(!resp.ok){const data=await resp.json();throw Error(data.detail||'Upload failed');}
        formSkills.reset();toast('Skill stored locally, disabled by default','ok');refreshSkills();
      }catch(err){toast(errorDetail(err).message,'danger');}
    };
    await refreshSkills();
    const memoryLearning=el('section',{class:'card stack'},el('h2',{},'Memory learning · Hindsight'),
      note('PODA keeps its encrypted memory graph authoritative. Optional Hindsight adds local multi-strategy retrieval and consolidations; it must run on this Mac using only Ollama. No external provider or automatic service installation.'));
    const memoryStatus=el('div',{class:'stack-sm'});
    const enableMemory=el('input',{type:'checkbox'});
    const enableLine=el('label',{class:'check'},enableMemory,'Use local Hindsight derived index (requires manually installed service)');
    const syncButton=el('button',{class:'btn btn-sm',type:'button',onclick:async()=>{
      syncButton.disabled=true;
      try{
        const data=await api('/memory/hindsight/sync',{method:'POST'});
        toast('Synchronized '+data.synchronized+' nodes; removed '+data.deleted+' deleted references','ok');
        refreshHindsight();
      }catch(e){toast(errorDetail(e).message,'danger');}finally{syncButton.disabled=false;}
    }},'Sync committed memory nodes');
    const refreshHindsight=async()=>{
      try{
        const data=await api('/memory/hindsight/status');
        enableMemory.checked=!!data.enabled;enableMemory.disabled=!data.local_server_reachable&&!data.enabled;
        syncButton.disabled=!data.enabled;
        memoryStatus.replaceChildren(note('Local service '+(data.local_server_reachable?'reachable':'not connected')+
           ' · '+data.indexed_nodes+' indexed PODA nodes. '+data.warning));
      }catch(e){memoryStatus.replaceChildren(note(errorDetail(e).message));}
    };
    enableMemory.onchange=async()=>{
      const value=enableMemory.checked;
      try{await api('/memory/hindsight/config',{method:'PUT',body:{enabled:value}});toast('Hindsight configuration updated','ok');}
      catch(e){enableMemory.checked=!value;toast(errorDetail(e).message,'danger');}
      refreshHindsight();
    };
    const reviewQuery=input('memory-search','','text',{placeholder:'Search memory to correct or restore'});
    const reviewResults=el('div',{class:'stack-sm'});
    const searchMemory=el('button',{class:'btn btn-sm',type:'button',onclick:async()=>{
      if(!reviewQuery.value.trim())return;
      try{
        const result=await api('/memory/search?q='+encodeURIComponent(reviewQuery.value.trim())+'&limit=12');
        reviewResults.replaceChildren();
        for(const node of result.results||[]){
          if(node.node_type!=='memory')continue;
          const choice=el('select',{class:'input'},
            ...['helpful','incorrect','outdated','restore'].map(t=>el('option',{value:t},t)));
          const commit=el('button',{class:'btn btn-sm',type:'button',onclick:async()=>{
            try{
              await api('/memory/hindsight/feedback',{method:'POST',
                body:{node_id:node.id,assessment:choice.value,reason:'User-reviewed in Personalize'}});
              toast('Memory quality updated in PODA graph','ok');
            }catch(err){toast(errorDetail(err).message,'danger');}
          }},'Apply');
          reviewResults.append(el('div',{class:'stack-sm'},el('strong',{},node.title||'Untitled memory'),
             note((node.summary||'').slice(0,250)),el('div',{class:'row'},choice,commit)));
        }
        if(!reviewResults.childNodes.length)reviewResults.append(note('No matching memories.'));
      }catch(err){toast(errorDetail(err).message,'danger');}
    }},'Find memories');
    memoryLearning.append(enableLine,memoryStatus,syncButton,
      el('h3',{},'Correct outdated or misleading memories'),
      note('Changes affect retrieval immediately; the underlying node remains visible and can be restored.'),
      reviewQuery,searchMemory,reviewResults);
    view.querySelector('.screen')?.append(memoryLearning);
    await refreshHindsight();
    inspector.append(note('Your preferred work speed is calculated from your own estimates and actual completion durations; no cloud model is used to store this data.'));
    await refresh();
  }
  window.PODAPace={mount};
})();
