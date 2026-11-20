// Run with NODE_PATH pointing at an installation of jsdom (no app dependency).
'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const root=path.join(__dirname,'../poda_app/static');
function create(){
  const dom=new JSDOM(fs.readFileSync(path.join(root,'index.html'),'utf8'),{url:'http://127.0.0.1:8799/#/notion',runScripts:'outside-only'});
  const w=dom.window;
  w.AbortSignal=AbortSignal;
  const calls=[];
  w.fetch=async(url,opts={})=>{calls.push([url,opts]);return {ok:true,status:200,text:async()=>JSON.stringify({}),json:async()=>({})};};
  w.eval(fs.readFileSync(path.join(root,'ui.js'),'utf8'));
  w.eval(fs.readFileSync(path.join(root,'calendar-setup.js'),'utf8'));
  return {dom,w,calls};
}
const settle=()=>new Promise(resolve=>setTimeout(resolve,20));
test('calendar choices and local add save a real event then show defaults',async()=>{
  const {dom,w,calls}=create();
  try{
    const setup=w.PODACalendar.setup({config:{provider:'local'},google:{connected:false}});
    w.document.body.append(setup.element);
    assert.equal(w.document.querySelector('[aria-label="Calendar provider"]').options.length,3);
    let refreshed=false,review='';
    const form=w.PODACalendar.newEvent(()=>refreshed=true,text=>review=text);w.document.body.append(form);
    form.querySelector('input').value='Lesson';
    form.querySelector('[type="datetime-local"]').value='2027-02-05T14:00';
    w.fetch=async(url,opts)=>{calls.push([url,opts]);return {ok:true,status:200,text:async()=>JSON.stringify({result:{review_question:'Temporary area: Personal. Change it?'}})};};
    form.querySelector('button').click();await settle();
    const [url,opts]=calls.at(-1);assert.equal(url,'/calendar/events');assert.equal(opts.method,'POST');
    assert.equal(JSON.parse(opts.body).title,'Lesson');assert.ok(refreshed);assert.match(review,/Temporary area/);
  }finally{dom.window.close();}
});
test('Notion defaults use schema choices; Google sign-in creates external link',async()=>{
  const {dom,w}=create();
  try{
    w.fetch=async url=>({ok:true,status:200,text:async()=>JSON.stringify(url.endsWith('/connect')?{url:'https://accounts.google.com/test'}:{date_property:'Date',values:{},suggested:{area:'Personal'},fields:[{key:'area',name:'Area',options:['Personal','Music']}],missing:[]})});
    const setup=w.PODACalendar.setup({config:{provider:'notion'},google:{connected:false}});
    w.document.body.append(setup.element,setup.defaults);await setup.drawDefaults();
    assert.equal(setup.defaults.querySelector('select').options.length,3);
    const choice=setup.element.querySelector('select');choice.value='google';choice.dispatchEvent(new w.Event('change'));
    [...setup.element.querySelectorAll('button')].find(b=>b.textContent==='Sign in with Google').click();await settle();
    assert.ok(setup.element.querySelector('a[href="https://accounts.google.com/test"]'));
  }finally{dom.window.close();}
});
test('a late route cannot overwrite the new page or its title',async()=>{
  const {dom,w}=create();
  try{
    let finish;const gate=new Promise(resolve=>finish=resolve);
    w.PODA.route('slow',async({view,setTitle})=>{await gate;view.append('Old');setTitle('Old title');});
    w.PODA.route('fast',async({view,setTitle})=>{view.append('Current');setTitle('Current title');});
    w.location.hash='#/slow';const first=w.PODA.renderRoute();
    w.location.hash='#/fast';await w.PODA.renderRoute();finish();await first;
    assert.equal(w.document.querySelector('#view').textContent,'Current');
    assert.equal(w.document.querySelector('#viewTitle').textContent,'Current title');
  }finally{dom.window.close();}
});
test('session recovery retries reads only, never writes',async()=>{
  const {dom,w}=create();
  try{
    const calls=[];let count=0;
    w.fetch=async(url)=>{calls.push(url);const ok=url==='/'||++count>1;return {ok,status:ok?200:401,text:async()=>JSON.stringify({ok})};};
    await w.PODA.api('/read');assert.deepEqual(calls,['/read','/','/read']);
    calls.length=0;count=0;
    await assert.rejects(w.PODA.api('/write',{method:'POST',body:{}}));assert.deepEqual(calls,['/write']);
  }finally{dom.window.close();}
});
test('bounded reads expose a useful timeout',async()=>{
  const {dom,w}=create();
  try{
    w.fetch=(_url,{signal})=>new Promise((_resolve,reject)=>signal.addEventListener('abort',()=>reject(signal.reason),{once:true}));
    await assert.rejects(w.PODA.api('/stalled',{timeoutMs:5}),/too long to load/);
  }finally{dom.window.close();}
});
test('full Calendar route boots for all three providers',async()=>{
  for(const provider of ['local','notion','google']){
    const {dom,w}=create();
    try{
      const cap={connected:true,can_read_content:true,can_insert_content:true,can_update_content:true,mapping_active:true,database_title:'Test calendar'};
      w.fetch=async url=>{
        const data=url==='/calendar/status'?{config:{provider,allow_write:true},capability:cap,google:{connected:false}}
          :url.startsWith('/calendar/events')?{events:[],count:0}
          :url==='/notion/status'?{status:'DISCONNECTED',capability:{}}
          :url==='/calendar/notion/defaults'?{fields:[],missing:[],values:{},suggested:{}}
          :url==='/system/build'?{build_id:'test',version:'test'}:{};
        return {ok:true,status:200,text:async()=>JSON.stringify(data),json:async()=>data};
      };
      w.eval(fs.readFileSync(path.join(root,'app.js'),'utf8'));await settle();await settle();
      assert.ok(w.document.querySelector('#view [aria-label="Calendar provider"]'),provider);
      assert.doesNotMatch(w.document.querySelector('#view').textContent,/is not defined|Cannot read/);
      if(provider!=='notion')assert.match(w.document.querySelector('#view').textContent,/Add event/);
    }finally{dom.window.close();}
  }
});
test('Memory releases listeners and resumes its draft after context loss',()=>{
  const {dom,w}=create();
  try{
    w.document.body.append(w.PODA.el('div',{id:'knowledgeExplorer'}));
    w.PODAKnowledge={explorer:()=>w.document.createElement('div')};
    w.matchMedia=()=>({matches:false});
    w.Worker=class{terminate(){this.terminated=true;}};
    w.WebGLBuffer=class{};w.WebGLProgram=class{};
    w.requestAnimationFrame=()=>1;w.cancelAnimationFrame=()=>{};
    let hidden=false;Object.defineProperty(w.document,'hidden',{get:()=>hidden});
    const src=fs.readFileSync(path.join(root,'memory.js'),'utf8').split('  let loadGeneration = 0;')[0];
    w.eval(src+' let memoryDraftDirty=true; function setStatus(){} window.TestMemory=Open3DMemoryWeb;})();');
    const C=w.TestMemory;C.prototype.initGL=function(){this.glInitializations=(this.glInitializations||0)+1;};
    C.prototype.resize=function(){};C.prototype.render=function(){};
    const canvas=w.document.createElement('canvas'),labels=w.document.createElement('canvas');
    canvas.getContext=()=>({deleteBuffer(){},deleteProgram(){}});labels.getContext=()=>({});
    const node={id:'draft',node_type:'memory',x:10,y:20,z:30};
    const scene=new C(canvas,labels,{nodes:[node]},()=>{},()=>{});const worker=scene.worker;
    canvas.dispatchEvent(new w.Event('webglcontextlost',{cancelable:true}));
    assert.ok(scene.contextLost&&worker.terminated);
    canvas.dispatchEvent(new w.Event('webglcontextrestored'));
    assert.equal(scene.glInitializations,2);assert.equal(scene.nodes[0],node);assert.equal(scene.contextLost,false);
    hidden=true;w.document.dispatchEvent(new w.Event('visibilitychange'));assert.equal(scene.paused,true);
    hidden=false;w.document.dispatchEvent(new w.Event('visibilitychange'));assert.equal(scene.paused,false);
    scene.destroy();scene.destroy();canvas.dispatchEvent(new w.Event('webglcontextrestored'));
    assert.equal(scene.glInitializations,2);assert.ok(scene.destroyed);assert.equal(scene.worker,null);
  }finally{dom.window.close();}
});
test('leaving Calendar during setup cannot change the next page breadcrumb',async()=>{
  const {dom,w}=create();
  try{
    let complete;
    w.fetch=async url=>{
      if(url==='/calendar/status')await new Promise(resolve=>complete=resolve);
      const data=url==='/calendar/status'?{config:{provider:'local'},capability:{},google:{connected:false}}:{};
      return {ok:true,status:200,text:async()=>JSON.stringify(data),json:async()=>data};
    };
    w.eval(fs.readFileSync(path.join(root,'app.js'),'utf8'));await settle();
    w.PODA.route('destination',({view})=>{view.append('Next page');w.document.querySelector('#viewCrumb').textContent='Next breadcrumb';});
    w.location.hash='#/destination';await w.PODA.renderRoute();complete();await settle();
    assert.equal(w.document.querySelector('#viewCrumb').textContent,'Next breadcrumb');
    assert.equal(w.document.querySelector('#view').textContent,'Next page');
  }finally{dom.window.close();}
});
test('Notion editor offers only the matching kind and saves completion independently',async()=>{
  for(const task of [true,false]){
    const {dom,w,calls}=create();
    try{
      const ev={page_id:'example',title:'Example',start:'2026-11-20',type:task?'Task':'Event',is_deadline:task,done:false,area:[],setup_warnings:['Clear the mismatched field.']};
      const form=w.PODACalendar.editor(ev,'notion',()=>{},{kind_options:{task_kind:['Homework','Personal project'],event_kind:['Meeting']}});
      w.document.body.append(form);
      const kind=form.querySelector('select');assert.equal(kind.getAttribute('aria-label'),task?'Task kind':'Event kind');
      assert.equal(form.querySelectorAll('select').length,1);
      if(task)assert.doesNotMatch(form.textContent,/commitment/);
      kind.value=task?'Personal project':'Meeting';
      const completed=form.querySelector('[type="checkbox"]');
      if(task){assert.ok(completed);completed.checked=true;}else assert.equal(completed,null);
      form.querySelector('button').click();await settle();
      const body=JSON.parse(calls.at(-1)[1].body);
      assert.equal(body.completed,task?true:undefined);assert.equal(body[task?'task_kind':'event_kind'],kind.value);
      assert.equal(body[task?'event_kind':'task_kind'],undefined);
    }finally{dom.window.close();}
  }
});
test('project task folders show progress, reopen chats, and save a new due time',async()=>{
  const {dom,w}=create();
  try{
    const projectPath='/project-manager/projects/demo';
    const task={id:'task-one',title:'Prepare prototype',progress:25,estimate:{minutes:60,remaining_minutes:45},
      deadline:'2027-04-02',tracking:{goal:'Samples',goal_target:4,goal_current:1},
      chats:[{session_id:'saved-chat',title:'Prototype plan'}]};
    const project={id:'demo',name:'Prototype',tasks:[task],chats:[{...task.chats[0],task_id:task.id}],attachments:[]};
    let patch;
    w.fetch=async(url,options)=>{
      if(options?.method==='PATCH')patch=JSON.parse(options.body);
      const data=url===projectPath?project:task;
      return {ok:true,status:200,text:async()=>JSON.stringify(data)};
    };
    w.eval(fs.readFileSync(path.join(root,'project-manager.js'),'utf8'));
    w.location.hash='#/projects?project=demo&task=task-one';
    await w.PODA.renderRoute();
    const screen=w.document.querySelector('.pm-screen');
    assert.equal(screen.querySelector('.pm-task-rail progress').value,25);
    const taskView=screen.querySelector('.pm-task-content');
    assert.equal(taskView.querySelector('progress').value,25);
    assert.match(taskView.querySelector('a[href="#/ask?session=saved-chat"]').textContent,/Prototype plan/);
    taskView.querySelector('[name="due_time"]').value='15:30';
    taskView.querySelector('[name="user_minutes"]').value='90';
    taskView.querySelector('form').requestSubmit();
    await settle();
    assert.equal(patch.user_minutes,90);
    assert.equal(patch.deadline,new w.Date('2027-04-02T15:30').toISOString());
    assert.equal(patch.progress,undefined);
  }finally{dom.window.close();}
});
test('slash menu completes one-word modes and @ mentions without losing drafts', async () => {
  const {dom,w,calls}=create();
  try {
    let session;
    w.fetch=async url=>{
      calls.push(url);
      const data=url==='/chat/plugins' ? {plugins:[{name:'github',label:'GitHub'}]} :
        url.includes('session_id=task-chat') ? {task_name:'Review',goal_target:5} : {};
      return {ok:true,status:200,text:async()=>JSON.stringify(data)};
    };
    w.eval(fs.readFileSync(path.join(root,'chat-commands.js'),'utf8'));
    const area=w.document.createElement('textarea');
    const menu=w.PODAChatCommands.mount(area,()=>session);
    w.document.body.append(menu.field,menu.trigger,menu.pluginTrigger);
    let enters=0;
    area.addEventListener('keydown',event=>{
      if (!menu.handleKey(event) && event.key==='Enter') enters++;
    });
    const panel=menu.field.querySelector('.chat-slash-panel');
    const type=value=>{
      area.focus();area.value=value;area.setSelectionRange(value.length,value.length);
      area.dispatchEvent(new w.Event('input',{bubbles:true}));
    };
    const press=key=>area.dispatchEvent(new w.KeyboardEvent('keydown',{key,bubbles:true,cancelable:true}));
    type('/');
    await settle();
    assert.equal(panel.hidden,false);
    assert.match(panel.textContent,/\/teachoff/);
    assert.doesNotMatch(panel.textContent,/\/(?:learn|act|work|project)|taskdone/);
    type('/teachoff');press('Enter');assert.equal(enters,1);
    type('Explain the equation');menu.trigger.click();
    panel.querySelector('[role="option"]').click();
    assert.equal(area.value,'/teach Explain the equation');
    type('/research @gi');await settle();press('Tab');
    assert.equal(area.value,'/research @github ');
    type('Use @gi');await settle();press('Tab');
    assert.equal(area.value,'Use @github ');
    session='task-chat';menu.reset();
    type('/task');await settle();
    assert.match(panel.textContent,/taskdone/);
    assert.match(panel.textContent,/taskprogress/);
    assert.ok(calls.includes('/project-manager/chat-commands?session_id=task-chat'));
    type('/');press('Escape');assert.equal(panel.hidden,true);
    type('https://example.com/path');assert.equal(panel.hidden,true);
  } finally {dom.window.close();}
});

test('Projects exposes task folders and files an existing chat without a command',async()=>{
  const {dom,w}=create();
  try{
    const projectPath='/project-manager/projects/demo';
    const task={id:'task-one',title:'Prototype',progress:50,estimate:{minutes:60,remaining_minutes:30},
      deadline:'2027-04-02',tracking:{scheduled:true},chats:[]};
    const project={id:'demo',name:'Workshop',tasks:[task],open_task_count:1,chats:[],attachments:[]};
    const saved={session_id:'saved-chat',title:'Sketch options',project_name:'Other project'};
    let filed, eventLink;
    w.fetch=async(url,opts={})=>{
      let data={};
      if(url==='/project-manager/projects')data={projects:[project]};
      if(url===projectPath)data=project;
      if(url.startsWith('/project-manager/chats?'))data={chats:[saved]};
      if(url.startsWith('/project-manager/calendar-events?'))data={scope:{provider:'local'},events:[{page_id:'event',title:'Review',start:'2027-04-03'}]};
      if(url===projectPath+'/tasks/task-one'){
        if(opts.method==='PATCH')eventLink=JSON.parse(opts.body);
        data=task;
      }
      if(url===projectPath+'/chats'&&opts.method==='POST'){
        filed=JSON.parse(opts.body);
        task.chats=[saved];
      }
      return {ok:true,status:200,text:async()=>JSON.stringify(data)};
    };
    const route=async hash=>{
      w.history.replaceState(null,'',hash);
      await w.PODA.renderRoute();
    };
    w.eval(fs.readFileSync(path.join(root,'project-manager.js'),'utf8'));
    await route('#/projects');
    const card=w.document.querySelector('.pm-project');
    assert.equal(card.querySelector('a[aria-label="Open project Workshop"]').hash,'#/projects?project=demo');
    const taskURL=card.querySelector('.pm-card-tasks a').hash;
    assert.equal(taskURL,'#/projects?project=demo&task=task-one');
    await route('#/projects?project=demo');
    assert.match(w.document.querySelector('.pm-task-rail').textContent,/Task folders.*Prototype/);
    assert.match(w.document.querySelector('.pm-task-content').textContent,/Project chats/);
    await route(taskURL);
    const taskView=w.document.querySelector('.pm-task-content');
    assert.equal(taskView.querySelector('a[href^="#/notion?day="]').hash,'#/notion?day=2027-04-02');
    const byLabel=(root, label)=>[...root.querySelectorAll('button')].find(button=>button.textContent===label);
    byLabel(taskView,'Find events').click();await settle();
    byLabel(taskView,'Use event start as deadline').click();await settle();
    assert.deepEqual(eventLink,{event_id:'event',calendar_scope:{provider:'local'}});
    const picker=w.document.querySelector('.pm-task-content .pm-chat-picker');
    picker.open=true;
    byLabel(picker,'Search').click();await settle();
    byLabel(picker,'Move here').click();await settle();
    assert.deepEqual(filed,{session_id:'saved-chat',task_id:'task-one'});
    assert.match(w.document.querySelector('.pm-task-content a[href="#/ask?session=saved-chat"]').textContent,/Sketch options/);
  }finally{dom.window.close();}
});

test('Chat header turns Work mode on, files the discussion, and renames it', async () => {
  const {dom,w}=create();
  try {
    w.history.replaceState(null,'','#/ask');
    w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
    w.HTMLDialogElement.prototype.close=function(){this.open=false;};
    const initial={session_id:'lesson-chat',title:'New work discussion',folder:null};
    let header=initial, saved=[], renamed;
    w.fetch=async (url,opts={})=>{
      let data={};
      if(url==='/chat/work')data=header;
      if(url==='/chat/sessions')data={sessions:header===initial?[]:[{session_id:header.session_id,label:header.title}]};
      if(url==='/project-manager/projects')data={projects:[{id:'class',name:'Materials'}]};
      if(url==='/project-manager/projects/class')data={tasks:[{id:'hw',title:'Homework 9'}]};
      if(url.endsWith('/lesson-chat/filing')){
        const payload=JSON.parse(opts.body);saved.push(payload);
        header={...header,folder:payload};data=header;
      }
      if(url.endsWith('/lesson-chat/title')){
        renamed=JSON.parse(opts.body).title;header={...header,title:renamed};data={title:renamed};
      }
      return {ok:true,status:200,text:async()=>JSON.stringify(data)};
    };
    for(const name of ['chat-commands.js','app.js']) w.eval(fs.readFileSync(path.join(root,name),'utf8'));
    await settle();
    const area=w.document.querySelector('.composer textarea');
    const toolbar=w.document.querySelector('.chat-work-toolbar');
    const [work]=toolbar.querySelectorAll('button');
    work.click();await settle();
    assert.equal(work.getAttribute('aria-pressed'),'true');
    const project=toolbar.querySelector('[aria-label="Project"]');
    project.value='class';project.dispatchEvent(new w.Event('change'));await settle();
    const task=toolbar.querySelector('[aria-label="Task"]');
    task.value='hw';task.dispatchEvent(new w.Event('change'));await settle();
    assert.deepEqual(saved,[{project_id:'class',task_id:null},{project_id:'class',task_id:'hw'}]);
    [...toolbar.querySelectorAll('button')].find(b=>b.textContent==='Rename').click();
    const dialog=w.document.querySelector('dialog');
    dialog.querySelector('input').value='Homework 9 walkthrough';
    [...dialog.querySelectorAll('button')].find(b=>b.textContent==='Save').click();
    await settle();
    assert.equal(renamed,'Homework 9 walkthrough');
    assert.equal(w.document.querySelector('#viewTitle').textContent,renamed);
    assert.match(w.document.querySelector('#workDiscussionLinks').textContent,/Homework 9 walkthrough/);
    work.click();await settle();assert.equal(work.getAttribute('aria-pressed'),'false');
    assert.equal(w.document.querySelector('#viewTitle').textContent,'Chat');
    assert.equal(w.document.querySelector('#workDiscussionLinks a').textContent,'Homework 9 walkthrough');
    area.focus();area.value='/';area.dispatchEvent(new w.Event('input',{bubbles:true}));await settle();
    assert.match(w.document.querySelector('.composer .chat-slash-panel').textContent,/\/teachoff/);
  }finally{dom.window.close();}
});

test('Practice cards submit through buttons or text and excerpts show the exact source', () => {
  const {dom,w}=create();
  try {
    w.eval(fs.readFileSync(path.join(root,'learning.js'),'utf8'));
    const box=w.document.createElement('main');
    w.document.body.append(box);
    const answers=[];
    const send=(text,ref)=>{answers.push([text,ref]);return true;};
    w.PODALearning.sources([{file:'Homework.pdf',page:2,text:'Problem M-9.1'}],box);
    w.PODALearning.mount([
      {kind:'quiz',question:'Which end is free?',choices:[{label:'Bottom'},{label:'Top'}],correct:null},
      {kind:'question',question:'What is the stress there?'},
      {kind:'plot',title:'Stress',x_label:'x',y_label:'σ',points:[[0,2],[1,0]]},
    ],box,send,null,'discussion','message');
    box.querySelector('.learning-choices button').click();
    const form=box.querySelector('form');
    form.querySelector('input').value='Zero';
    form.dispatchEvent(new w.Event('submit',{cancelable:true}));
    assert.deepEqual(JSON.parse(JSON.stringify(answers)),[
      ['Bottom',{message_id:'message',widget:0,choice:0}],
      ['Zero',{message_id:'message',widget:1,choice:null}],
    ]);
    assert.match(box.querySelector('figure').textContent,/Homework.pdf · page 2.*Problem M-9.1/);
    const slider=box.querySelector('input[type="range"]');
    slider.value=1;slider.dispatchEvent(new w.Event('input'));
    assert.match(box.querySelector('output').textContent,/x = 1 · σ = 0/);
  } finally {dom.window.close();}
});

test('BestNDS matching errors stay beside the preset button',async()=>{
  const {dom,w}=create();
  try{
    w.fetch=async url=>{
      const bad=url==='/notion/mapping/bestnds';
      const data=bad?{detail:{code:'mapping_invalid',message:"The 'Date' property is missing."}}
        :url==='/calendar/status'?{config:{provider:'notion'},capability:{connected:true}}
        :url==='/notion/status'?{capability:{connected:true}}
        :url==='/notion/schema'?{properties:{Name:{type:'title'}}}:{};
      return {ok:!bad,status:bad?400:200,text:async()=>JSON.stringify(data)};
    };
    w.eval(fs.readFileSync(path.join(root,'app.js'),'utf8'));await settle();await settle();
    const preset=[...w.document.querySelectorAll('#view button')].find(b=>b.textContent==="Use Kaden's BestNDS");
    assert.ok(preset);
    preset.click();await settle();
    assert.equal(preset.parentElement.nextElementSibling.textContent,"The 'Date' property is missing.");
  }finally{dom.window.close();}
});
