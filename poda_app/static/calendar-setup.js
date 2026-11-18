/* Calendar configuration and editing. All data flows through the local API. */
(function(){
  'use strict';
  const {el,api,errorDetail,toast}=window.PODA;
  const label=(text,input)=>el('label',{class:'field'},el('span',{class:'label'},text),input);
  const button=(text,fn,kind='btn')=>el('button',{class:kind,type:'button',onclick:async()=>{try{await fn();}catch(e){toast(errorDetail(e).message,'danger');}}},text);
  const input=(value='')=>el('input',{class:'input',value});
  function setup(state){
    const selected=state.config.provider;
    const select=el('select',{class:'input','aria-label':'Calendar provider'},...[
      ['notion','Notion'],['local','PODA local · no account needed'],['google','Google Calendar']
    ].map(([value,text])=>el('option',{value,selected:value===selected},text)));
    const body=el('div',{class:'stack-sm'}),defaults=el('div',{class:'stack-sm'});
    const element=el('section',{class:'card stack-sm'},el('h2',{},'Calendar setup'),
      el('p',{class:'muted'},'One calendar serves chat, remote commands, reminders and daily automation. Switching preserves events; reauthorize unattended writes for the new destination.'),label('Calendar provider',select),body);
    async function activate(provider,calendar_id='',allow_write=true){
      await api('/calendar/provider',{method:'PUT',body:{provider,calendar_id,allow_write}});location.reload();
    }
    function draw(){
      body.replaceChildren();
      if(select.value==='notion'){
        body.append(el('p',{class:'small muted'},'Connect the original database below and map the same date property used by Notion Calendar.'),
          button(selected==='notion'?'Keep Notion active':'Use Notion',()=>activate('notion'),'btn btn-primary'),
          el('a',{href:'https://github.com/fennokw/BestNDS',target:'_blank',rel:'noopener'},'Kaden’s Notion dashboard setup'));
      }else if(select.value==='local'){
        body.append(el('p',{class:'muted'},'Events stay in PODA’s local database and work offline. No account is required. This is PODA’s own calendar, not Apple Calendar sync.'),button('Use PODA local calendar',()=>activate('local'),'btn btn-primary'));
      }else{
        const file=el('input',{type:'file',accept:'.json,application/json','aria-label':'Google Desktop OAuth client JSON'});
        file.addEventListener('change',async()=>{try{
          if(!file.files[0])return;if(file.files[0].size>64000)throw new Error('Choose the small OAuth client JSON file.');
          await api('/calendar/google/client',{method:'POST',body:JSON.parse(await file.files[0].text())});toast('Client saved securely. Sign in next.','ok');
        }catch(e){toast(errorDetail(e).message,'danger');}finally{file.value='';}});
        const login=el('div',{class:'row'}),calendars=el('div',{class:'stack-sm'});
        login.append(button('Sign in with Google',async()=>{const r=await api('/calendar/google/connect',{method:'POST'});login.append(el('a',{class:'btn btn-primary',href:r.url,target:'_blank',rel:'noopener'},'Continue in Google'));}));
        async function refresh(){
          const r=await api('/calendar/google/calendars');const choose=el('select',{class:'input','aria-label':'Google calendar'},...r.calendars.map(c=>el('option',{value:c.id,selected:c.id===state.config.calendar_id},c.summary+(c.accessRole==='reader'?' (read only)':''))));
          const write=el('input',{type:'checkbox',checked:state.config.allow_write!==false});
          const check=()=>{const c=r.calendars.find(c=>c.id===choose.value);write.disabled=!c||!['owner','writer'].includes(c.accessRole);if(write.disabled)write.checked=false;};choose.addEventListener('change',check);check();
          calendars.replaceChildren(label('Use this calendar',choose),el('label',{class:'check'},write,'Allow adding and editing events'),button('Use this Google calendar',()=>activate('google',choose.value,write.checked),'btn btn-primary'));
        }
        body.append(el('p',{class:'small muted'},'One-time setup: enable Google Calendar API in your Google Cloud project, configure its consent screen and add yourself as a test user, then create an OAuth client of type Desktop app and download its JSON. Upload it here, sign in in your browser, then select one calendar. Tokens stay in your computer’s credential store; AI processing stays local.'),
          el('a',{href:'https://console.cloud.google.com/apis/credentials',target:'_blank',rel:'noopener'},'Open Google Cloud credentials'),file,login,button('Refresh calendars after sign-in',refresh),calendars,
          button('Disconnect Google',async()=>{await api('/calendar/google/disconnect',{method:'POST'});location.reload();},'btn btn-danger'));
        if(state.google.connected)refresh().catch(e=>toast(errorDetail(e).message,'danger'));
      }
    }
    select.addEventListener('change',draw);draw();
    async function drawDefaults(){
      if(selected!=='notion')return;
      try{
        const r=await api('/calendar/notion/defaults');
        defaults.replaceChildren(el('h3',{},'New event defaults'),el('p',{class:'small muted'},`PODA writes dates to “${r.date_property||'not mapped yet'}”. Add this database/view to Notion Calendar, choose that date property, and check its filters against these defaults.`));
        const inputs={};for(const field of r.fields){
          const choice=el('select',{class:'input','aria-label':field.name},el('option',{value:''},`Automatic${r.suggested[field.key]?' · '+r.suggested[field.key]:''}`),...field.options.map(v=>el('option',{value:v},v)));
          choice.value=r.values[field.key]||'';inputs[field.key]=choice;defaults.append(label(field.name,choice));
        }
        if(r.missing.length)defaults.append(el('p',{class:'small'},'Needs review: '+r.missing.join(', ')));
        defaults.append(button('Save new event defaults',async()=>{const values=Object.fromEntries(Object.entries(inputs).filter(([,v])=>v.value).map(([k,v])=>[k,v.value]));await api('/calendar/notion/defaults',{method:'PUT',body:{values}});toast('Defaults saved. Existing entries are unchanged.','ok');drawDefaults();}));
      }catch(e){defaults.replaceChildren(el('p',{class:'small muted'},errorDetail(e).message));}
    }
    return {element,defaults,drawDefaults};
  }
  function newEvent(refresh,review){
    const form=el('details',{class:'disclosure'},el('summary',{},'Add event'));
    const title=input();title.placeholder='Event title';const start=el('input',{class:'input',type:'datetime-local'}),end=el('input',{class:'input',type:'datetime-local'});
    const feedback=el('p',{class:'small','aria-live':'polite'});
    const save=button('Add event',async()=>{
      if(!title.value.trim()||!start.value)throw new Error('Enter a title and start time.');
      save.disabled=true;try{
        const r=await api('/calendar/events',{method:'POST',body:{title:title.value,start:new Date(start.value).toISOString(),end:end.value?new Date(end.value).toISOString():''}});
        const text=r.result.review_question||(r.result.duplicate?'Matching event already exists.':'Event added.');feedback.textContent=text;review(text);refresh();
      }finally{save.disabled=false;}
    });
    form.append(label('Title',title),label('Start (this computer’s time zone)',start),label('End (optional)',end),save,feedback);return form;
  }
  function editor(ev,provider,refresh,fields={}){
    const form=el('details',{class:'disclosure'},el('summary',{},'Edit event details'));
    const values={title:ev.title,start:ev.start,end:ev.end||'',commitment:ev.commitment||'',area:(ev.area||[]).join(', ')};const inputs={};
    const task=ev.is_deadline??['task','homework','test','assignment','quiz','exam'].includes(String(ev.type).toLowerCase());
    if(task)delete values.commitment;
    for(const [k,v] of Object.entries(values)){inputs[k]=input(v);form.append(label(k==='start'||k==='end'?k+' (ISO date with timezone)':k,inputs[k]));}
    const kindKey=task?'task_kind':'event_kind';
    const choices=fields.kind_options?.[kindKey];
    let kindInput;
    if(provider==='notion' && choices){
      kindInput=el('select',{class:'input','aria-label':task?'Task kind':'Event kind'},el('option',{value:''},'Not set'),...choices.map(v=>el('option',{value:v},v)));
      kindInput.value=ev[kindKey]||'';form.append(label(task?'Task kind':'Event kind',kindInput));
    }
    for(const warning of ev.setup_warnings||[])form.append(el('p',{class:'notice notice-warn'},warning));
    const completed=el('input',{type:'checkbox',checked:!!ev.done});
    if(provider==='notion' && task && ev.done!==null && ev.done!==undefined)form.append(label('Completed',completed));
    const feedback=el('p',{class:'small','aria-live':'polite'});
    form.append(button('Save event details',async()=>{const body={};if(kindInput&&kindInput.value!==(ev[kindKey]||''))body[kindKey]=kindInput.value;if(provider==='notion' && task && ev.done!==null && ev.done!==undefined && completed.checked!==!!ev.done)body.completed=completed.checked;for(const [k,v] of Object.entries(inputs))if(v.value&&v.value!==values[k])body[k]=v.value;if(body.end&&!body.start)body.start=inputs.start.value;if(!Object.keys(body).length)return;await api('/calendar/events/'+encodeURIComponent(ev.page_id),{method:'PATCH',body});feedback.textContent='Event updated.';refresh();}),feedback);
    if(provider==='notion')form.append(button('Fill missing calendar fields',async()=>{const r=await api('/calendar/notion/events/'+encodeURIComponent(ev.page_id)+'/fill-missing',{method:'POST'});feedback.textContent=r.result.review_question||'Missing fields checked; existing values preserved.';refresh();}));
    return form;
  }
  window.PODACalendar={setup,newEvent,editor};
})();
