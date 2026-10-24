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
    setTitle('Daily Automation');setInspectorTitle('Local scheduler');
    document.getElementById('viewCrumb').textContent='Scheduled questions · notifications · history';
    const root=el('div',{class:'screen stack daily-screen'});view.append(root);
    inspector.append(note('Prompts run locally when PODA is open. A Mac asleep or shut down cannot execute them until PODA is awake within the 10-minute catch-up window.'),
      note('Runs are isolated from chat memory. Explicitly authorized email, calendar, file, and task changes can run unattended; other actions still require approval.'));
    let editing=null;
    const render=async()=>{
      let data;
      try{data=await api('/daily-updates');}catch(e){root.replaceChildren(note(errorDetail(e).message));return;}
      root.replaceChildren(el('div',{class:'daily-top'},
        el('div',{},el('h2',{},'Daily Automations'),note('Give PODA a question to answer automatically at one or several times every day.')),
        button('Refresh results',render)));
      const columns=el('div',{class:'daily-columns'}),scheduleCol=el('div',{class:'stack'}),historyCol=el('div',{class:'stack'});
      columns.append(scheduleCol,historyCol);root.append(columns);
      const form=el('form',{class:'card stack'});
      const title=input('title','','text',{required:true,maxlength:120,placeholder:'Morning overview'});
      const prompt=el('textarea',{class:'input',rows:5,placeholder:'Example: Review important emails since yesterday, summarize meetings, and tell me what needs attention.'});
      const times=input('times','08:00','text',{required:true,placeholder:'08:00, 17:00'});
      const tz=input('timezone',Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC','text',{required:true});
      const enabled=el('input',{type:'checkbox',checked:true});
      const permissionArea=el('div',{class:'stack-sm'});
      const configuredPermissions=[];
      let permissionCatalog=[],mailAccounts=[];
      try {
        const grants=await api('/daily-updates/permissions/catalog');
        permissionCatalog=grants.permissions||[];
        mailAccounts=grants.mail_accounts||[];
      } catch(e) { permissionArea.append(note(errorDetail(e).message)); }
      const selectedAccounts=new Set();
      const mailAccountArea=el('div',{class:'stack-sm'});
      const renderMailAccounts=()=>{
        mailAccountArea.replaceChildren(note('Select any combination of already-connected inboxes for this automation. Each outgoing email still needs its own approved sending permission.'));
        for(const account of mailAccounts){
          const box=el('input',{type:'checkbox',checked:selectedAccounts.has(account.id)});
          box.onchange=()=>{if(box.checked)selectedAccounts.add(account.id);else selectedAccounts.delete(account.id);redrawPermissions();};
          mailAccountArea.append(el('label',{class:'check'},box,account.account_name+' · '+account.address_masked));
        }
        if(!mailAccounts.length)mailAccountArea.append(note('No accounts connected. Add one in Mail first.'));
      };
      const redrawPermissions=()=>{
        permissionArea.replaceChildren(el('h4',{},'Unattended action permissions'),
          note('Each action and target must be approved separately. Mail grants are per selected account; no blanket send permission.'));
        for(const entry of permissionCatalog){
          const isMail=entry.tool.startsWith('mail_');
          const grants=configuredPermissions.filter(p=>p.tool===entry.tool);
          const section=el('div',{class:'stack-sm'},el('strong',{},entry.label));
          const allowedAccounts=isMail?mailAccounts.filter(a=>selectedAccounts.has(a.id)):[null];
          if(isMail && !allowedAccounts.length){section.append(note('Select an inbox above first.'));permissionArea.append(section);continue;}
          for(const account of allowedAccounts){
            const grant=grants.find(g=>!account||g.scope.account_id===account.id);
            const scoped={};
            const header=el('label',{class:'check'});
            const enabledGrant=el('input',{type:'checkbox',checked:!!grant,disabled:!entry.available && !grant});
            header.append(enabledGrant,account?account.account_name+' · '+account.address_masked:'Allow this action');
            const block=el('div',{class:'stack-sm'},header);
            for(const f of entry.fields||[]){
              if(f.name==='account_id')continue;
              let ctl;
              if(f.name==='action'){
                ctl=el('select',{class:'input'},...['mark_read','mark_unread','flag','unflag','archive'].map(v=>el('option',{value:v},v.replace('_',' '))));
                ctl.value=grant?.scope?.[f.name]||'mark_read';
              }else ctl=input(f.name,grant?.scope?.[f.name]||'','text',{placeholder:f.label});
              scoped[f.name]=ctl;
              block.append(field(f.label,ctl));
            }
            const sync=()=>{
              const index=configuredPermissions.findIndex(g=>g.tool===entry.tool && (!account||g.scope.account_id===account.id));
              if(index>=0)configuredPermissions.splice(index,1);
              if(enabledGrant.checked)configuredPermissions.push({tool:entry.tool,scope:{
                ...(account?{account_id:account.id}:{}),
                ...Object.fromEntries(Object.entries(scoped).map(([k,v])=>[k,v.value]))
              }});
            };
            enabledGrant.onchange=sync;
            for(const ctl of Object.values(scoped)){ctl.onchange=sync;ctl.oninput=sync;}
            section.append(block);
          }
          if(!entry.available)section.append(note(entry.reason));
          permissionArea.append(section);
        }
      };
      renderMailAccounts();
      redrawPermissions();
      const save=el('button',{class:'btn btn-primary',type:'submit'},'Save automation');
      form.append(el('h3',{},'Create daily automation'),
        field('Name',title),field('Automation prompt',prompt),
        field('Times (24-hour, comma separated)',times),field('Time zone (IANA)',tz),
        field('Connected inboxes',mailAccountArea),
        note('Accounts are loaded from the Mail page. No passwords, account IDs or access codes need to be entered here.'),
        el('label',{class:'check'},enabled,'Active'),
        permissionArea,save);
      form.onsubmit=async e=>{
        e.preventDefault();save.disabled=true;
        const payload={title:title.value,prompt:prompt.value,times:times.value.split(',').map(x=>x.trim()).filter(Boolean),
                       timezone:tz.value.trim(),enabled:enabled.checked,
                       permissions:configuredPermissions,mail_account_ids:Array.from(selectedAccounts)};
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
              tz.value=s.timezone;enabled.checked=s.enabled;selectedAccounts.clear();for(const id of (s.mail_account_ids|| (s.mail_account_id?[s.mail_account_id]:[]))) selectedAccounts.add(id);
              renderMailAccounts();
              configuredPermissions.splice(0,configuredPermissions.length,...(JSON.parse(s.permissions_json||'[]')));
              redrawPermissions();save.textContent='Save changes';title.focus();
            }),
            button(s.enabled?'Pause':'Resume',async()=>{
              try{
                await api('/daily-updates/'+encodeURIComponent(s.id),{method:'PUT',
                  body:{title:s.title,prompt:s.prompt,times:s.times,timezone:s.timezone,enabled:!s.enabled,permissions:JSON.parse(s.permissions_json||'[]'),mail_account_ids:s.mail_account_ids|| (s.mail_account_id?[s.mail_account_id]:[])}});
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
