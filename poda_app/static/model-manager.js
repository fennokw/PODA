/* PODA-owned GGUF inventory and llama.cpp controls. */
(() => {
  'use strict';
  const { el, api, toast, errorDetail, fmtBytes } = window.PODA;
  const path = '/system/model-manager';
  const label = {
    fast: 'Fast', balanced: 'Balanced', deep: 'Deep', vision: 'Visual',
    fast_coding: 'Fast coding', deep_coding: 'Deep coding',
  };
  const note = (text) => el('p', { class: 'small muted' }, text);
  const button = (text, onclick) => el('button', { type: 'button', class: 'btn btn-sm', onclick }, text);
  const option = (value, text = value) => el('option', { value }, text);
  const field = (text, input) => el('label', { class: 'field stack-sm' }, el('span', { class: 'label' }, text), input);

  async function mount(target) {
    target.replaceChildren(note('Checking local models and hardware…'));
    let data;
    try { data = await api(path + '/catalog'); }
    catch (error) { target.replaceChildren(note(errorDetail(error).message)); return; }
    const progress = el('p', { class: 'small', role: 'status' });
    const section = el('div', { class: 'stack' }, progress);
    const hardware = data.hardware || {};
    const memory = hardware.unified_memory_gb == null
      ? `${hardware.ram_total_gb ?? 'Unknown'} GB RAM · ${hardware.gpu_memory_dedicated_gb ?? 'Unknown'} GB dedicated VRAM`
      : `${hardware.unified_memory_gb} GB unified memory`;
    const engine = el('div', { class: 'stack-sm' },
      el('strong', {}, data.engine.reachable ? `llama.cpp ${data.engine.version} is installed` : 'Install llama.cpp to start'),
      note(`${memory} · ${hardware.disk_free_gb ?? 'Unknown'} GB free disk`));
    section.append(engine);

    async function track(job) {
      while (target.isConnected) {
        if (job.state === 'completed') {
          await mount(target);
          toast(`${job.label} is ready.`, 'ok');
          return;
        }
        if (job.state === 'failed') { progress.textContent = job.error || 'Installation failed.'; return; }
        progress.textContent = job.total ? `${job.label} · ${fmtBytes(job.bytes)} of ${fmtBytes(job.total)}` : `${job.label} · working…`;
        await new Promise((resolve) => setTimeout(resolve, 850));
        job = await api(path + '/jobs/' + job.id);
      }
    }
    async function install(endpoint, body) {
      try { await track(await api(path + endpoint, { method: 'POST', body })); }
      catch (error) { progress.textContent = errorDetail(error).message; }
    }
    if (!data.engine.reachable) engine.append(button('Install local engine', () => install('/runtime', { backend: 'auto' })));
    if (data.windows && !data.vulkan_available) engine.append(button('Install Windows Vulkan acceleration',
      () => install('/runtime', { backend: 'vulkan' })));

    const roles = el('div', { class: 'stack-sm' }, el('h3', {}, 'Model roles'),
      note('Choose which installed model PODA uses for each kind of request. Automatic uses the best available match for that role.'));
    for (const [profile, title] of Object.entries(label)) {
      const candidates = data.installed.filter((item) => item.role !== 'embedding' &&
        (profile !== 'vision' || item.vision));
      const effective = data.roles[profile]?.model;
      const automatic = data.installed.find((item) => item.id === effective);
      const select = el('select', { class: 'input', 'aria-label': title + ' model' },
        option('', automatic ? `Automatic · ${automatic.label} ${automatic.quant}` : 'Automatic · no matching model'),
        ...candidates.map((item) => option(item.id, `${item.label} · ${item.quant}`)));
      select.value = data.selected[profile] || '';
      if (select.selectedIndex < 0) select.value = '';
      roles.append(el('div', { class: 'model-profile-row' }, el('strong', {}, title), select, button('Save role', async () => {
        try {
          await api(path + '/profile', { method: 'PUT', body: { profile, model: select.value } });
          toast(title + ' model saved.', 'ok');
        } catch (error) { toast(errorDetail(error).message, 'danger'); }
      })));
    }
    section.append(roles);

    const library = el('div', { class: 'stack-sm' }, el('h3', {}, 'Download GGUF models'),
      note('Weight quantization changes the model file. Lower precision saves memory. Download a different variant to compare quality.'));
    const grouped = new Map();
    for (const item of data.catalog) {
      const variants = grouped.get(item.label) || [];
      variants.push(item);
      grouped.set(item.label, variants);
    }
    for (const [family, variants] of grouped) {
      const select = el('select', { class: 'input', 'aria-label': family + ' quantization' },
        ...variants.map((item) => option(item.id, `${item.quant} · ${fmtBytes(item.bytes)}${item.installed ? ' · installed' : ''}`)));
      const download = button('Download', () => install('/download', { model: select.value }));
      const fit = note('');
      function state() {
        const variant = variants.find((item) => item.id === select.value);
        download.disabled = Boolean(variant?.installed);
        fit.textContent = variant?.fit || '';
        download.textContent = download.disabled ? 'Installed' : 'Download';
      }
      select.onchange = state;
      state();
      library.append(el('div', { class: 'model-profile-row' }, el('div', {}, el('strong', {}, family), fit), select, download));
    }
    section.append(library);

    const localPath = el('input', { class: 'input', placeholder: 'Absolute path to model.gguf' });
    const projector = el('input', { class: 'input', placeholder: 'Absolute path to matching mmproj.gguf' });
    const thinking = el('input', { type: 'checkbox' });
    const tools = el('input', { type: 'checkbox' });
    section.append(el('details', { class: 'stack-sm' }, el('summary', {}, 'Import a GGUF you already have'),
      note('PODA copies the model into its own inventory. A visual model also needs the matching projector.'),
      field('Model file', localPath), field('Visual projector (optional)', projector),
      el('label', { class: 'check' }, thinking, 'This model supports thinking'),
      el('label', { class: 'check' }, tools, 'This model supports function calling'),
      button('Import GGUF', () => {
        if (!localPath.value.trim()) { toast('Enter the full path to your GGUF file.', 'warn'); return; }
        install('/import', { path: localPath.value.trim(), projector: projector.value.trim() || null,
          thinking: thinking.checked, tools: tools.checked });
      })));

    const controls = {};
    function selectSetting(key, name, options) {
      const input = el('select', { class: 'input' }, ...options.map((value) =>
        Array.isArray(value) ? option(...value) : option(value)));
      input.value = data.settings[key];
      controls[key] = input;
      return field(name, input);
    }
    function numberSetting(key, name, min, max) {
      const input = el('input', { class: 'input', type: 'number', min, max, value: data.settings[key] });
      controls[key] = input;
      return field(name, input);
    }
    section.append(el('details', { class: 'stack-sm' }, el('summary', {}, 'Advanced llama.cpp settings'),
      note('K and V cache types change memory use at runtime. Speculation is applied to compatible text models. Image and embedding workers use their own settings.'),
      selectSetting('backend', 'Compute backend', [['auto', 'Automatic'], ['cpu', 'CPU only'],
        ...(data.windows ? [['vulkan', 'Windows Vulkan']] : [])]),
      selectSetting('speculation', 'Speculative decoding', [['ngram-simple', 'N-gram lookup (default)'],
        ['ngram-cache', 'N-gram cache'], ['none', 'Off']]),
      selectSetting('cache_k', 'K cache precision', data.cache_types),
      selectSetting('cache_v', 'V cache precision', data.cache_types),
      selectSetting('flash_attention', 'Flash Attention', ['auto', 'on', 'off']),
      numberSetting('context_limit', 'Context token limit', 2048, 131072),
      numberSetting('gpu_layers', 'GPU layers (−1 for all)', -1, 256),
      button('Save engine settings', async () => {
        const settings = Object.fromEntries(Object.entries(controls).map(([key, input]) =>
          [key, input.type === 'number' ? input.valueAsNumber : input.value]));
        try {
          await api(path + '/settings', { method: 'PUT', body: settings });
          toast('Engine settings saved for the next response.', 'ok');
        } catch (error) { toast(errorDetail(error).message, 'danger'); }
      })));
    section.append(button('Refresh local inventory', () => mount(target)));
    target.replaceChildren(section);
    if (data.active_job) track(data.active_job).catch((error) => { progress.textContent = errorDetail(error).message; });
  }
  window.PODAModelManager = { mount };
})();
