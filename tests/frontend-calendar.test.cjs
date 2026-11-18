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
      kind.value=task?'Personal project':'Meeting';form.querySelector('[type="checkbox"]').checked=true;
      form.querySelector('button').click();await settle();
      const body=JSON.parse(calls.at(-1)[1].body);
      assert.equal(body.completed,true);assert.equal(body[task?'task_kind':'event_kind'],kind.value);
      assert.equal(body[task?'event_kind':'task_kind'],undefined);
    }finally{dom.window.close();}
  }
});
