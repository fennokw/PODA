/* PODA Project Manager: one central project dashboard; legacy Plan/Workload APIs remain usable. */
(function () {
  'use strict';
  const {route, el, api, toast, confirmDialog, promptDialog, errorDetail, navigate} = window.PODA;
  const fmt = (m) => m < 60 ? m + ' min' : (Math.round(m / 6) / 10) + ' hr';
  const inp = (name, value='', type='text', attrs={}) => el('input', {class:'input',name,type,value:value ?? '',...attrs});
  const field = (name,label,node) => el('label',{class:'field'},el('span',{class:'label'},label),node || inp(name));
  const btn = (label,click,primary=false) => el('button',{type:'button',class:primary?'btn btn-primary':'btn',onclick:click},label);
  const text = (value,cls='muted small')=>el('p',{class:cls},String(value??''));
  const projectRoute = (id, taskId) => 'projects?project='+encodeURIComponent(id)+(taskId?'&task='+encodeURIComponent(taskId):'');
  const formatDate = d=>d?String(d).slice(0,10):'No deadline';
  const taskProgress = task => {
    const progress = Number(task.progress || 0);
    return el('div', {class:'stack-sm'}, el('progress', {value:progress,max:100,'aria-label':task.title+' completion'}),
      text(progress === 100 ? 'Completed' : progress+'% complete'));
  };
  const displayDate = value => value?.includes('T') ? new Date(value).toLocaleString() : formatDate(value);
  const dateInputValue = value => {
    if (!value?.includes('T')) return value || '';
    const date = new Date(value);
    return new Date(date.getTime() - date.getTimezoneOffset()*60000).toISOString().slice(0,16);
  };
  const categories = ['general','homework','study','writing','coding','research','admin','creative','reading'];

  route('projects', async ({view,inspector,setTitle,setInspectorTitle})=>{
    setTitle('Projects'); setInspectorTitle('Project overview');
    document.getElementById('viewCrumb').textContent='Projects · source files · tasks · timeline';
    const screen=el('div',{class:'screen stack pm-screen'});
    view.appendChild(screen);
    inspector.append(
      text('PODA uses one project list backed by the original project records and workload-time calibration.','small muted'),
      text('Uploaded documents live only in the encrypted PODA database. Linked computer files remain on disk and require Files & access permission to be read.','small muted'),
      text('Proposed sessions are drafts, never writes to Notion until separately approved.','small muted'));

    const params = new URLSearchParams(location.hash.split('?')[1] || '');
    const openedTask = params.get('task');
    let selectionVersion = 0;
    const notice=(msg)=>toast(msg,'ok');
    const showError=(err)=>toast(errorDetail(err).message,'danger');
    const reset=()=>screen.replaceChildren();

    async function dashboard(){
      const version = ++selectionVersion; reset();
      let projects;
      try {projects=(await api('/project-manager/projects')).projects || [];}
      catch(err){screen.append(text(errorDetail(err).message));return;}
      if (version !== selectionVersion) return;
      const head=el('div',{class:'pm-header'},
        el('div',{},el('h2',{},'Your projects'),
          text('See what you’re working on, what is due, and how much time is left.')),
        btn('+ New project',create,true));
      screen.append(head);
      const active=projects.filter(p=>p.progress<100), complete=projects.filter(p=>p.progress>=100);
      const totalMinutes=active.reduce((sum,p)=>sum+Number(p.remaining_minutes||0),0);
      screen.append(text(active.length+' active · '+complete.length+' complete · '+fmt(totalMinutes)+' remaining'));
      const search=inp('filter','','search',{placeholder:'Search project titles and notes…'});
      const cards=el('div',{class:'pm-cards'});
      screen.append(field('filter','Find a project',search),cards);
      function draw(){
        cards.replaceChildren();
        const q=search.value.trim().toLowerCase();
        const matches=projects.filter(p=>(p.name+' '+(p.notes||'')).toLowerCase().includes(q));
        if(!matches.length){cards.append(text(projects.length?'No matching projects.':'No projects yet. Create a project to begin.'));return;}
        for(const p of matches){
          const progress=Math.max(0,Math.min(100,Number(p.progress||0)));
          const tasks = p.tasks || [];
          const card=el('article',{class:'pm-project stack-sm'},
            el('div',{class:'pm-project-head'},el('h3',{},p.name),el('span',{class:'chip'},progress+'%')),
            el('progress',{value:progress,max:100,'aria-label':p.name+' completion'}),
            text(p.open_task_count+' open · '+fmt(p.remaining_minutes||0)+' remaining · '+formatDate(p.deadline)),
            p.notes ? text(p.notes.slice(0,160)) : null,
            el('div',{class:'pm-card-tasks stack-sm'},el('strong',{},'Tasks'),
              ...(tasks.length ? tasks.slice(0,3).map(task=>el('a',{
                href:'#/'+projectRoute(p.id,task.id)},task.title+' · '+task.progress+'%')) : [text('No tasks yet.')]),
              tasks.length>3 ? text('+'+(tasks.length-3)+' more in the project') : null),
            el('a',{class:'btn',href:'#/'+projectRoute(p.id),'aria-label':'Open project '+p.name},'Open project'));
          cards.append(card);
        }
      }
      search.addEventListener('input',draw);draw();
    }

    async function create(){
      const name=await promptDialog({title:'Create a project',label:'Project name',placeholder:'e.g. Aerodynamics problem set'});
      if(!name?.trim())return;
      try{
        const p=await api('/project-manager/projects',{method:'POST',body:{name:name.trim()}});
        notice('Project created');navigate('projects?project='+encodeURIComponent(p.id));
      }catch(err){showError(err);}
    }

    const projectURL = id => '/project-manager/projects/'+encodeURIComponent(id);
    function chatList(project, title, entries, taskId=null) {
      const panel=el('section',{class:'card stack'},el('div',{class:'row-between'},el('h3',{},title),
        btn('New chat',async()=>{
          try {
            const created=await api(projectURL(project.id)+'/chats',{method:'POST',body:{task_id:taskId}});
            navigate('ask?session='+encodeURIComponent(created.session_id));
          } catch(err){showError(err);}
        })));
      const picker=el('details',{class:'pm-chat-picker'});
      const query=inp('chat_search','','search',{'aria-label':'Search chat titles',placeholder:'Search chat titles'});
      const results=el('div',{class:'stack-sm','aria-busy':'false'});
      const search=btn('Search',()=>loadChats());
      async function loadChats(){
        search.disabled=true;
        results.setAttribute('aria-busy','true');
        results.replaceChildren(text('Finding chats…'));
        try{
          const {chats}=await api('/project-manager/chats?query='+encodeURIComponent(query.value.trim()));
          results.replaceChildren();
          if(!chats.length)results.append(text(query.value?'No matching chats.':'No saved chats yet.'));
          for(const chat of chats){
            const current=chat.project_id===project.id && (chat.task_id||null)===taskId;
            const action=btn(current?'Filed here':chat.project_name?'Move here':'File here',async()=>{
              action.disabled=true;
              try {
                await api(projectURL(project.id)+'/chats',{method:'POST',body:{task_id:taskId,session_id:chat.session_id}});
                notice('Chat filed');
                await detail(project.id,taskId);
              }catch(err){showError(err);action.disabled=false;}
            });
            action.disabled=current;
            results.append(el('div',{class:'pm-picker-result'},el('div',{},el('strong',{},chat.title),
              text([chat.project_name,chat.task_title].filter(Boolean).join(' / ')||'Unfiled')),action));
          }
        }catch(err){results.replaceChildren(text(errorDetail(err).message));}
        finally{search.disabled=false;results.setAttribute('aria-busy','false');}
      }
      query.addEventListener('keydown',event=>{
        if(event.key==='Enter' && !event.isComposing && !search.disabled){event.preventDefault();loadChats();}
      });
      picker.append(el('summary',{},'File existing chat'),el('div',{class:'pm-chat-search'},query,search),results);
      picker.addEventListener('toggle',()=>{
        if(picker.open&&!results.hasChildNodes())loadChats();
      });
      panel.append(picker);
      if(!entries?.length)panel.append(text('No chats filed here yet.'));
      for(const chat of entries||[]){
        const task=project.tasks.find(t=>t.id===chat.task_id);
        panel.append(el('a',{class:'pm-chat-link',href:'#/ask?session='+encodeURIComponent(chat.session_id)},
          el('strong',{},chat.title),task&&!taskId?text(task.title):null));
      }
      return panel;
    }

    async function detail(id, taskId=null){
      const version=++selectionVersion; reset();
      let p;
      try {p=await api('/project-manager/projects/'+encodeURIComponent(id));}
      catch(err){screen.append(text(errorDetail(err).message));return;}
      if (version !== selectionVersion) return;
      const breadcrumbs=el('nav',{class:'row pm-crumbs','aria-label':'Project navigation'},
        el('a',{href:'#/projects'},'Projects'),taskId?el('a',{href:'#/'+projectRoute(p.id)},p.name):null);
      const header=el('div',{class:'pm-header'},el('div',{},breadcrumbs,el('h2',{},p.name),
        taskId?null:text(p.open_task_count+' open tasks · '+fmt(p.remaining_minutes||0)+' remaining')),
        taskId?null:btn('Delete project',async()=>{
          if(!(await confirmDialog({title:'Delete project?',body:'Project documents and links will be removed. Existing workload tasks will be retained as unassigned tasks.',confirmLabel:'Delete project',danger:true})))return;
          try{await api(projectURL(id),{method:'DELETE'});notice('Project removed');navigate('projects');}catch(err){showError(err);}
        }));
      screen.append(header);
      const openTask = id => navigate(projectRoute(p.id,id));
      const taskRail=el('aside',{class:'card stack pm-task-rail'},el('h3',{},'Task folders'));
      for(const task of p.tasks||[])taskRail.append(el('a',{class:'pm-task-choice',href:'#/'+projectRoute(p.id,task.id),
        'aria-current':task.id===taskId?'page':null},el('strong',{},task.title),taskProgress(task),text(displayDate(task.deadline))));
      if(!p.tasks?.length)taskRail.append(text('No task folders yet.'));
      renderTaskCreation(taskRail,p,openTask);
      if(taskId){
        const content=el('div',{class:'stack pm-task-content'});
        screen.append(el('div',{class:'pm-workspace'},taskRail,content));
        try {
          const task=await api(projectURL(id)+'/tasks/'+encodeURIComponent(taskId));
          if(version!==selectionVersion)return;
          renderTask(content,p,task);
        }catch(err){content.append(text(errorDetail(err).message));}
        return;
      }
      const content=el('div',{class:'stack pm-task-content'});
      screen.append(el('div',{class:'pm-workspace'},taskRail,content));
      content.append(chatList(p,'Project chats',p.chats));
      const detailsForm=el('form',{class:'stack'});
      const projectName=inp('name',p.name,'text',{required:true,maxlength:160});
      const notes=el('textarea',{class:'input',name:'notes',rows:5},p.notes||'');
      detailsForm.append(field('name','Project name',projectName),field('notes','Overview, milestones and notes',notes));
      const imp=inp('importance',p.importance,'number',{min:1,max:10});
      const diff=inp('difficulty',p.difficulty,'number',{min:1,max:10});
      const hours=inp('hours_remaining',p.hours_remaining,'number',{min:0,step:0.25});
      const deadline=inp('deadline',formatDate(p.deadline)==='No deadline'?'':formatDate(p.deadline),'date');
      const progress=inp('progress',p.progress,'number',{min:0,max:100});
      detailsForm.append(el('div',{class:'pm-fields'},field('importance','Importance / 10',imp),field('difficulty','Difficulty / 10',diff),
        field('hours_remaining','Legacy estimated hours',hours),field('deadline','Deadline',deadline),field('progress','Progress %',progress)));
      const save=el('button',{type:'submit',class:'btn btn-primary'},'Save project');
      detailsForm.append(save);
      detailsForm.onsubmit=async e=>{
        e.preventDefault();save.disabled=true;
        try{
          await api('/project-manager/projects/'+encodeURIComponent(id),{method:'PATCH',body:{
            name:projectName.value,notes:notes.value,importance:+imp.value,difficulty:+diff.value,
            hours_remaining:+hours.value,deadline:deadline.value||null,progress:+progress.value}});
          notice('Project saved'); await detail(id);
        }catch(err){showError(err);}finally{save.disabled=false;}
      };
      content.append(el('details',{class:'card pm-disclosure'},el('summary',{},'Project settings'),detailsForm));

      const docs=el('div',{class:'card stack'},el('h3',{},'Documents & linked files'),
        text('Upload actual instructions and source data or link to a file under a granted folder.'));
      const attach=el('input',{class:'input',type:'file',accept:'.pdf,.docx,.txt,.md,.csv'});
      const upload=btn('Upload document',async()=>{
        const file=attach.files?.[0];if(!file){toast('Choose a document first','danger');return;}
        const form=new FormData();form.append('file',file);
        upload.disabled=true;
        try{
          const resp=await fetch('/project-manager/projects/'+encodeURIComponent(id)+'/attachments',
            {method:'POST',body:form,credentials:'same-origin',cache:'no-store'});
          const raw=await resp.json();if(!resp.ok)throw Error(raw.detail||'Upload failed');
          notice('Document stored in encrypted PODA database');
          if(raw.suggested_tasks?.length){
            const suggestion=raw.suggested_tasks[0];
            if(await confirmDialog({title:'Create a task from the uploaded work?',body:'Suggested: '+suggestion.title,confirmLabel:'Add task'})){
              await api('/workload',{method:'POST',body:{...suggestion,project:p.name}});
              notice('Suggested work item added');
            }
          }
          detail(id);
        }catch(err){showError(err);}finally{upload.disabled=false;}
      });
      const link=btn('Link local file',async()=>{
        const path=await promptDialog({title:'Link a local file',label:'Absolute path (grant the folder in Files & Agent first)',placeholder:'/Users/me/Documents/assignment.pdf'});
        if(!path)return;
        try{await api('/project-manager/projects/'+encodeURIComponent(id)+'/links',{method:'POST',body:{path}});
          notice('File link saved; permissions remain enforced');detail(id);
        }catch(err){showError(err);}
      });
      docs.append(attach,el('div',{class:'row'},upload,link));
      if(!p.attachments?.length) docs.append(text('No files yet.'));
      for(const att of p.attachments||[]){
        const base='/project-manager/projects/'+encodeURIComponent(id)+'/attachments/'+encodeURIComponent(att.id);
        docs.append(el('div',{class:'pm-attachment'},
          el('div',{},el('strong',{},att.filename),text(att.kind+' · '+Math.round((att.size_bytes||0)/1024)+' KB')),
          el('div',{class:'row'},
            el('a',{class:'btn btn-sm',href:base+'/download',download:att.filename},'Download'),
            btn('Preview',async()=>{
              try{
                const extracted=await api(base+'/text');
                const panel=el('div',{class:'pm-document-preview'},el('h4',{},extracted.filename),
                  text('Document text is untrusted external content. It is never followed as instructions.'),
                  el('pre',{class:'json'},(extracted.text||'No extractable text').slice(0,12000)));
                const existing=docs.querySelector('.pm-document-preview');if(existing)existing.remove();
                docs.append(panel);
              }catch(err){showError(err);}
            }),
            btn('Remove',async()=>{
              if(!(await confirmDialog({title:'Remove file from project?',body:'Linked source files on disk are not deleted.',confirmLabel:'Remove'})))return;
              try{await api(base,{method:'DELETE'});notice('File unlinked');detail(id);}catch(err){showError(err);}
            }))));
      }
      content.append(docs);

      const schedule=el('div',{class:'card stack'},el('h3',{},'Suggested work sessions'),
        text('Built from your pace calibration and availability. Notion events are read if connected; nothing is written.'));
      const scheduleBox=el('div',{class:'stack-sm'});
      schedule.append(btn('Generate 14-day plan',async()=>{
        scheduleBox.replaceChildren(text('Calculating…'));
        try{
          const res=await api('/workload/schedule?days=14&include_calendar=true');
          const sessions=(res.sessions||[]).filter(x=>x.project?.toLowerCase()===p.name.toLowerCase());
          scheduleBox.replaceChildren();
          if(!sessions.length)scheduleBox.append(text('No time blocks allocated. Check task dates, available time, and calendar connection.'));
          else for(const s of sessions.slice(0,24))scheduleBox.append(
            el('div',{class:'pm-session'},el('strong',{},s.title),text(new Date(s.start).toLocaleString()+' · '+fmt(s.minutes))));
          if(res.unscheduled?.length)scheduleBox.append(text('Some work could not fit in the planning window.','small muted'));
          scheduleBox.append(text(res.calendar||'Calendar availability unverified.'));
        }catch(err){scheduleBox.replaceChildren(text(errorDetail(err).message));}
      },true),scheduleBox);
      content.append(schedule);
    }
    function renderTaskCreation(rail, project, openTask){
      const taskForm=el('form',{class:'pm-task-form'});
      const title=inp('title','','text',{required:true,maxlength:200});
      const minutes=inp('user_minutes','','number',{min:1});
      const category=el('select',{class:'input',name:'category'},...categories.map(t=>el('option',{value:t},t)));
      const due=inp('deadline','','date');
      const difficulty=inp('difficulty',5,'number',{min:1,max:10});
      const priority=inp('importance',5,'number',{min:1,max:10});
      const submit=el('button',{class:'btn btn-primary',type:'submit'},'Create task folder');
      taskForm.append(field('title','Task name',title),field('category','Category',category),
        field('deadline','Due date (optional)',due),field('user_minutes','Estimate in minutes (optional)',minutes),
        el('div',{class:'grid-2'},field('difficulty','Difficulty',difficulty),field('importance','Priority',priority)),submit);
      taskForm.onsubmit=async e=>{
        e.preventDefault();
        submit.disabled=true;
        try{
          const created=await api('/workload',{method:'POST',body:{
            title:title.value,project:project.name,user_minutes:minutes.value?+minutes.value:null,
            difficulty:+difficulty.value,importance:+priority.value,category:category.value,deadline:due.value||null}});
          if(due.value){
            try{await api(projectURL(project.id)+'/tasks/'+encodeURIComponent(created.id),{method:'PATCH',body:{deadline:due.value}});}
            catch(err){showError(err);}
          }
          openTask(created.id);
          notice('Task folder added');
        }catch(err){showError(err);}finally{submit.disabled=false;}
      };
      rail.append(el('details',{class:'pm-disclosure'},el('summary',{},'Add task'),taskForm));
    }

    function renderTask(root, project, task) {
      const path=projectURL(project.id)+'/tasks/'+encodeURIComponent(task.id);
      const tracking=task.tracking || {};
      const form=el('form',{class:'card stack'});
      const title=inp('title',task.title,'text',{required:true,maxlength:200});
      const description=el('textarea',{class:'input',rows:3},task.description||'');
      const progress=inp('progress',task.progress,'number',{min:0,max:100,required:true});
      const minutes=inp('user_minutes',task.user_minutes??'','number',{min:1,placeholder:String(task.estimate.minutes)});
      const actual=inp('actual_minutes',task.actual_minutes??'','number',{min:1});
      const done=inp('done','','checkbox');
      done.checked=task.progress===100;
      const dueDay=inp('due_day',dateInputValue(task.deadline).slice(0,10),'date');
      const dueTime=inp('due_time',dateInputValue(task.deadline).slice(11),'time');
      const goal=inp('goal',tracking.goal||'','text',{maxlength:300,placeholder:'For example, finish 12 practice problems'});
      const target=inp('goal_target',tracking.goal_target??'','number',{min:1,max:1000000});
      const current=inp('goal_current',tracking.goal_current??0,'number',{min:0,max:1000000});
      const updateGoalPreview=()=>{
        progress.disabled=Boolean(target.value);
        current.disabled=!target.value;
        if(target.value && current.value!==''){
          progress.value=Math.min(100,Math.floor(100*Number(current.value)/Number(target.value)));
          done.checked=Number(current.value)>=Number(target.value);
        }
      };
      target.oninput=current.oninput=updateGoalPreview;
      done.onchange=()=>{
        if(target.value)current.value=done.checked?target.value:0;
        progress.value=done.checked?100:0;
      };
      progress.oninput=()=>{done.checked=Number(progress.value)===100;};
      updateGoalPreview();
      const save=el('button',{type:'submit',class:'btn btn-primary'},'Save task');
      const dateLabel=task.deadline?displayDate(task.deadline):'No deadline';
      const headings=[el('h3',{},task.title),taskProgress(task)];
      if(tracking.event_title)headings.push(text('Deadline follows '+tracking.event_title+'. Editing the due date removes the event link.'));
      root.append(...headings);
      const report=message=>{if(message)root.prepend(text(message,'notice notice-warn'));};
      report(task.link_warning);
      if(tracking.calendar_error)root.append(text(tracking.calendar_error,'notice notice-warn'),
        btn('Retry calendar sync',async()=>{
          try{await api(path+'/retry-calendar',{method:'POST'});await detail(project.id,task.id);}catch(err){showError(err);}
        }));
      const rebuild=async (updates)=>{
        await api(path,{method:'PATCH',body:updates});
        await detail(project.id,task.id);
      };
      form.append(field('title','Task name',title),field('description','Notes',description),
        el('div',{class:'grid-2'},field('progress','Completion (%)',progress),field('done','Completed',done),
          field('user_minutes','Time estimate (minutes)',minutes),field('actual_minutes','Actual time (optional)',actual)),
        text(fmt(task.estimate.minutes)+' estimated · '+fmt(task.estimate.remaining_minutes)+' remaining'),
        el('div',{class:'grid-2'},field('due_day','Due date',dueDay),field('due_time','Due time (optional)',dueTime)),
        el('h3',{},'Measurable goal'),field('goal','Goal',goal),
        el('div',{class:'grid-2'},field('goal_target','Target count',target),field('goal_current','Completed count',current)),
        text('Reaching the target checks off the task. In a task chat, report the count, use /taskprogress followed by the count, or use /taskdone.'),
        save);
      form.onsubmit=async e=>{
        e.preventDefault();
        const priorDate=dateInputValue(task.deadline);
        const newDate=dueDay.value+(dueDay.value&&dueTime.value?'T'+dueTime.value:'');
        const updates={title:title.value,description:description.value,user_minutes:minutes.value?+minutes.value:null,
          actual_minutes:actual.value?+actual.value:null,goal:goal.value};
        if (+progress.value!==task.progress)updates.progress=+progress.value;
        if ((target.value?+target.value:null)!==(tracking.goal_target??null))updates.goal_target=target.value?+target.value:null;
        if (target.value&&+current.value!==(tracking.goal_current??0))updates.goal_current=+current.value;
        if (newDate!==priorDate)updates.deadline=newDate.includes('T')?new Date(newDate).toISOString():(newDate||null);
        save.disabled=true;
        try{await rebuild(updates);}catch(err){showError(err);}finally{save.disabled=false;}
      };
      const calendar=el('section',{class:'card stack'},el('h3',{},'Calendar & deadline'),
        text(task.deadline ? 'Due '+dateLabel : dateLabel));
      if(task.deadline && (tracking.scheduled || tracking.event_id))calendar.append(el('a',{
        class:'btn',href:'#/notion?day='+dateInputValue(task.deadline).slice(0,10)},'View in calendar'));
      if(task.deadline && !tracking.scheduled && !tracking.event_id)calendar.append(btn('Add deadline to calendar',async()=>{
        try{await rebuild({deadline:task.deadline});}catch(err){showError(err);}
      }));
      const linkPanel=el('details',{class:'pm-disclosure'},el('summary',{},'Link calendar event'));
      const today=new Date();
      const dateKey=d=>[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');
      const until=new Date(today);until.setDate(until.getDate()+30);
      const start=inp('from',dateKey(today),'date');
      const end=inp('to',dateKey(until),'date');
      const events=el('div',{class:'stack-sm'});
      linkPanel.append(el('div',{class:'grid-2'},field('from','From',start),field('to','Through',end)),
        btn('Find events',async()=>{
          events.replaceChildren(text('Loading events…'));
          try{
            const result=await api('/project-manager/calendar-events?start='+encodeURIComponent(start.value)+'&end='+encodeURIComponent(end.value));
            events.replaceChildren();
            const select=el('select',{class:'input','aria-label':'Calendar event'},
              ...result.events.map(ev=>el('option',{value:ev.page_id},ev.title+' · '+displayDate(ev.start))));
            if(result.events.length)events.append(select,btn('Use event start as deadline',async()=>{
              try{await rebuild({event_id:select.value,calendar_scope:result.scope});}catch(err){showError(err);}
            },true));else events.append(text('No events in this date range.'));
          }catch(err){events.replaceChildren(text(errorDetail(err).message));}
        }),events);
      if(tracking.event_id)linkPanel.append(btn('Unlink event and keep deadline',async()=>{
        try{await rebuild({event_id:null});}catch(err){showError(err);}
      }));
      calendar.append(linkPanel);
      root.append(calendar,chatList(project,'Task chats',task.chats,task.id),
        el('details',{class:'card pm-disclosure',open:true},el('summary',{},'Task details'),form));
    }

    if(params.get('project'))await detail(params.get('project'),openedTask);
    else await dashboard();
  });
})();
