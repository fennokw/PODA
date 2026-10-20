/* PODA Project Manager: one central project dashboard; legacy Plan/Workload APIs remain usable. */
(function () {
  'use strict';
  const {route, el, api, toast, confirmDialog, promptDialog, errorDetail} = window.PODA;
  const fmt = (m) => m < 60 ? m + ' min' : (Math.round(m / 6) / 10) + ' hr';
  const inp = (name, value='', type='text', attrs={}) => el('input', {class:'input',name,type,value:value ?? '',...attrs});
  const field = (name,label,node) => el('label',{class:'field'},el('span',{class:'label'},label),node || inp(name));
  const btn = (label,click,primary=false) => el('button',{type:'button',class:primary?'btn btn-primary':'btn',onclick:click},label);
  const text = (value,cls='muted small')=>el('p',{class:cls},String(value??''));
  const formatDate = d=>d?String(d).slice(0,10):'No deadline';
  const categories = ['general','homework','study','writing','coding','research','admin','creative','reading'];

  route('projects', async ({view,inspector,setTitle,setInspectorTitle})=>{
    setTitle('Project Manager'); setInspectorTitle('Project overview');
    document.getElementById('viewCrumb').textContent='Projects · source files · tasks · timeline';
    const screen=el('div',{class:'screen stack pm-screen'});
    view.appendChild(screen);
    inspector.append(
      text('PODA uses one project list backed by the original project records and workload-time calibration.','small muted'),
      text('Uploaded documents live only in the encrypted PODA database. Linked computer files remain on disk and require Files & Agent permission to be read.','small muted'),
      text('Proposed sessions are drafts, never writes to Notion until separately approved.','small muted'));

    let selected=null;
    const notice=(msg)=>toast(msg,'ok');
    const showError=(err)=>toast(errorDetail(err).message,'danger');
    const reset=()=>screen.replaceChildren();

    async function dashboard(){
      selected=null; reset();
      let projects;
      try {projects=(await api('/project-manager/projects')).projects || [];}
      catch(err){screen.append(text(errorDetail(err).message));return;}
      const head=el('div',{class:'pm-header'},
        el('div',{},el('h2',{},'Project Manager'),
          text('Track every workstream from intake to completion, with evidence and realistic time estimates.')),
        btn('+ New project',create,true));
      screen.append(head);
      const active=projects.filter(p=>p.progress<100), complete=projects.filter(p=>p.progress>=100);
      const totalMinutes=active.reduce((sum,p)=>sum+Number(p.remaining_minutes||0),0);
      screen.append(el('div',{class:'pm-stats'},
        ...[['Projects',projects.length],['Active',active.length],['Remaining workload',fmt(totalMinutes)],['Completed',complete.length]].map(([label,value])=>
          el('div',{class:'pm-stat'},el('span',{class:'muted small'},label),el('strong',{},String(value))))));
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
          const card=el('button',{class:'pm-project',type:'button',onclick:()=>detail(p.id)},
            el('div',{class:'pm-project-head'},el('h3',{},p.name),el('span',{class:'chip'},progress+'%')),
            text(p.notes?.slice(0,200)||'Add goals, context, and supporting work files.'),
            el('div',{class:'pm-progress'},el('i',{style:{width:progress+'%'}})),
            el('div',{class:'pm-project-foot'},el('span',{},p.open_task_count+' tasks open'),
              el('span',{},fmt(p.remaining_minutes||0)+' remaining'),
              el('span',{},formatDate(p.deadline))),
            el('span',{class:'pm-arrow'},'↗'));
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
        notice('Project created');await detail(p.id);
      }catch(err){showError(err);}
    }

    async function detail(id){
      selected=id; reset();
      let p;
      try {p=await api('/project-manager/projects/'+encodeURIComponent(id));}
      catch(err){screen.append(text(errorDetail(err).message));return;}
      const back=btn('← All projects',dashboard);
      const header=el('div',{class:'pm-header'},el('div',{},back,el('h2',{},p.name),text('Project workspace - update scope, progress, work files and schedule')),
          btn('Delete project',async()=>{
            if(!(await confirmDialog({title:'Delete project?',body:'Project documents and links will be removed. Existing workload tasks will be retained as unassigned tasks.',confirmLabel:'Delete project',danger:true})))return;
            try{await api('/project-manager/projects/'+encodeURIComponent(id),{method:'DELETE'});notice('Project removed');dashboard();}catch(err){showError(err);}
          }));
      screen.append(header);
      const col=el('div',{class:'pm-columns'}),left=el('div',{class:'stack'}),right=el('div',{class:'stack'});
      col.append(left,right);screen.append(col);
      const detailsForm=el('form',{class:'card stack'});
      const projectName=inp('name',p.name,'text',{required:true,maxlength:160});
      const notes=el('textarea',{class:'input',name:'notes',rows:5},p.notes||'');
      detailsForm.append(el('div',{class:'row-between'},el('h3',{},'Project details'),el('span',{class:'chip'},'Saved locally')),
        field('name','Project name',projectName),field('notes','Overview, milestones and notes',notes));
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
      };left.append(detailsForm);

      // Workload tasks are the same source of truth used by PODA's chat tools.
      const tasks=el('div',{class:'card stack'},el('div',{class:'row-between'},el('h3',{},'Work items'),el('span',{class:'chip'},String(p.task_count))));
      const list=el('div',{class:'stack-sm'});
      if(!p.tasks?.length)list.append(text('No tasks yet. Add a work item or import a document.'));
      for(const task of p.tasks||[]){
        list.append(el('div',{class:'pm-task'},
          el('div',{},el('strong',{},task.title),text([task.category, task.status, formatDate(task.deadline)].join(' · ')),
            text(fmt(task.estimate?.remaining_minutes||0)+' remaining · '+(task.estimate?.source||'uncalibrated'))),
          btn('Edit',()=>editTask(task))));
      }
      const taskForm=el('form',{class:'pm-task-form'});
      const title=inp('title','','text',{required:true,placeholder:'New work item (e.g. solve questions 1–6)'});
      const minutes=inp('user_minutes','','number',{min:1,placeholder:'Your time estimate (minutes)'});
      const taskCategory=el('select',{class:'input',name:'category'},...categories.map(t=>el('option',{value:t},t)));
      const taskDue=inp('deadline','','date');
      const taskDifficulty=inp('difficulty',5,'number',{min:1,max:10});
      const taskPriority=inp('importance',5,'number',{min:1,max:10});
      taskForm.append(title,minutes,taskCategory,taskDue,
        el('div',{class:'grid-2'},field('difficulty','Difficulty',taskDifficulty),field('importance','Priority',taskPriority)),
        el('button',{class:'btn btn-primary',type:'submit'},'Add work item'));
      taskForm.onsubmit=async e=>{
        e.preventDefault();
        try{
          await api('/workload',{method:'POST',body:{
            title:title.value,project:p.name,user_minutes:minutes.value?+minutes.value:null,
            difficulty:+taskDifficulty.value,importance:+taskPriority.value,
            deadline:taskDue.value||null,category:taskCategory.value}});
          notice('Task added');detail(id);
        }catch(err){showError(err);}
      };
      tasks.append(list,el('h4',{},'Add work'),taskForm);left.append(tasks);

      async function editTask(task){
        const name=await promptDialog({title:'Edit task',label:'Task name',value:task.title});
        if(name===null)return;
        const spent=await promptDialog({title:'Adjust workload estimate',label:'Your estimate (minutes; blank uses calibrated prediction)',
          value:String(task.user_minutes||'')});
        if(spent===null)return;
        const done=await promptDialog({title:'Progress',label:'Completed % (0–100)',value:String(task.progress||0)});
        if(done===null)return;
        const actual=await promptDialog({title:'Actual time spent',label:'Minutes actually spent (optional)',value:String(task.actual_minutes||'')});
        if(actual===null)return;
        try{
          await api('/workload/'+encodeURIComponent(task.id),{method:'PUT',body:{...task,title:name||task.title,
            user_minutes:spent?+spent:null,actual_minutes:actual?+actual:null,
            progress:Math.max(0,Math.min(100,+done||0))}});
          notice('Task updated and pacing feedback recorded');detail(id);
        }catch(err){showError(err);}
      }

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
      right.append(docs);

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
      right.append(schedule);
    }
    await dashboard();
  });
})();
