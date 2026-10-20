/* PODA Daily Updates: scheduled local prompts and output history. */
(function(){
  'use strict';
  const {route,el,api,toast,confirmDialog,errorDetail}=window.PODA;
  const md=window.PODAMarkdown;
  const field=(label,node)=>el('label',{class:'field'},el('span',{class:'label'},label),node);
  const input=(name,value='',type='text',extra={})=>el('input',{class:'input',name,type,value,...extra});
  const button=(label,fn,primary=false)=>el('button',{class:primary?'btn btn-primary':'btn btn-sm',type:'button',onclick:fn},label);
  const note=(msg)=>el('p',{class:'small muted'},msg);
  route('daily',async({view,inspector,setTitle,setInspectorTitle})=>{
    setTitle('Daily Updates');setInspectorTitle('Local scheduler');
    document.getElementById('viewCrumb').textContent='Scheduled questions · notifications · history';
    const root=el('div',{class:'screen stack daily-screen'});view.append(root);
    inspector.append(note('Prompts run locally when PODA is open. A Mac asleep or shut down cannot execute them until PODA is awake within the 10-minute catch-up window.'),
      note('Messages are created in the encrypted PODA database and macOS notifications are delivered when permitted. Writes to email, files or Notion still require manual approval.'));
    let editing=null;
    const render=async()=>{
      let data;
      try{data=await api('/daily-updates');}catch(e){root.replaceChildren(note(errorDetail(e).message));return;}
      root.replaceChildren(el('div',{class:'daily-top'},
        el('div',{},el('h2',{},'Your Daily Updates'),note('Give PODA a question to answer automatically at one or several times every day.')),
        button('Refresh results',render)));
      const columns=el('div',{class:'daily-columns'}),scheduleCol=el('div',{class:'stack'}),historyCol=el('div',{class:'stack'});
      columns.append(scheduleCol,historyCol);root.append(columns);
      const form=el('form',{class:'card stack'});
      const title=input('title','','text',{required:true,maxlength:120,placeholder:'Morning overview'});
      const prompt=el('textarea',{class:'input',rows:5,placeholder:'Example: Review important emails since yesterday, summarize meetings, and tell me what needs attention.'});
      const times=input('times','08:00','text',{required:true,placeholder:'08:00, 17:00'});
      const tz=input('timezone',Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC','text',{required:true});
      const enabled=el('input',{type:'checkbox',checked:true});
      const save=el('button',{class:'btn btn-primary',type:'submit'},'Save daily prompt');
      form.append(el('h3',{},'Create a recurring prompt'),
        field('Name',title),field('Question to run',prompt),
        field('Times (24-hour, comma separated)',times),field('Time zone (IANA)',tz),
        el('label',{class:'check'},enabled,'Active'),save);
      form.onsubmit=async e=>{
        e.preventDefault();save.disabled=true;
        const payload={title:title.value,prompt:prompt.value,times:times.value.split(',').map(x=>x.trim()).filter(Boolean),
                       timezone:tz.value.trim(),enabled:enabled.checked};
        try{
          await api(editing?'/daily-updates/'+encodeURIComponent(editing):'/daily-updates',
            {method:editing?'PUT':'POST',body:payload});
          editing=null;toast('Daily prompt saved locally','ok');await render();
        }catch(err){toast(errorDetail(err).message,'danger');}finally{save.disabled=false;}
      };
      scheduleCol.append(form);
      const schedules=el('section',{class:'card stack'},el('h3',{},'Your schedules'));
      if(!data.schedules?.length)schedules.append(note('No daily prompts yet. Create one to start.'));
      for(const s of data.schedules||[]){
        schedules.append(el('div',{class:'daily-entry'},
          el('div',{},el('strong',{},s.title),note(s.times.join(' · ')+' · '+s.timezone),
            note(s.enabled?'Enabled':'Paused'),note(s.prompt.slice(0,190))),
          el('div',{class:'row'},
            button('Run now',async()=>{
              try{
                await api('/daily-updates/'+encodeURIComponent(s.id)+'/run',{method:'POST'});
                toast('Daily prompt started; refresh for its result','ok');await render();
              }catch(err){toast(errorDetail(err).message,'danger');}
            }),
            button('Edit',()=>{
              editing=s.id;title.value=s.title;prompt.value=s.prompt;times.value=s.times.join(', ');
              tz.value=s.timezone;enabled.checked=s.enabled;save.textContent='Save changes';title.focus();
            }),
            button(s.enabled?'Pause':'Resume',async()=>{
              try{
                await api('/daily-updates/'+encodeURIComponent(s.id),{method:'PUT',
                  body:{title:s.title,prompt:s.prompt,times:s.times,timezone:s.timezone,enabled:!s.enabled}});
                render();
              }catch(err){toast(errorDetail(err).message,'danger');}
            }),
            button('Remove',async()=>{
              if(!(await confirmDialog({title:'Delete scheduled prompt?',body:'Existing result history is retained.',confirmLabel:'Delete'})))return;
              try{await api('/daily-updates/'+encodeURIComponent(s.id),{method:'DELETE'});render();}
              catch(err){toast(errorDetail(err).message,'danger');}
            }))));
      }
      scheduleCol.append(schedules);
      const history=el('section',{class:'card stack'},el('div',{class:'row-between'},
        el('h3',{},'Completed updates'),button('Refresh',render)));
      if(!data.runs?.length)history.append(note('Your scheduled responses will appear here.'));
      for(const r of (data.runs||[]).slice(0,60)){
        const details=el('details',{class:'daily-result'},el('summary',{},
          el('span',{},r.title||'Daily update'),el('span',{class:'chip'},r.status),
          el('span',{class:'small muted'},new Date(r.scheduled_for).toLocaleString())));
        details.append(el('div',{class:'daily-result-body'},r.response?
          el('div',{class:'markdown'},el('div',{html:md.render(r.response)})):
          note(r.error||'Still processing...'),note(r.notified?'macOS notification sent':'Notification not confirmed')));
        history.append(details);
      }
      historyCol.append(history);
    };
    await render();
  });
})();
