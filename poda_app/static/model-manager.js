/* First-party local Ollama catalog and profile management. */
(function(){
  'use strict';
  const {el,api,toast,confirmDialog,errorDetail}=window.PODA;
  const note=s=>el('p',{class:'small muted'},s);
  const btn=(name,fn)=>el('button',{type:'button',class:'btn btn-sm',onclick:fn},name);
  async function mount(target){
    target.replaceChildren(note('Loading installed Ollama models and current Qwen quantization options...'));
    let data;
    try{data=await api('/system/model-manager/catalog');}
    catch(err){target.replaceChildren(note(errorDetail(err).message));return;}
    const installed=data.installed||[];
    const title=el('h3',{},'Choose local models for each PODA profile');
    const reg=el('div',{class:'stack-sm'},title,
      note('PODA can use any Ollama model installed on your Mac, including custom or imported models. Select one for each chat profile. Embedding models are excluded.'));
    const chatModels=installed.filter(m=>!/embed|nomic-/i.test(m));
    for(const profile of ['fast','balanced','deep']){
      const chosen=data.selected?.[profile]||'';
      const select=el('select',{class:'input','aria-label':profile+' model'},
        el('option',{value:''},'Auto (based on installed models)'),
        ...chatModels.map(m=>el('option',{value:m,selected:chosen===m},m)));
      const save=btn('Use this model',async()=>{
        if(!select.value){toast('Choose an installed model','danger');return;}
        try{await api('/system/model-manager/profile',{method:'PUT',body:{profile,model:select.value}});
          toast(profile+' profile updated. New replies use '+select.value,'ok');}
        catch(err){toast(errorDetail(err).message,'danger');}
      });
      reg.append(el('div',{class:'model-profile-row'},el('strong',{},profile),
        select,save,btn('Install model...',()=>installPrompt(profile))));
    }
    const catalog=el('div',{class:'stack-sm'},el('h3',{},'Qwen quantization choices'),
      note('Available Qwen 3.6 / 3.5 tags. RAM estimates reserve about 6 GB for the OS plus KV cache; they are guides, not guaranteed performance. A Q4 model is smaller; Q8 and BF16 generally require more memory.'));
    const selectQuant=el('select',{class:'input','aria-label':'Qwen model variant'});
    const options=data.recommendations||[];
    options.sort((a,b)=>a.disk_gb-b.disk_gb);
    for(const c of options){
      const label=c.tag+' · '+c.disk_gb+' GB download · '+c.verdict+(c.installed?' · installed':'');
      selectQuant.append(el('option',{value:c.tag},label));
    }
    async function openInstall(tag){
      if(!(await confirmDialog({title:'Open Ollama installation in Terminal?',body:'Install '+tag+' using a local Terminal window and Ollama. This downloads model weights onto your Mac and can use significant disk space. PODA will not install anything without your click.',confirmLabel:'Open Terminal'})))return;
      try{
        const x=await api('/system/model-manager/install-terminal',{method:'POST',body:{tag}});
        toast(x.command+' opened in Terminal. Wait for Ollama to finish, then refresh.','ok',10000);
      }catch(err){toast(errorDetail(err).message,'danger');}
    }
    async function installPrompt(profile){
      const tag=window.prompt('Enter an Ollama model tag to install for '+profile+' (example: qwen3.6:27b-q4_K_M)');
      if(tag?.trim())await openInstall(tag.trim());
    }
    catalog.append(el('div',{class:'model-install-row'},selectQuant,
      btn('Install selected quantization in Terminal',()=>openInstall(selectQuant.value)),
      btn('Refresh installed models',()=>mount(target))));
    target.replaceChildren(reg,catalog);
  }
  window.PODAModelManager={mount};
})();
