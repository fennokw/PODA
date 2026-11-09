/* First-party local Ollama catalog and profile management. */
(function(){
 'use strict';
 const {el,api,toast,confirmDialog,errorDetail}=window.PODA;
 const note=s=>el('p',{class:'small muted'},s);
 const btn=(name,fn)=>el('button',{type:'button',class:'btn btn-sm',onclick:fn},name);
 async function mount(target){
  target.replaceChildren(note('Checking installed Ollama models and actual RAM / VRAM / disk capacity...'));
  let data;try{data=await api('/system/model-manager/catalog');}catch(e){target.replaceChildren(note(errorDetail(e).message));return;}
  const section=el('div',{class:'stack-sm'},el('h3',{},'Fully local Ollama model comparison'));
  const h=data.hardware||{};
  section.append(note('Measured capacity · RAM '+(h.ram_total_gb??'unavailable')+' GB · '+(h.gpu_shared_with_ram?
    'GPU shares unified memory (not additional VRAM)':('Dedicated VRAM '+(h.gpu_dedicated_vram_gb??'not measurable')+' GB'))+
    ' · disk free '+(h.disk_free_gb??'unavailable')+' GB. '+(data.disclaimer||'')));
  const roles=['all','general','coding','vision'];
  const filter=el('select',{class:'input','aria-label':'Model type'},...roles.map(v=>el('option',{value:v},v==='all'?'All model types':v)));
  const results=el('div',{class:'stack-sm'});
  const openInstall=async tag=>{
    if(!(await confirmDialog({title:'Download local Ollama weights?',body:'Install '+tag+
      ' on this Mac. This downloads weights but PODA does not enable cloud inference. Confirm exact model license and disk usage.',confirmLabel:'Open Terminal'})))return;
    try{const r=await api('/system/model-manager/install-terminal',{method:'POST',body:{tag}});
      toast(r.command+' opened in Terminal; refresh after completion.','ok',9000);
    }catch(e){toast(errorDetail(e).message,'danger');}
  };
  const refresh=()=>{
    results.replaceChildren();
    for(const f of (data.families||[])){
      if(filter.value!=='all'&&filter.value!==f.role)continue;
      const card=el('div',{class:'card stack-sm'},el('strong',{},f.family+' · '+f.role),
        note(f.notes),note(f.verdict+' · Suggested quantization: '+(f.recommended_quant||'none fits')));
      for(const variant of f.variants||[]){
        card.append(el('div',{class:'row'},note(variant.tag+' · '+variant.quant+' · '+variant.disk_gb+
          ' GB disk · est. '+variant.estimated_resident_gb+' GB resident · '+variant.status+
          (variant.installed?' · installed':'')),
        btn('Install',()=>openInstall(variant.tag))));
      }
      results.append(card);
    }
  };
  filter.onchange=refresh;refresh();
  section.append(filter,results);
  const profiles=el('div',{class:'stack-sm'},el('h3',{},'Installed models for PODA profiles'),
    note('Only locally installed tags can be assigned. Install weights first, then select a profile.'));
  const installed=(data.installed||[]).filter(x=>!/embed|nomic-|cloud/i.test(x));
  for(const p of ['fast','balanced','deep']){
    const select=el('select',{class:'input'},el('option',{value:''},'Choose installed model'),
      ...installed.map(tag=>el('option',{value:tag},tag)));
    select.value=data.selected?.[p]||'';
    profiles.append(el('div',{class:'model-profile-row'},el('strong',{},p),select,
      btn('Use model',async()=>{if(!select.value)return toast('Select a model','danger');
        try{await api('/system/model-manager/profile',{method:'PUT',body:{profile:p,model:select.value}});toast('Profile updated','ok');}
        catch(e){toast(errorDetail(e).message,'danger');}})));
  }
  profiles.append(btn('Refresh local inventory',()=>mount(target)));
  target.replaceChildren(profiles,section);
 }
 window.PODAModelManager={mount};
})();