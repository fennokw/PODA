/* PODA canonical memory viewer. WebGL2 renderer: GL point memories, translucent Open3D sphere clouds, distance-weighted web.
   Two tiers (v0.5.0): surface memories are miniature translucent clouds (distilled core meanings) that hide their in-depth
   question-and-answer members until you zoom in, select, hover, or search; a toggle restores the classic all-points view.
   Draft-until-commit semantics, Open3D-style camera mapping, idle auto-rotation (15 s, 0.0544 rad/s). */
(function () {
  'use strict';
  const { $, $$, el, api, errorDetail, toast, confirmDialog, promptDialog, pill, fmtTime, loadBuild } = window.PODA;
  const REDUCED_MOTION = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const vadd = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
  const vsub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const vmul = (a, s) => [a[0] * s, a[1] * s, a[2] * s];
  const vdot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const vcross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const vlen = (a) => Math.hypot(a[0], a[1], a[2]);
  const vnorm = (a) => { const l = vlen(a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
  const posOf = (n) => [n.x || 0, n.y || 0, n.z || 0];

  function mat4Perspective(fovy, aspect, near, far) {
    const f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
    return new Float32Array([f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, (2 * far * near) * nf, 0]);
  }
  function mat4LookAt(eye, center, up) {
    const z = vnorm(vsub(eye, center)); const x = vnorm(vcross(up, z)); const y = vcross(z, x);
    return new Float32Array([x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0, -vdot(x, eye), -vdot(y, eye), -vdot(z, eye), 1]);
  }
  function mat4Mul(a, b) {
    const o = new Float32Array(16);
    for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
    return o;
  }
  function projectWorld(m, p, w, h) {
    const [x, y, z] = p;
    const cx = m[0] * x + m[4] * y + m[8] * z + m[12], cy = m[1] * x + m[5] * y + m[9] * z + m[13], cz = m[2] * x + m[6] * y + m[10] * z + m[14], cw = m[3] * x + m[7] * y + m[11] * z + m[15];
    if (cw <= 0.0001) return null;
    return { x: (cx / cw * 0.5 + 0.5) * w, y: (1 - (cy / cw * 0.5 + 0.5)) * h, depth: cz / cw };
  }

  const LOD_FULL_GRAPH_MAX = 400;
  const KNN = 12;
  const REVEAL_DISTANCE_FACTOR = 6;   // members appear when the camera is closer than 6 × surface radius
  const REVEAL_SECONDS = 0.24;
  const SURFACE_TINT = [0.60, 0.64, 0.98];
  const SURFACES_KEY = 'poda_surfaces_shown';

  class Open3DMemoryWeb {
    constructor(glCanvas, labelCanvas, sceneData, onSelect, onDirty) {
      this.canvas = glCanvas; this.labels = labelCanvas; this.canvas.tabIndex = 0;
      this.gl = glCanvas.getContext('webgl2', { alpha: false, antialias: true, premultipliedAlpha: false, preserveDrawingBuffer: false });
      if (!this.gl) throw new Error('WebGL2 is required for the Open3D memory renderer.');
      this.labelCtx = labelCanvas.getContext('2d');
      this.nodes = sceneData.nodes || [];
      this.nodeMap = new Map(this.nodes.map((n) => [n.id, n]));
      this.cloudVolume = sceneData.cloud_volume || null;
      this.bounds = sceneData.bounds || { center: [0, 0, 0], diagonal: 400 };
      this.onSelect = onSelect; this.onDirty = onDirty || (() => {});
      this.selectedId = null; this.hoverId = null; this.focusedCloudId = null; this.pointer = null; this.destroyed = false; this.projected = [];
      this.connectionsDirty = true; this.connSeq = 0; this.connPending = false; this.connLastRequest = 0;
      this.density = this.memoryNodes().length <= LOD_FULL_GRAPH_MAX ? 'complete' : 'neighbors';
      this.densityAuto = true;
      this.reveal = new Map(); this.pinnedReveal = null; this.revealSig = '';
      const stored = localStorage.getItem(SURFACES_KEY);
      this.surfacesShown = stored === null ? (sceneData.settings?.surface_show_default ?? true) !== false : stored === '1';
      this.idleRotateDelayMs = 15000;
      this.autoRotateRate = 0.0544; // radians / second
      this.lastInteractionAt = performance.now(); this.lastFrameAt = performance.now(); this.lastAutoStatus = '';
      this.frameTimes = [];
      const distance = Math.max(360, (this.bounds.diagonal || 320) * 1.45);
      const center = [...(this.bounds.center || [0, 0, 0])];
      this.camera = { yaw: -0.66, pitch: 0.25, roll: 0, distance, target: center, fov: 50 * Math.PI / 180, desiredYaw: -0.66, desiredPitch: 0.25, desiredRoll: 0, desiredDistance: distance, desiredTarget: [...center], desiredFov: 50 * Math.PI / 180 };
      this.initWorker();
      this.initGL(); this.bind(); this.resize(); this.animate();
    }

    initWorker() {
      try {
        this.worker = new Worker('/static/memory-worker.js?v=0.6.0');
        this.worker.onmessage = (e) => {
          const d = e.data; if (d.seq !== this.connSeq) return; // stale
          this.uploadConnections(d.pos, d.colors, d.alphas, d.count); this.connPending = false; this.edgeCount = d.edges;
          this.updateLodBadge();
          if (this.connectionsDirty) this.requestConnections();
        };
        this.worker.onerror = () => { this.worker = null; this.connectionsDirty = true; };
      } catch { this.worker = null; }
    }

    compile(vs, fs) {
      const gl = this.gl;
      const sh = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s)); return s; };
      const p = gl.createProgram(); gl.attachShader(p, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(p);
      if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
      return p;
    }
    makeCloudMeshBuffer(mesh) {
      if (!mesh?.vertices?.length || !mesh?.triangles?.length) return null;
      const gl = this.gl;
      const vertices = new Float32Array(mesh.vertices.flat()); const normals = new Float32Array((mesh.normals?.length ? mesh.normals : mesh.vertices).flat()); const indices = new Uint32Array(mesh.triangles.flat());
      const vertexBuffer = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, vertexBuffer); gl.bufferData(gl.ARRAY_BUFFER, vertices, gl.STATIC_DRAW);
      const normalBuffer = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, normalBuffer); gl.bufferData(gl.ARRAY_BUFFER, normals, gl.STATIC_DRAW);
      const indexBuffer = gl.createBuffer(); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, indexBuffer); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, indices, gl.STATIC_DRAW);
      return { vertexBuffer, normalBuffer, indexBuffer, count: indices.length };
    }
    initGL() {
      const gl = this.gl;
      this.cloudProgram = this.compile(`#version 300 es
        in vec3 a_position; in vec3 a_normal; uniform mat4 u_vp; uniform vec3 u_center; uniform float u_scale; out vec3 v_world; out vec3 v_normal;
        void main(){ vec3 world=a_position*u_scale+u_center; v_world=world; v_normal=normalize(a_normal); gl_Position=u_vp*vec4(world,1.0); }`,
        `#version 300 es
        precision highp float; uniform vec3 u_camera; uniform vec3 u_color; uniform float u_alpha; uniform float u_rim; in vec3 v_world; in vec3 v_normal; out vec4 outColor;
        void main(){ vec3 viewDir=normalize(u_camera-v_world); float rim=pow(1.0-abs(dot(normalize(v_normal),viewDir)),2.35); float soft=0.34+u_rim*rim; outColor=vec4(u_color,u_alpha*soft); }`);
      this.pointProgram = this.compile(`#version 300 es
        in vec3 a_position; in vec3 a_color; in float a_alpha; in float a_size; uniform mat4 u_vp; out vec3 v_color; out float v_alpha;
        void main(){ gl_Position=u_vp*vec4(a_position,1.0); gl_PointSize=a_size; v_color=a_color; v_alpha=a_alpha; }`,
        `#version 300 es
        precision highp float; in vec3 v_color; in float v_alpha; out vec4 outColor;
        void main(){ vec2 q=gl_PointCoord*2.0-1.0; float r2=dot(q,q); if(r2>1.0) discard; float core=1.0-smoothstep(0.05,1.0,r2); float glow=exp(-2.7*r2); outColor=vec4(v_color,v_alpha*(0.38*glow+0.62*core)); }`);
      this.connProgram = this.compile(`#version 300 es
        in vec3 a_position; in vec3 a_color; in float a_alpha; uniform mat4 u_vp; out vec3 v_color; out float v_alpha;
        void main(){ gl_Position=u_vp*vec4(a_position,1.0); v_color=a_color; v_alpha=a_alpha; }`,
        `#version 300 es
        precision highp float; in vec3 v_color; in float v_alpha; out vec4 outColor; void main(){ outColor=vec4(v_color,v_alpha); }`);
      this.cloudMesh = this.makeCloudMeshBuffer(this.cloudVolume);
      this.connPosBuffer = gl.createBuffer(); this.connColorBuffer = gl.createBuffer(); this.connAlphaBuffer = gl.createBuffer(); this.connCount = 0;
      this.pointPosBuffer = gl.createBuffer(); this.pointColorBuffer = gl.createBuffer(); this.pointAlphaBuffer = gl.createBuffer(); this.pointSizeBuffer = gl.createBuffer();
      gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LEQUAL); gl.depthMask(true); gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      gl.clearColor(0.0015, 0.0025, 0.007, 1.0);
    }

    bind() {
      this.onResize = () => this.resize(); window.addEventListener('resize', this.onResize);
      this.canvas.addEventListener('contextmenu', (e) => e.preventDefault());
      this.canvas.addEventListener('wheel', (e) => {
        e.preventDefault(); this.noteInteraction();
        const selected = this.nodeMap.get(this.selectedId);
        if (e.altKey && selected && selected.node_type !== 'root') {
          const { forward } = this.cameraVectors();
          const step = clamp(e.deltaY, -120, 120) * clamp(this.camera.distance * 0.00018, 0.025, 0.45);
          this.translateDraftNode(selected, vmul(forward, step)); this.onDirty('depth', selected); this.onSelect(selected); return;
        }
        this.camera.desiredDistance = clamp(this.camera.desiredDistance * Math.exp(e.deltaY * 0.00105), 28, 3000);
      }, { passive: false });
      this.canvas.addEventListener('pointerdown', (e) => this.pointerDown(e));
      this.canvas.addEventListener('pointermove', (e) => this.pointerMove(e));
      this.canvas.addEventListener('pointerup', (e) => this.pointerUp(e));
      this.canvas.addEventListener('pointercancel', (e) => this.pointerUp(e));
      this.canvas.addEventListener('dblclick', (e) => { const hit = this.hitTest(e.offsetX, e.offsetY); if (hit?.node.node_type === 'domain') { this.noteInteraction(); this.focusCloud(hit.node.id); } else if (hit?.node.node_type === 'surface') { this.noteInteraction(); this.focusSurface(hit.node.id); } });
      this.canvas.addEventListener('keydown', (e) => this.keyDown(e));
    }
    noteInteraction() { this.lastInteractionAt = performance.now(); this.setAutoRotateStatus(false); }
    setAutoRotateStatus(active, now = performance.now()) {
      const node = $('#autoRotateStatus'); if (!node) return;
      let text, mode;
      if (REDUCED_MOTION) { text = 'AUTO-ROTATE · OFF (REDUCED MOTION)'; mode = 'waiting'; }
      else if (active) { text = 'AUTO-ROTATE · ACTIVE · 0.0544 rad/s'; mode = 'active'; }
      else { const remaining = Math.max(0, Math.ceil((this.idleRotateDelayMs - (now - this.lastInteractionAt)) / 1000)); text = `AUTO-ROTATE · RESUMES IN ${remaining}s`; mode = 'waiting'; }
      const sig = `${mode}:${text}`; if (sig === this.lastAutoStatus) return; this.lastAutoStatus = sig; node.textContent = text; node.dataset.mode = mode;
    }
    keyDown(e) {
      if (e.target !== this.canvas) return;
      this.noteInteraction();
      if (e.key === 'r' || e.key === 'R') { e.preventDefault(); this.resetCamera(); }
      if (e.key === '[') { e.preventDefault(); this.camera.desiredFov = clamp(this.camera.desiredFov + 2 * Math.PI / 180, 20 * Math.PI / 180, 90 * Math.PI / 180); }
      if (e.key === ']') { e.preventDefault(); this.camera.desiredFov = clamp(this.camera.desiredFov - 2 * Math.PI / 180, 20 * Math.PI / 180, 90 * Math.PI / 180); }
      if (e.key === 'Escape') { this.selectedId = null; this.pinnedReveal = null; this.onSelect(null); }
    }
    resize() {
      const rect = this.canvas.getBoundingClientRect(), dpr = Math.min(2, window.devicePixelRatio || 1);
      const w = Math.max(1, Math.round(rect.width * dpr)), h = Math.max(1, Math.round(rect.height * dpr));
      if (this.canvas.width !== w || this.canvas.height !== h) { this.canvas.width = w; this.canvas.height = h; this.labels.width = w; this.labels.height = h; }
      this.cssW = rect.width; this.cssH = rect.height; this.dpr = dpr; this.gl.viewport(0, 0, w, h);
    }
    domainNodes() { return this.nodes.filter((n) => n.node_type === 'domain'); }
    surfaceNodes() { return this.nodes.filter((n) => n.node_type === 'surface'); }
    memoryNodes() { return this.nodes.filter((n) => n.node_type === 'memory'); }
    membersOf(surfaceId) { return this.nodes.filter((n) => n.node_type === 'memory' && n.surface_id === surfaceId); }
    visibleNodes() { return [...this.domainNodes(), ...(this.surfacesShown ? this.surfaceNodes() : []), ...this.memoryNodes()]; }
    smoothCamera() {
      const c = this.camera, k = REDUCED_MOTION ? 1 : 0.18, kz = REDUCED_MOTION ? 1 : 0.2;
      c.yaw += (c.desiredYaw - c.yaw) * k; c.pitch += (c.desiredPitch - c.pitch) * k; c.roll += (c.desiredRoll - c.roll) * k;
      c.distance += (c.desiredDistance - c.distance) * kz; c.fov += (c.desiredFov - c.fov) * k;
      for (let i = 0; i < 3; i++) c.target[i] += (c.desiredTarget[i] - c.target[i]) * kz;
    }
    cameraVectors() {
      const c = this.camera, cp = Math.cos(c.pitch), sp = Math.sin(c.pitch), sy = Math.sin(c.yaw), cy = Math.cos(c.yaw);
      const eye = [c.target[0] + c.distance * cp * sy, c.target[1] + c.distance * sp, c.target[2] + c.distance * cp * cy];
      const forward = vnorm(vsub(c.target, eye)); const baseRight = vnorm(vcross(forward, [0, 1, 0])); const baseUp = vnorm(vcross(baseRight, forward));
      const cr = Math.cos(c.roll), sr = Math.sin(c.roll);
      return { eye, forward, right: vadd(vmul(baseRight, cr), vmul(baseUp, sr)), up: vadd(vmul(baseUp, cr), vmul(baseRight, -sr)) };
    }
    matrices() {
      const { eye, up } = this.cameraVectors();
      const view = mat4LookAt(eye, this.camera.target, up); const proj = mat4Perspective(this.camera.fov, Math.max(0.1, this.canvas.width / this.canvas.height), 0.1, 5000);
      return { vp: mat4Mul(proj, view), eye };
    }
    cloudRadius(node) {
      const children = this.nodes.filter((n) => (n.node_type === 'memory' || n.node_type === 'surface') && n.parent_id === node.id);
      if (!children.length) return 34;
      let maxD = 16;
      for (const ch of children) maxD = Math.max(maxD, vlen(vsub(posOf(ch), posOf(node))) + (ch.node_type === 'surface' ? this.surfaceRadius(ch) : 0));
      return clamp(maxD + 24, 36, 240);
    }
    surfaceRadius(node) {
      if (Number.isFinite(node.radius) && node.radius > 0) return node.radius;
      const count = node.member_count ?? this.membersOf(node.id).length;
      return clamp(6 + 2.2 * Math.sqrt(count || 1), 8, 28);
    }
    cloudColor(node) {
      let h = 0; const s = String(node.id || node.title || 'cloud'); for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
      const palette = [[0.18, 0.35, 0.62], [0.20, 0.45, 0.62], [0.30, 0.34, 0.67], [0.17, 0.39, 0.55], [0.28, 0.42, 0.70]];
      return palette[h % palette.length];
    }
    zoomFactor() { const ref = Math.max(150, (this.bounds.diagonal || 320) * 0.65); return clamp(ref / Math.max(1, this.camera.distance), 0, 1.2); }
    memoryOpacity() { const z = this.zoomFactor(); const t = clamp((z - 0.10) / 0.85, 0, 1); const smooth = t * t * (3 - 2 * t); return 0.10 + 0.90 * smooth; }

    // ----- Two-tier reveal: members fade in when a surface is near, selected, hovered, or pinned by search -----
    setSurfacesShown(v) {
      this.surfacesShown = !!v; localStorage.setItem(SURFACES_KEY, this.surfacesShown ? '1' : '0');
      if (!this.surfacesShown && this.nodeMap.get(this.selectedId)?.node_type === 'surface') { this.selectedId = null; this.onSelect(null); }
      this.connectionsDirty = true; this.updateLodBadge();
      $$('.surface-seg button').forEach((b) => b.setAttribute('aria-pressed', (b.dataset.surfaces === 'shown') === this.surfacesShown ? 'true' : 'false'));
    }
    revealAlpha(surfaceId) { if (!this.surfacesShown) return 1; return this.reveal.get(surfaceId) ?? 0; }
    memberVisibility(m) {
      if (!this.surfacesShown || !m.surface_id) return 1;
      if (!this.nodeMap.has(m.surface_id)) return 1; // orphan: surface not in scene, always show
      return this.revealAlpha(m.surface_id);
    }
    updateReveal(dt, eye) {
      if (!this.surfacesShown) return;
      let changed = false; const sig = [];
      for (const s of this.surfaceNodes()) {
        const r = this.surfaceRadius(s);
        const near = vlen(vsub(posOf(s), eye)) < REVEAL_DISTANCE_FACTOR * r;
        const target = (near || s.id === this.selectedId || s.id === this.hoverId || s.id === this.pinnedReveal) ? 1 : 0;
        const cur = this.reveal.get(s.id) ?? 0;
        const next = REDUCED_MOTION ? target : cur + (target - cur) * Math.min(1, dt / REVEAL_SECONDS);
        const val = Math.abs(next - target) < 0.01 ? target : next;
        if (Math.abs(val - cur) > 0.005) changed = true;
        this.reveal.set(s.id, val);
        if (val > 0.02) sig.push(s.id + ':' + Math.round(val * 8));
      }
      const s2 = sig.join('|');
      if (changed && s2 !== this.revealSig) { this.revealSig = s2; this.connectionsDirty = true; }
    }

    // ----- Edges: complete graph for small scenes, k-nearest beyond the LOD threshold; built off-thread when possible -----
    setDensity(mode, auto = false) { this.density = mode; this.densityAuto = auto; this.connectionsDirty = true; this.updateLodBadge(); }
    packedScene() {
      const surfaces = this.surfacesShown ? this.surfaceNodes() : [];
      const memories = this.memoryNodes();
      const all = [...surfaces, ...memories];
      const pos = new Float32Array(all.length * 3);
      all.forEach((n, i) => { pos[i * 3] = n.x || 0; pos[i * 3 + 1] = n.y || 0; pos[i * 3 + 2] = n.z || 0; });
      if (!this.surfacesShown) return { pos, count: all.length, tiers: null };
      const sIndex = new Map(surfaces.map((s, i) => [s.id, i]));
      const kinds = new Uint8Array(all.length), surfaceOf = new Int32Array(all.length).fill(-1), reveal = new Float32Array(all.length);
      surfaces.forEach((s, i) => { kinds[i] = 0; reveal[i] = this.reveal.get(s.id) ?? 0; });
      memories.forEach((m, j) => { const i = surfaces.length + j; const si = m.surface_id ? sIndex.get(m.surface_id) : undefined; if (si === undefined) { kinds[i] = 2; } else { kinds[i] = 1; surfaceOf[i] = si; } });
      return { pos, count: all.length, tiers: { kinds, surfaceOf, reveal } };
    }
    requestConnections() {
      const { pos, count, tiers } = this.packedScene();
      const mode = this.density;
      const small = count <= 160 || (tiers && this.surfaceNodes().length <= 120 && count <= 900);
      if ((!this.worker || small) && window.PODAEdges) { // small scenes: synchronous for instant feedback while dragging / fading
        const data = window.PODAEdges.buildEdges(pos, count, mode, KNN, tiers); this.uploadConnections(data.pos, data.colors, data.alphas, data.count); this.edgeCount = data.edges; this.connectionsDirty = false; this.updateLodBadge(); return;
      }
      if (!this.worker) { this.connectionsDirty = false; return; }
      const now = performance.now();
      if (this.connPending || now - this.connLastRequest < 50) { this.connectionsDirty = true; return; }
      this.connPending = true; this.connLastRequest = now; this.connectionsDirty = false; this.connSeq++;
      const transfer = [pos.buffer]; if (tiers) transfer.push(tiers.kinds.buffer, tiers.surfaceOf.buffer, tiers.reveal.buffer);
      this.worker.postMessage({ positions: pos, count, mode, k: KNN, seq: this.connSeq, tiers }, transfer);
    }
    uploadConnections(pos, colors, alphas, count) {
      const gl = this.gl; this.connCount = count;
      gl.bindBuffer(gl.ARRAY_BUFFER, this.connPosBuffer); gl.bufferData(gl.ARRAY_BUFFER, pos, gl.DYNAMIC_DRAW);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.connColorBuffer); gl.bufferData(gl.ARRAY_BUFFER, colors, gl.DYNAMIC_DRAW);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.connAlphaBuffer); gl.bufferData(gl.ARRAY_BUFFER, alphas, gl.DYNAMIC_DRAW);
    }
    updateLodBadge() {
      const badge = $('#lodBadge'); if (!badge) return;
      const n = this.memoryNodes().length, s = this.surfaceNodes().length;
      badge.hidden = false;
      const revealed = this.surfacesShown ? [...this.reveal.values()].filter((v) => v > 0.5).length : 0;
      badge.textContent = `${this.surfacesShown ? `${s} SURFACES (${revealed} OPEN) · ` : ''}${n} IN-DEPTH · ${this.edgeCount ?? 0} EDGES · ${this.density.toUpperCase()}${this.density === 'complete' ? ' GRAPH' : ` k=${this.density === 'minimal' ? 4 : KNN}`}${n > LOD_FULL_GRAPH_MAX && this.density !== 'complete' ? ' · LOD ACTIVE (ALL MEMORIES STILL SEARCHABLE)' : ''}${this.worker ? ' · WORKER' : ''}${this.surfacesShown ? '' : ' · CLASSIC VIEW'}`;
    }

    uploadAttr(buffer, data, loc, size) { const gl = this.gl; gl.bindBuffer(gl.ARRAY_BUFFER, buffer); gl.bufferData(gl.ARRAY_BUFFER, data, gl.DYNAMIC_DRAW); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0); }
    drawConnections(vp) {
      const gl = this.gl; if (this.connectionsDirty) this.requestConnections(); if (!this.connCount) return;
      gl.useProgram(this.connProgram); gl.uniformMatrix4fv(gl.getUniformLocation(this.connProgram, 'u_vp'), false, vp);
      let loc = gl.getAttribLocation(this.connProgram, 'a_position'); gl.bindBuffer(gl.ARRAY_BUFFER, this.connPosBuffer); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 3, gl.FLOAT, false, 0, 0);
      loc = gl.getAttribLocation(this.connProgram, 'a_color'); gl.bindBuffer(gl.ARRAY_BUFFER, this.connColorBuffer); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 3, gl.FLOAT, false, 0, 0);
      loc = gl.getAttribLocation(this.connProgram, 'a_alpha'); gl.bindBuffer(gl.ARRAY_BUFFER, this.connAlphaBuffer); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 1, gl.FLOAT, false, 0, 0);
      gl.disable(gl.DEPTH_TEST); gl.depthMask(false); gl.blendFunc(gl.SRC_ALPHA, gl.ONE); gl.drawArrays(gl.LINES, 0, this.connCount); gl.enable(gl.DEPTH_TEST); gl.depthMask(true);
    }
    drawClouds(vp, eye) {
      if (!this.cloudMesh) return;
      const gl = this.gl;
      const volumes = [...this.domainNodes().map((n) => ({ n, kind: 'domain' })), ...(this.surfacesShown ? this.surfaceNodes().map((n) => ({ n, kind: 'surface' })) : [])]
        .map((v) => ({ ...v, d: vlen(vsub(posOf(v.n), eye)) })).sort((a, b) => b.d - a.d);
      gl.useProgram(this.cloudProgram); gl.uniformMatrix4fv(gl.getUniformLocation(this.cloudProgram, 'u_vp'), false, vp); gl.uniform3fv(gl.getUniformLocation(this.cloudProgram, 'u_camera'), eye);
      let loc = gl.getAttribLocation(this.cloudProgram, 'a_position'); gl.bindBuffer(gl.ARRAY_BUFFER, this.cloudMesh.vertexBuffer); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 3, gl.FLOAT, false, 0, 0);
      loc = gl.getAttribLocation(this.cloudProgram, 'a_normal'); gl.bindBuffer(gl.ARRAY_BUFFER, this.cloudMesh.normalBuffer); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 3, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.cloudMesh.indexBuffer);
      gl.enable(gl.DEPTH_TEST); gl.depthMask(false); gl.disable(gl.CULL_FACE); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      const uCenter = gl.getUniformLocation(this.cloudProgram, 'u_center'), uScale = gl.getUniformLocation(this.cloudProgram, 'u_scale'), uColor = gl.getUniformLocation(this.cloudProgram, 'u_color'), uAlpha = gl.getUniformLocation(this.cloudProgram, 'u_alpha'), uRim = gl.getUniformLocation(this.cloudProgram, 'u_rim');
      for (const { n, kind } of volumes) {
        gl.uniform3f(uCenter, n.x || 0, n.y || 0, n.z || 0);
        if (kind === 'domain') {
          gl.uniform1f(uScale, this.cloudRadius(n)); gl.uniform3fv(uColor, this.cloudColor(n)); gl.uniform1f(uRim, 1.35);
          gl.uniform1f(uAlpha, n.id === this.selectedId ? 0.072 : 0.040);
        } else {
          const open = this.reveal.get(n.id) ?? 0; const hot = n.id === this.selectedId || n.id === this.hoverId;
          gl.uniform1f(uScale, this.surfaceRadius(n)); gl.uniform3fv(uColor, n.stale ? [0.72, 0.66, 0.88] : SURFACE_TINT); gl.uniform1f(uRim, 1.9);
          gl.uniform1f(uAlpha, 0.085 + 0.07 * open + (hot ? 0.06 : 0));
        }
        gl.drawElements(gl.TRIANGLES, this.cloudMesh.count, gl.UNSIGNED_INT, 0);
      }
      gl.depthMask(true);
    }
    pointData() {
      const memories = this.memoryNodes();
      const pos = [], colors = [], alphas = [], sizes = [];
      const baseOpacity = this.memoryOpacity(), z = this.zoomFactor();
      for (const m of memories) {
        const vis = this.memberVisibility(m); if (vis < 0.02) continue;
        pos.push(m.x || 0, m.y || 0, m.z || 0);
        const selected = m.id === this.selectedId, hover = m.id === this.hoverId;
        const c = selected ? [0.95, 0.98, 1] : hover ? [0.70, 0.88, 1] : m.memory_kind === 'summary' ? [0.62, 0.66, 1] : m.memory_kind === 'durable' ? [0.50, 0.88, 1] : [0.45, 0.72, 1];
        colors.push(...c); alphas.push((selected ? 1 : hover ? Math.max(0.82, baseOpacity) : baseOpacity) * vis);
        sizes.push((selected ? 8.5 : hover ? 7.0 : 2.6 + 3.4 * clamp(z, 0, 1)) * this.dpr * (m.surface_id && this.surfacesShown ? 0.9 : 1));
      }
      return { pos: new Float32Array(pos), colors: new Float32Array(colors), alphas: new Float32Array(alphas), sizes: new Float32Array(sizes), count: sizes.length };
    }
    drawPoints(vp) {
      const gl = this.gl, data = this.pointData(); if (!data.count) return;
      gl.useProgram(this.pointProgram); gl.uniformMatrix4fv(gl.getUniformLocation(this.pointProgram, 'u_vp'), false, vp);
      this.uploadAttr(this.pointPosBuffer, data.pos, gl.getAttribLocation(this.pointProgram, 'a_position'), 3);
      this.uploadAttr(this.pointColorBuffer, data.colors, gl.getAttribLocation(this.pointProgram, 'a_color'), 3);
      this.uploadAttr(this.pointAlphaBuffer, data.alphas, gl.getAttribLocation(this.pointProgram, 'a_alpha'), 1);
      this.uploadAttr(this.pointSizeBuffer, data.sizes, gl.getAttribLocation(this.pointProgram, 'a_size'), 1);
      gl.disable(gl.DEPTH_TEST); gl.depthMask(false); gl.blendFunc(gl.SRC_ALPHA, gl.ONE); gl.drawArrays(gl.POINTS, 0, data.count); gl.enable(gl.DEPTH_TEST); gl.depthMask(true);
    }
    screenRadius(vp, center, worldRadius) {
      const { right } = this.cameraVectors();
      const a = projectWorld(vp, center, this.cssW, this.cssH), b = projectWorld(vp, vadd(center, vmul(right, worldRadius)), this.cssW, this.cssH);
      if (!a || !b) return 10;
      return Math.max(10, Math.hypot(a.x - b.x, a.y - b.y));
    }
    drawLabels(vp) {
      const ctx = this.labelCtx; ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0); ctx.clearRect(0, 0, this.cssW, this.cssH); this.projected = [];
      const zoom = this.zoomFactor(), memAlpha = this.memoryOpacity(); const many = this.memoryNodes().length > LOD_FULL_GRAPH_MAX;
      const labelThreshold = many ? 0.92 : 0.72;
      const memCount = new Map(); for (const m of this.memoryNodes()) memCount.set(m.parent_id, (memCount.get(m.parent_id) || 0) + 1);
      const drawn = []; // greedy label decluttering: selected/hover/cloud/surface labels win, other labels skip overlaps
      const overlaps = (x, y, w, h) => drawn.some((r) => x < r.x + r.w && x + w > r.x && y < r.y + r.h && y + h > r.y);
      const rank = (n) => (n.id === this.selectedId || n.id === this.hoverId) ? 0 : n.node_type === 'domain' ? 1 : n.node_type === 'surface' ? 2 : 3;
      const ordered = this.visibleNodes().sort((a, b) => rank(a) - rank(b));
      for (const n of ordered) {
        const p = projectWorld(vp, posOf(n), this.cssW, this.cssH); if (!p || p.depth > 1) continue;
        const selected = n.id === this.selectedId, hover = n.id === this.hoverId;
        if (n.node_type === 'memory') {
          const vis = this.memberVisibility(n); if (vis < 0.02) continue;
          const r = Math.max(8, (2.6 + 3.4 * clamp(zoom, 0, 1)) * 1.5);
          this.projected.push({ node: n, x: p.x, y: p.y, r, priority: 3 });
          const nearSurface = n.surface_id && this.surfacesShown;
          if (!(selected || hover || zoom > labelThreshold || (nearSurface && vis > 0.6 && zoom > 0.35))) continue;
          ctx.font = '10px "SF Mono", Menlo, monospace';
          const text = String(n.title || '').slice(0, 54); const w = ctx.measureText(text).width + 8, h = 14, lx = p.x - w / 2, ly = p.y + 9;
          if (!(selected || hover) && overlaps(lx, ly, w, h)) continue;
          drawn.push({ x: lx, y: ly, w, h });
          ctx.globalAlpha = (selected || hover ? 1 : clamp(Math.max((zoom - labelThreshold) / 0.35, nearSurface ? 0.55 : 0), 0, 0.72) * memAlpha) * vis;
          ctx.textAlign = 'center'; ctx.textBaseline = 'top'; ctx.shadowBlur = 5; ctx.shadowColor = 'rgba(114,160,255,.70)'; ctx.fillStyle = selected ? '#ffffff' : 'rgba(220,232,255,.92)';
          ctx.fillText(text, p.x, p.y + 10); ctx.shadowBlur = 0; ctx.globalAlpha = 1;
        } else if (n.node_type === 'surface') {
          const sr = this.screenRadius(vp, posOf(n), this.surfaceRadius(n));
          this.projected.push({ node: n, x: p.x, y: p.y, r: sr, priority: 2 });
          const show = selected || hover || zoom > 0.22;
          if (!show) continue;
          ctx.font = '600 11px "SF Mono", Menlo, monospace'; const t = String(n.title || 'Surface memory').slice(0, 58); const w = ctx.measureText(t).width + 10, h = 26, lx = p.x - w / 2, ly = p.y + sr + 4;
          if (!(selected || hover) && overlaps(lx, ly, w, h)) continue;
          drawn.push({ x: lx, y: ly, w, h });
          ctx.globalAlpha = selected || hover ? 1 : clamp((zoom - 0.22) / 0.3, 0.35, 0.92);
          ctx.textAlign = 'center'; ctx.textBaseline = 'top'; ctx.shadowBlur = 8; ctx.shadowColor = 'rgba(154,168,255,.75)'; ctx.fillStyle = selected ? '#ffffff' : 'rgba(226,228,255,.95)';
          ctx.fillText(t, p.x, ly); ctx.shadowBlur = 0;
          ctx.font = '9px "SF Mono", Menlo, monospace'; ctx.fillStyle = 'rgba(190,196,245,.62)'; ctx.fillText(`${n.member_count ?? this.membersOf(n.id).length} memories${n.stale ? ' · stale' : ''}`, p.x, ly + 14); ctx.globalAlpha = 1;
        } else {
          this.projected.push({ node: n, x: p.x, y: p.y, r: 22, priority: 1 });
          ctx.font = '600 12px "SF Mono", Menlo, monospace'; const ct = String(n.title || '').slice(0, 64); const cw = ctx.measureText(ct).width + 10; drawn.push({ x: p.x - cw / 2, y: p.y + 6, w: cw, h: 30 });
          ctx.textAlign = 'center'; ctx.textBaseline = 'top'; ctx.shadowBlur = 9; ctx.shadowColor = 'rgba(114,140,255,.72)'; ctx.fillStyle = 'rgba(236,240,255,.94)';
          ctx.fillText(ct, p.x, p.y + 8); ctx.shadowBlur = 0;
          const sCount = this.surfaceNodes().filter((s) => s.parent_id === n.id).length;
          ctx.font = '9px "SF Mono", Menlo, monospace'; ctx.fillStyle = 'rgba(170,190,235,.55)'; ctx.fillText(`${memCount.get(n.id) || 0} memories${this.surfacesShown && sCount ? ` · ${sCount} surfaces` : ''}`, p.x, p.y + 24);
        }
      }
    }
    render(dt) {
      this.resize(); this.smoothCamera(); const gl = this.gl; gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      const { vp, eye } = this.matrices(); this.lastMatrices = vp;
      this.updateReveal(dt, eye);
      this.drawClouds(vp, eye); this.drawConnections(vp); this.drawPoints(vp); this.drawLabels(vp);
    }
    hitTest(x, y) {
      let best = null;
      for (const p of this.projected) { const d = Math.hypot(x - p.x, y - p.y); if (d <= p.r + 8 && (!best || p.priority > best.priority || (p.priority === best.priority && d < best.d))) best = { ...p, d }; }
      return best;
    }
    pointerDown(e) {
      this.noteInteraction(); this.canvas.focus({ preventScroll: true }); this.canvas.setPointerCapture?.(e.pointerId);
      const hit = this.hitTest(e.offsetX, e.offsetY); const wasSelected = !!hit && this.selectedId === hit.node.id;
      if (hit) { this.selectedId = hit.node.id; if (hit.node.node_type === 'surface') this.pinnedReveal = hit.node.id; if (hit.node.node_type === 'memory' && hit.node.surface_id) this.pinnedReveal = hit.node.surface_id; this.onSelect(hit.node); }
      let mode = 'orbit';
      const plain = e.button === 0 && !e.ctrlKey && !e.metaKey && !e.shiftKey;
      if (hit?.node.node_type === 'memory' && plain) mode = 'node';
      else if ((hit?.node.node_type === 'domain' || hit?.node.node_type === 'surface') && plain && (e.altKey || wasSelected)) mode = 'node';
      else if (e.ctrlKey || e.metaKey || e.button === 1 || e.button === 2) mode = 'pan';
      else if (e.shiftKey && e.button === 0) mode = 'roll';
      this.pointer = { x: e.clientX, y: e.clientY, hitId: hit?.node.id || null, mode, dirtyNotified: false };
    }
    translateDraftNode(node, delta) {
      const move = (n) => { n.x = (n.x || 0) + delta[0]; n.y = (n.y || 0) + delta[1]; n.z = (n.z || 0) + delta[2]; n.position_source = 'draft'; };
      move(node);
      if (node.node_type === 'domain') for (const ch of this.nodes) { if ((ch.node_type === 'memory' || ch.node_type === 'surface') && ch.parent_id === node.id) move(ch); }
      if (node.node_type === 'surface') for (const m of this.membersOf(node.id)) move(m);
      this.connectionsDirty = true;
    }
    pointerMove(e) {
      const hit = this.hitTest(e.offsetX, e.offsetY); this.hoverId = hit?.node.id || null;
      const selectedVolume = (hit?.node.node_type === 'domain' || hit?.node.node_type === 'surface') && this.selectedId === hit.node.id;
      this.canvas.style.cursor = this.pointer ? (this.pointer.mode === 'node' ? 'move' : 'grabbing') : (hit?.node.node_type === 'memory' || selectedVolume ? 'move' : hit ? 'pointer' : 'grab');
      if (!this.pointer) return;
      this.noteInteraction();
      const dx = e.clientX - this.pointer.x, dy = e.clientY - this.pointer.y; this.pointer.x = e.clientX; this.pointer.y = e.clientY;
      if (this.pointer.mode === 'orbit') { this.camera.desiredYaw -= dx * 0.0062; this.camera.desiredPitch = clamp(this.camera.desiredPitch - dy * 0.0058, -Math.PI * 0.485, Math.PI * 0.485); }
      else if (this.pointer.mode === 'roll') this.camera.desiredRoll += dx * 0.0065;
      else if (this.pointer.mode === 'pan') { const { right, up } = this.cameraVectors(), wpp = 2 * this.camera.distance * Math.tan(this.camera.fov / 2) / Math.max(200, this.cssH); this.camera.desiredTarget = vadd(this.camera.desiredTarget, vadd(vmul(right, -dx * wpp), vmul(up, dy * wpp))); }
      else if (this.pointer.mode === 'node') {
        const node = this.nodeMap.get(this.pointer.hitId);
        if (node) { const { right, up } = this.cameraVectors(), wpp = 2 * this.camera.distance * Math.tan(this.camera.fov / 2) / Math.max(200, this.cssH); this.translateDraftNode(node, vadd(vmul(right, dx * wpp), vmul(up, -dy * wpp))); if (!this.pointer.dirtyNotified) { this.onDirty('position', node); this.pointer.dirtyNotified = true; } }
      }
    }
    pointerUp(e) {
      if (this.pointer) { const movedId = this.pointer.mode === 'node' ? this.pointer.hitId : null; try { this.canvas.releasePointerCapture?.(e.pointerId); } catch { /* ignore */ } this.pointer = null; if (movedId) { const n = this.nodeMap.get(movedId); if (n) this.onSelect(n); return; } }
      this.pointer = null;
    }
    addDraftNode(node) { this.nodes.push(node); this.nodeMap.set(node.id, node); this.selectedId = node.id; this.connectionsDirty = true; this.onDirty('add', node); this.onSelect(node); }
    removeDraftNode(id, { dissolve = false } = {}) {
      const n = this.nodeMap.get(id); if (!n || n.node_type === 'root') return false;
      if (this.nodes.some((x) => x.parent_id === id)) return false;
      if (n.node_type === 'surface') { const members = this.membersOf(id); if (members.length && !dissolve) return false; for (const m of members) { m.surface_id = null; m.position_source = 'draft'; } }
      this.nodes = this.nodes.filter((x) => x.id !== id); this.nodeMap.delete(id); this.reveal.delete(id); this.connectionsDirty = true;
      if (this.selectedId === id) this.selectedId = null; if (this.focusedCloudId === id) this.focusedCloudId = null; if (this.pinnedReveal === id) this.pinnedReveal = null; this.onDirty('delete', n); return true;
    }
    moveNodeToCloud(id, parentId) {
      const n = this.nodeMap.get(id), p = this.nodeMap.get(parentId); if (!n || (n.node_type !== 'memory' && n.node_type !== 'surface') || !p || p.node_type !== 'domain') return;
      const delta = vsub(vadd(posOf(p), [18, 8, 10]), posOf(n));
      n.parent_id = parentId; this.translateDraftNode(n, delta);
      if (n.node_type === 'memory' && n.surface_id && this.nodeMap.get(n.surface_id)?.parent_id !== parentId) n.surface_id = null; // a member cannot live in a different cloud than its surface
      for (const m of n.node_type === 'surface' ? this.membersOf(n.id) : []) m.parent_id = parentId;
      this.connectionsDirty = true; this.onDirty('parent', n); this.onSelect(n);
    }
    moveMemberToSurface(id, surfaceId) {
      const n = this.nodeMap.get(id); if (!n || n.node_type !== 'memory') return;
      const s = surfaceId ? this.nodeMap.get(surfaceId) : null;
      n.surface_id = s ? s.id : null; n.position_source = 'draft';
      if (s) { n.parent_id = s.parent_id; const r = this.surfaceRadius(s) * 0.6; const k = this.membersOf(s.id).length; const a = k * 2.399963; n.x = (s.x || 0) + Math.cos(a) * r; n.y = (s.y || 0) + Math.sin(a) * r * 0.7; n.z = (s.z || 0) + Math.sin(a * 0.5) * r * 0.5; this.pinnedReveal = s.id; }
      this.connectionsDirty = true; this.onDirty('surface', n); this.onSelect(n);
    }
    draftNodes() { return this.nodes.filter((n) => n.node_type !== 'transient').map((n) => ({ ...n, tags: Array.isArray(n.tags) ? n.tags : [] })); }
    focusNode(id) {
      this.noteInteraction(); const n = this.nodeMap.get(id); if (!n) return false;
      this.selectedId = id; this.camera.desiredTarget = posOf(n);
      if (n.node_type === 'domain') this.camera.desiredDistance = clamp(this.cloudRadius(n) * 2.55, 105, 520);
      else if (n.node_type === 'surface') { this.pinnedReveal = id; this.camera.desiredDistance = clamp(this.surfaceRadius(n) * 7, 80, 300); }
      else { if (n.surface_id) this.pinnedReveal = n.surface_id; const s = n.surface_id ? this.nodeMap.get(n.surface_id) : null; this.camera.desiredDistance = s ? clamp(this.surfaceRadius(s) * 4.5, 60, 200) : clamp(this.camera.distance * 0.58, 72, 220); }
      this.onSelect(n); return true;
    }
    focusSurface(id) { const n = this.nodeMap.get(id); if (!n || n.node_type !== 'surface') return; this.focusNode(id); }
    focusCloud(id) { this.noteInteraction(); const n = this.nodeMap.get(id); if (!n || n.node_type !== 'domain') return; this.focusedCloudId = id; this.selectedId = id; this.camera.desiredTarget = posOf(n); this.camera.desiredDistance = clamp(this.cloudRadius(n) * 2.55, 105, 520); this.onSelect(n); }
    overview() { this.noteInteraction(); this.focusedCloudId = null; this.pinnedReveal = null; this.camera.desiredTarget = [...(this.bounds.center || [0, 0, 0])]; this.camera.desiredDistance = Math.max(360, (this.bounds.diagonal || 320) * 1.45); }
    resetCamera() { this.noteInteraction(); if (this.focusedCloudId) this.focusCloud(this.focusedCloudId); else this.overview(); this.camera.desiredYaw = -0.66; this.camera.desiredPitch = 0.25; this.camera.desiredRoll = 0; this.camera.desiredFov = 50 * Math.PI / 180; }
    animate(now = performance.now()) {
      if (this.destroyed) return;
      const dt = clamp((now - this.lastFrameAt) / 1000, 0, 0.08); this.frameTimes.push(now - this.lastFrameAt); if (this.frameTimes.length > 240) this.frameTimes.shift(); this.lastFrameAt = now;
      const autoActive = !REDUCED_MOTION && !this.pointer && (now - this.lastInteractionAt) >= this.idleRotateDelayMs;
      if (autoActive) this.camera.desiredYaw += this.autoRotateRate * dt;
      this.setAutoRotateStatus(autoActive, now);
      this.render(dt); requestAnimationFrame((t) => this.animate(t));
    }
    frameStats() { const f = this.frameTimes.slice(1); if (!f.length) return null; const avg = f.reduce((a, b) => a + b, 0) / f.length; return { avg_ms: +avg.toFixed(2), max_ms: +Math.max(...f).toFixed(2), fps: +(1000 / avg).toFixed(1), samples: f.length }; }
    destroy() { this.destroyed = true; window.removeEventListener('resize', this.onResize); this.worker?.terminate(); }
  }

  // ---------------------------------------------------------------------------
  let memoryScene = null, memoryDraftDirty = false, memoryDeletedIds = new Set(), memoryDissolve = false, memorySearchMatches = [], memorySearchIndex = -1, searchTimer = 0, surfaceList = null;

  function setStatus(text, mode = '') { const node = $('#memoryDraftStatus'); node.textContent = text; node.dataset.mode = mode; }
  function markDirty(kind, node) { memoryDraftDirty = true; $('#commitMemoryChanges').disabled = false; setStatus(`PENDING DRAFT · ${kind.toUpperCase()}${node?.title ? ' · ' + String(node.title).slice(0, 34) : ''}`, 'dirty'); }
  function domainOptions(selectedId = '') { return (memoryScene?.nodes || []).filter((n) => n.node_type === 'domain').map((n) => el('option', { value: n.id, selected: n.id === selectedId }, n.title)); }
  function surfaceOptions(selectedId = '', cloudId = null) {
    const fromScene = (memoryScene?.nodes || []).filter((n) => n.node_type === 'surface');
    const list = fromScene.length ? fromScene : (surfaceList || []);
    const opts = [el('option', { value: '', selected: !selectedId }, 'no surface (stays a free point)')];
    for (const s of list) if (!cloudId || !s.parent_id || s.parent_id === cloudId) opts.push(el('option', { value: s.id, selected: s.id === selectedId }, `${s.title || 'Surface'} (${s.member_count ?? memoryScene?.membersOf(s.id).length ?? 0})`));
    return opts;
  }
  function referenceValue(node) { const explicit = String(node?.reference_triggers || '').trim(); if (explicit) return explicit; return (Array.isArray(node?.tags) ? node.tags.filter(Boolean) : []).join(', '); }
  function tierOf(node) { if (node.node_type === 'surface') return 'surface'; const t = node.tier ? String(node.tier).replace(/_/g, '-') : (node.memory_kind === 'durable' ? 'durable' : 'in-depth'); return t === 'memory' ? 'in-depth' : t; }
  function tierPill(node) { const t = tierOf(node); return pill(t, t === 'surface' ? 'lav' : t === 'durable' ? 'ok' : 'accent'); }

  function provenanceRow(node) {
    const kind = node.memory_kind || 'durable'; const speaker = node.speaker || '';
    return el('div', { class: 'provenance-row' }, tierPill(node), pill(kind, kind === 'durable' ? 'ok' : kind === 'summary' ? 'lav' : 'accent'),
      speaker && speaker !== 'exchange' ? pill(speaker === 'assistant' ? 'prior PODA response · not authoritative' : speaker, speaker === 'assistant' ? 'warn' : 'muted') : null,
      node.position_source ? pill(`position: ${node.position_source}`, 'muted') : null, node.superseded_by ? pill('superseded', 'warn') : null);
  }
  function versionHistory(nodeId) {
    const versions = el('div', { class: 'version-list' }, el('span', { class: 'faint small' }, 'Loading history…'));
    if (String(nodeId).startsWith('draft-')) { versions.replaceChildren(el('span', { class: 'faint small' }, 'New draft; history begins at first commit.')); return versions; }
    api(`/memory/versions/${encodeURIComponent(nodeId)}`).then((r) => {
      const list = r.versions || [];
      versions.replaceChildren(...(list.length ? list.slice(0, 20).map((v) => el('div', { class: 'version-item' }, el('div', { class: 'row' }, pill(v.change, v.change === 'delete' ? 'danger' : v.change === 'create' ? 'ok' : 'accent'), el('span', { class: 'mono' }, fmtTime(v.created_at)), el('span', { class: 'mono' }, `tx ${String(v.transaction_id).slice(0, 8)}`)),
        v.before && v.after ? el('span', { class: 'faint' }, Object.keys(v.after).filter((k) => JSON.stringify(v.before[k]) !== JSON.stringify(v.after[k])).join(', ') || 'no field change') : null)) : [el('span', { class: 'faint small' }, 'No committed versions yet.')]));
    }).catch(() => versions.replaceChildren(el('span', { class: 'faint small' }, 'History unavailable.')));
    return versions;
  }
  function xyzEditor(node, onChange, prefix) {
    const xyz = ['X', 'Y', 'Z'].map((axis) => el('input', { class: 'input', id: `${prefix}${axis}`, type: 'number', step: 1, value: Number(node[axis.toLowerCase()] || 0).toFixed(2), 'aria-label': axis }));
    const sync = () => { const [x, y, z] = xyz.map((i) => Number(i.value)); if ([x, y, z].every(Number.isFinite)) { memoryScene?.translateDraftNode(node, [x - (node.x || 0), y - (node.y || 0), z - (node.z || 0)]); onChange(); } };
    xyz.forEach((i) => { i.onchange = sync; });
    return { wrap: el('div', { class: 'xyz-editor' }, ...xyz), refresh: () => xyz.forEach((i, k) => { i.value = Number(node[['x', 'y', 'z'][k]] || 0).toFixed(2); }) };
  }

  function renderMemoryDetail(node) {
    const panel = $('#nodePanel'); panel.innerHTML = '';
    const isPair = node.memory_kind === 'exchange' || (node.user_text !== undefined && node.user_text !== null) || (node.assistant_text !== undefined && node.assistant_text !== null);
    const inferred = !String(node.reference_triggers || '').trim();
    const title = el('input', { class: 'input', id: 'mTitle', value: node.title || '', 'aria-label': 'Memory title' });
    const content = el('textarea', { class: 'input memory-primary-textarea', id: 'mContent', rows: 8 }); content.value = node.content || node.summary || '';
    const triggers = el('textarea', { class: 'input memory-trigger-textarea', id: 'mTriggers', rows: 4, placeholder: 'Topics, people, projects, dates, questions, or phrases that should recall this memory' }); triggers.value = referenceValue(node);
    const parent = el('select', { class: 'input', id: 'mParent' }, ...domainOptions(node.parent_id || 'seed-personal-memory'));
    const surface = el('select', { class: 'input', id: 'mSurface' }, ...surfaceOptions(node.surface_id || '', node.parent_id));
    const owner = node.surface_id ? memoryScene?.nodeMap.get(node.surface_id) : null;
    const xyz = xyzEditor(node, () => markDirty('position', node), 'm');
    const del = el('button', { class: 'btn btn-danger btn-sm' }, 'Stage delete');
    panel.append(
      el('div', { class: 'node-kind' }, isPair ? 'In-depth memory · question and answer · uncommitted draft' : 'Memory point · uncommitted draft'),
      provenanceRow(node),
      el('label', { class: 'label', for: 'mTitle' }, 'Memory title'), title);
    if (isPair) {
      panel.append(
        el('div', { class: 'pair-block you' }, el('div', { class: 'who' }, 'You', node.source_message_id ? el('span', { class: 'mono faint', style: { textTransform: 'none', letterSpacing: 0 } }, String(node.source_message_id).slice(0, 8)) : null), el('div', { class: 'txt' }, String(node.user_text ?? '').trim() || '(no user text recorded)')),
        el('div', { class: 'pair-block poda' }, el('div', { class: 'who' }, 'PODA', pill('contextual · not authoritative', 'warn')), el('div', { class: 'txt' }, String(node.assistant_text ?? '').trim() || '(no answer recorded)')),
        el('p', { class: 'help' }, 'The pair is the immutable record of this exchange. Edit the title and triggers to steer recall; the surface memory above it holds the distilled meaning.'));
    } else {
      panel.append(el('label', { class: 'label', for: 'mContent' }, 'What this memory actually contains'), content);
    }
    panel.append(
      el('label', { class: 'label', for: 'mTriggers' }, 'What should cause PODA to reference this memory?'), triggers,
      el('p', { class: 'help' }, (inferred ? 'These cues are inferred from tags. Editing makes them explicit. ' : 'These explicit cues are part of the retrieval embedding. ') + 'Text edits re-embed on commit; moving the point only changes the committed spatial signal.'),
      el('label', { class: 'label', for: 'mSurface' }, 'Surface memory'), surface,
      owner ? el('div', { class: 'row small' }, el('span', { class: 'faint' }, 'Unified context: this point’s cosine blends with'), el('button', { class: 'btn btn-sm btn-ghost', onclick: () => memoryScene?.focusNode(owner.id) }, owner.title || 'its surface')) : el('p', { class: 'help' }, 'Free points are scored on their own; assigning a surface blends in the surface’s score.'),
      el('label', { class: 'label', for: 'mParent' }, 'Memory cloud'), parent,
      el('label', { class: 'label' }, 'Draft position (x, y, z)'), xyz.wrap,
      node.source_message_id ? el('p', { class: 'faint small mono' }, `source message ${String(node.source_message_id).slice(0, 8)}${node.response_message_id ? ` · reply ${String(node.response_message_id).slice(0, 8)}` : ''} · ${fmtTime(node.created_at)}`) : el('p', { class: 'faint small' }, `created ${fmtTime(node.created_at)}`),
      el('div', { class: 'node-actions' }, el('button', { class: 'btn btn-sm', onclick: () => memoryScene?.focusNode(node.id) }, 'Focus'), del),
      el('div', { class: 'panel-title', style: { marginTop: '8px' } }, 'Version history'), versionHistory(node.id));
    title.oninput = () => { node.title = title.value; markDirty('title', node); };
    content.oninput = () => { node.content = content.value; node.summary = content.value.trim().replace(/\s+/g, ' ').slice(0, 420); markDirty('memory text', node); };
    triggers.oninput = () => { node.reference_triggers = triggers.value; markDirty('reference triggers', node); };
    parent.onchange = () => { memoryScene?.moveNodeToCloud(node.id, parent.value); markDirty('cloud', node); xyz.refresh(); surface.replaceChildren(...surfaceOptions(node.surface_id || '', node.parent_id)); };
    surface.onchange = () => { memoryScene?.moveMemberToSurface(node.id, surface.value || null); markDirty('surface', node); xyz.refresh(); parent.value = node.parent_id; };
    del.onclick = async () => { if (!(await confirmDialog({ title: `Stage deletion of "${node.title}"?`, body: isPair ? 'Nothing is deleted until you commit. Deleting this pair also removes both hidden source messages on commit.' : 'Nothing is deleted until you commit. Deleting a chat-backed memory also removes its hidden source message on commit.', confirmLabel: 'Stage delete', danger: true }))) return; memoryDeletedIds.add(node.id); memoryScene.removeDraftNode(node.id); markDirty('delete', node); showNode(null); };
  }

  function renderSurfaceEditor(node) {
    const panel = $('#nodePanel'); panel.innerHTML = '';
    const members = memoryScene.membersOf(node.id);
    const title = el('input', { class: 'input', id: 'sTitle', value: node.title || '', 'aria-label': 'Surface title' });
    const content = el('textarea', { class: 'input memory-primary-textarea', id: 'sContent', rows: 6, placeholder: 'The distilled core meaning PODA reads first' }); content.value = node.content || node.summary || '';
    const triggers = el('textarea', { class: 'input memory-trigger-textarea', id: 'sTriggers', rows: 3 }); triggers.value = referenceValue(node);
    const parent = el('select', { class: 'input', id: 'sParent' }, ...domainOptions(node.parent_id || 'seed-personal-memory'));
    const xyz = xyzEditor(node, () => markDirty('surface position', node), 's');
    const conf = Math.round((node.confidence ?? 0) * 100);
    const redistill = el('button', { class: 'btn btn-sm' }, 'Re-distill now');
    const del = el('button', { class: 'btn btn-danger btn-sm' }, 'Stage delete');
    panel.append(
      el('div', { class: 'node-kind' }, 'Surface memory · uncommitted draft'),
      el('div', { class: 'provenance-row' }, pill('surface', 'lav'), pill(`${members.length} ${members.length === 1 ? 'memory' : 'memories'}`, 'muted'), pill(node.stale ? 'stale' : 'fresh', node.stale ? 'warn' : 'ok'), node.confidence !== undefined && node.confidence !== null ? pill(`confidence ${conf}%`, conf >= 70 ? 'ok' : 'warn') : null, node.user_edited ? pill('user edited', 'accent') : null, node.position_source ? pill(`position: ${node.position_source}`, 'muted') : null),
      el('label', { class: 'label', for: 'sTitle' }, 'Core meaning title'), title,
      el('label', { class: 'label', for: 'sContent' }, 'Core meaning'), content,
      el('p', { class: 'help' }, 'This is the vector-indexed memory PODA reads first. Its cosine score lifts every in-depth member inside it (unified context). Editing it re-embeds on commit and marks it user-edited so automatic re-distillation leaves it alone.'),
      el('label', { class: 'label', for: 'sTriggers' }, 'Reference triggers'), triggers,
      el('label', { class: 'label', for: 'sParent' }, 'Memory cloud'), parent,
      el('label', { class: 'label' }, 'Draft position (x, y, z)'), xyz.wrap,
      el('p', { class: 'help' }, 'Drag the selected surface to move it with every member inside. Members appear when you zoom close, hover, or select the surface.'),
      el('div', { class: 'node-actions' }, el('button', { class: 'btn btn-sm', onclick: () => memoryScene?.focusSurface(node.id) }, 'Focus'), redistill, del),
      el('div', { class: 'panel-title', style: { marginTop: '8px' } }, `Members (${members.length})`),
      members.length ? el('div', { class: 'member-list' }, ...members.map((m) => el('button', { type: 'button', class: 'member-item', onclick: () => memoryScene?.focusNode(m.id) }, tierPill(m), el('span', { class: 'mt', title: m.title || '' }, m.title || 'Untitled'), el('span', { class: 'mono faint small' }, fmtTime(m.created_at))))) : el('p', { class: 'faint small' }, 'No members yet.'),
      el('div', { class: 'panel-title', style: { marginTop: '8px' } }, 'Version history'), versionHistory(node.id));
    title.oninput = () => { node.title = title.value; node.user_edited = true; markDirty('surface title', node); };
    content.oninput = () => { node.content = content.value; node.summary = content.value.trim().replace(/\s+/g, ' ').slice(0, 420); node.user_edited = true; markDirty('surface meaning', node); };
    triggers.oninput = () => { node.reference_triggers = triggers.value; node.user_edited = true; markDirty('surface triggers', node); };
    parent.onchange = () => { memoryScene?.moveNodeToCloud(node.id, parent.value); markDirty('cloud', node); xyz.refresh(); };
    redistill.onclick = async () => {
      if (String(node.id).startsWith('draft-')) { toast('Commit this surface first, then re-distill it.', 'danger'); return; }
      if (memoryDraftDirty && !(await confirmDialog({ title: 'Re-distill and reload the scene?', body: 'Re-distillation rewrites this surface from its members on the server and reloads the scene, which discards your uncommitted draft.', confirmLabel: 'Re-distill', danger: true }))) return;
      redistill.disabled = true;
      try { await api(`/memory/surfaces/${encodeURIComponent(node.id)}/distill`, { method: 'POST' }); toast('Surface re-distilled', 'ok'); await loadMemoryViewer(); memoryScene?.focusNode(node.id); }
      catch (err) { toast(errorDetail(err).message, 'danger'); redistill.disabled = false; }
    };
    del.onclick = async () => {
      if (!(await confirmDialog({ title: `Stage deletion of "${node.title || 'this surface'}"?`, body: `Nothing is deleted until you commit. On commit the surface is removed and its ${members.length} in-depth ${members.length === 1 ? 'memory is' : 'memories are'} dissolved back into the cloud, where PODA will re-group them.`, confirmLabel: 'Stage delete', danger: true }))) return;
      memoryDeletedIds.add(node.id); memoryDissolve = true; memoryScene.removeDraftNode(node.id, { dissolve: true }); markDirty('delete surface', node); showNode(null);
    };
  }

  function renderCloudEditor(node) {
    const panel = $('#nodePanel'); panel.innerHTML = '';
    const title = el('input', { class: 'input', id: 'nTitle', value: node.title || '' });
    const content = el('textarea', { class: 'input', id: 'nContent', rows: 4 }); content.value = node.summary || node.content || '';
    const xyz = xyzEditor(node, () => markDirty('cloud position', node), 'n');
    const del = el('button', { class: 'btn btn-danger btn-sm' }, 'Stage delete');
    const surfaces = memoryScene.surfaceNodes().filter((s) => s.parent_id === node.id);
    panel.append(el('div', { class: 'node-kind' }, 'Memory cloud · uncommitted draft'),
      el('div', { class: 'provenance-row' }, pill(`${surfaces.length} surfaces`, 'lav'), pill(`${memoryScene.memoryNodes().filter((m) => m.parent_id === node.id).length} memories`, 'muted')),
      el('label', { class: 'label', for: 'nTitle' }, 'Cloud title'), title, el('label', { class: 'label', for: 'nContent' }, 'Cloud description'), content,
      el('label', { class: 'label' }, 'Draft position (x, y, z)'), xyz.wrap,
      el('p', { class: 'help' }, 'Click the cloud once to select it, then drag it to move the cloud with every surface and memory inside. Option-drag moves it immediately.'),
      el('div', { class: 'node-actions' }, el('button', { class: 'btn btn-sm', onclick: () => memoryScene?.focusCloud(node.id) }, 'Focus cloud'), del));
    title.oninput = () => { node.title = title.value; markDirty('cloud title', node); };
    content.oninput = () => { node.summary = content.value; node.content = content.value; markDirty('cloud text', node); };
    del.onclick = async () => { if (memoryScene.nodes.some((n) => n.parent_id === node.id)) { toast('Move or stage-delete this cloud’s surfaces and memories first.', 'danger'); return; } if (!(await confirmDialog({ title: `Stage deletion of "${node.title}"?`, body: 'Nothing is deleted until commit.', confirmLabel: 'Stage delete', danger: true }))) return; memoryDeletedIds.add(node.id); memoryScene.removeDraftNode(node.id); markDirty('delete', node); showNode(null); };
  }
  function showNode(node) {
    const panel = $('#nodePanel');
    if (!node) { panel.innerHTML = ''; panel.appendChild(el('p', { class: 'muted small' }, 'Select a surface memory to edit its distilled meaning, an in-depth point to read the question and answer behind it, or a cloud to move a whole region.')); return; }
    if (node.node_type === 'root') { panel.innerHTML = ''; panel.append(el('div', { class: 'node-kind' }, 'Root node'), el('p', { class: 'muted small' }, 'The PODA root is protected.')); return; }
    if (node.node_type === 'memory') renderMemoryDetail(node); else if (node.node_type === 'surface') renderSurfaceEditor(node); else renderCloudEditor(node);
  }

  // ----- Search backed by the full database -----
  function searchParams() {
    const p = new URLSearchParams();
    p.set('q', $('#memorySearchInput').value.trim()); p.set('limit', '120');
    for (const [id, key] of [['memorySearchKind', 'kind'], ['memorySearchSpeaker', 'speaker'], ['memorySearchCloud', 'parent_id'], ['memorySearchSince', 'since'], ['memorySearchUntil', 'until']]) { const v = $(`#${id}`).value; if (v) p.set(key, key === 'until' ? `${v}T23:59:59` : v); }
    return p;
  }
  async function runSearch() {
    const meta = $('#memorySearchMeta'), list = $('#memorySearchResults'); const q = $('#memorySearchInput').value.trim();
    const anyFilter = ['memorySearchKind', 'memorySearchSpeaker', 'memorySearchCloud', 'memorySearchSince', 'memorySearchUntil'].some((id) => $(`#${id}`).value);
    if (!q && !anyFilter) { memorySearchMatches = []; memorySearchIndex = -1; meta.textContent = 'Searches title, contents, reference triggers, tags, and provenance across the whole database, including points hidden inside surfaces or by level of detail.'; list.innerHTML = ''; return; }
    try {
      const r = await api(`/memory/search?${searchParams()}`);
      memorySearchMatches = r.results || [];
    } catch (err) {
      // Fallback: local draft search
      const tokens = q.toLowerCase().split(/\s+/).filter(Boolean);
      memorySearchMatches = (memoryScene?.nodes || []).filter((n) => n.node_type === 'memory' || n.node_type === 'surface').filter((n) => { const h = [n.title, n.summary, n.content, n.reference_triggers, (n.tags || []).join(' '), n.speaker, n.memory_kind, n.user_text, n.assistant_text].filter(Boolean).join(' ').toLowerCase(); return tokens.every((t) => h.includes(t)); });
      meta.textContent = `Server search unavailable (${errorDetail(err).message}); showing local draft matches.`;
    }
    if (memorySearchMatches.length && (memorySearchIndex < 0 || memorySearchIndex >= memorySearchMatches.length)) memorySearchIndex = 0;
    if (!memorySearchMatches.length) memorySearchIndex = -1;
    renderSearchList();
  }
  function renderSearchList() {
    const meta = $('#memorySearchMeta'), list = $('#memorySearchResults');
    meta.textContent = memorySearchMatches.length ? `${memorySearchMatches.length} matching memories · ${memorySearchIndex + 1}/${memorySearchMatches.length}` : 'No matching memories';
    list.innerHTML = '';
    memorySearchMatches.slice(0, 120).forEach((node, i) => {
      const inScene = memoryScene?.nodeMap.has(node.id);
      const kind = node.memory_kind === 'conversation' ? `${node.speaker || 'chat'} conversation` : node.memory_kind === 'exchange' ? 'question and answer' : (node.memory_kind || 'memory');
      list.appendChild(el('button', { type: 'button', class: `memory-search-result ${i === memorySearchIndex ? 'active' : ''}`, role: 'option', 'aria-selected': i === memorySearchIndex ? 'true' : 'false', onclick: () => focusSearchResult(i) },
        el('span', {}, node.title || 'Untitled'), el('span', { class: 'search-result-kind' }, el('span', { class: 'tier' }, tierPill(node)), `${kind}${inScene ? '' : ' · '}`, inScene ? null : el('span', { class: 'offscreen' }, 'not in current scene'))));
    });
  }
  function focusSearchResult(index) {
    if (!memorySearchMatches.length) return;
    memorySearchIndex = (index + memorySearchMatches.length) % memorySearchMatches.length;
    const node = memorySearchMatches[memorySearchIndex];
    if (!memoryScene?.focusNode(node.id)) { showNode({ ...node, node_type: node.node_type === 'surface' ? 'surface' : 'memory' }); toast('This memory is stored but not rendered in the current scene (level of detail or transient). Showing its record.', '', 5000); }
    renderSearchList();
    $('#memorySearchResults .memory-search-result.active')?.scrollIntoView({ block: 'nearest' });
  }

  function openTiersHelp() {
    const dlg = el('dialog', { class: 'tiers-help', 'aria-label': 'How memory tiers work' });
    const close = el('button', { class: 'btn btn-primary' }, 'Got it');
    dlg.append(el('div', { class: 'dialog-body' }, el('h2', {}, 'How the two tiers work'),
      el('dl', {},
        el('dt', {}, 'Surface memory'), el('dd', {}, 'A distilled core meaning stored in the vector index. PODA reads these first to answer most questions without the full detail.'),
        el('dt', {}, 'In-depth memory'), el('dd', {}, 'The full question and answer pairs behind each surface. PODA opens them only when it needs more context or when Deep think is on.'),
        el('dt', {}, 'Unified context'), el('dd', {}, 'An in-depth point’s cosine score is blended with its surface’s score, and surfaces influence each other, so related exchanges rise together.'),
        el('dt', {}, 'Committed XYZ'), el('dd', {}, 'Moving and committing a point or surface changes only the spatial relevance signal. It never rewrites the cosine similarity between texts; editing text does.'),
        el('dt', {}, 'Hidden toggle'), el('dd', {}, 'Switch surfaces to Hidden to see every in-depth point at once, exactly as the classic view.')),
      el('p', { class: 'faint small' }, 'Zoom close to a surface, hover it, select it, or jump from search to reveal the memories inside.')),
      el('div', { class: 'dialog-actions' }, close));
    document.body.appendChild(dlg); dlg.showModal();
    const done = () => { dlg.close(); dlg.remove(); };
    close.onclick = done; dlg.oncancel = done;
  }

  async function loadMemoryViewer() {
    setStatus('LOADING OPEN3D SCENE…', 'busy');
    const data = await api('/memory/open3d/scene');
    const tiers = data.tiers || {};
    $('#rendererBadge').textContent = `OPEN3D ${data.open3d?.version || ''} · VOLUMETRIC CLOUDS · ${tiers.surfaces !== undefined ? 'TWO-TIER WEB' : 'PERSISTENT POINT WEB'} · GEOMETRY COSINE · ${data.semantic_layout?.mode || ''}`;
    if (memoryScene) memoryScene.destroy();
    memoryDraftDirty = false; memoryDeletedIds = new Set(); memoryDissolve = false;
    $('#commitMemoryChanges').disabled = true; setStatus('NO PENDING CHANGES', 'clean');
    memoryScene = new Open3DMemoryWeb($('#memoryGL'), $('#memoryLabels'), data, showNode, markDirty);
    window.__podaScene = memoryScene;
    $$('.density-seg button').forEach((b) => b.setAttribute('aria-pressed', b.dataset.density === memoryScene.density ? 'true' : 'false'));
    $$('.surface-seg button').forEach((b) => b.setAttribute('aria-pressed', (b.dataset.surfaces === 'shown') === memoryScene.surfacesShown ? 'true' : 'false'));
    const cloudSel = $('#memorySearchCloud'); cloudSel.replaceChildren(el('option', { value: '' }, 'any cloud'), ...memoryScene.domainNodes().map((d) => el('option', { value: d.id }, d.title)));
    api('/memory/surfaces').then((r) => { surfaceList = r.surfaces || r.results || null; }).catch(() => { surfaceList = null; });
    showNode(null); runSearch();
    const focus = new URLSearchParams(location.hash.replace(/^#/, '')).get('focus');
    if (focus) { setTimeout(() => { if (!memoryScene.focusNode(focus)) toast('Linked memory is not in the rendered scene.', 'danger'); }, 120); }
  }

  $('#backToPoda').onclick = async () => { if (memoryDraftDirty && !(await confirmDialog({ title: 'Discard uncommitted memory changes?', body: 'Returning to chat abandons the current draft.', confirmLabel: 'Discard and return', danger: true }))) return; window.location.assign('/'); };
  $('#overviewMemory').onclick = () => memoryScene?.overview();
  $('#resetCamera').onclick = () => memoryScene?.resetCamera();
  $('#tiersHelp').onclick = openTiersHelp;
  $('#discardDraft').onclick = async () => { if (!memoryDraftDirty || (await confirmDialog({ title: 'Discard every uncommitted change?', confirmLabel: 'Discard draft', danger: true }))) loadMemoryViewer().catch(showFatal); };
  $$('.density-seg button').forEach((b) => { b.onclick = () => { $$('.density-seg button').forEach((x) => x.setAttribute('aria-pressed', 'false')); b.setAttribute('aria-pressed', 'true'); memoryScene?.setDensity(b.dataset.density); }; });
  $$('.surface-seg button').forEach((b) => { b.onclick = () => memoryScene?.setSurfacesShown(b.dataset.surfaces === 'shown'); });
  $('#addCloud').onclick = async () => {
    const title = await promptDialog({ title: 'New memory cloud', label: 'Cloud name', value: 'New Cloud' }); if (!title) return;
    const c = memoryScene?.camera.target || [0, 0, 0];
    const node = { id: `draft-${crypto.randomUUID()}`, parent_id: 'root-personal-organization', title, node_type: 'domain', summary: '', content: '', reference_triggers: '', tags: ['manual-cloud'], x: c[0] + 30, y: c[1] + 18, z: c[2], pinned: true, importance: 0.72, position_source: 'draft' };
    memoryScene.addDraftNode(node); markDirty('add cloud', node);
  };
  $('#addMemoryNode').onclick = async () => {
    let parent = memoryScene?.focusedCloudId; const selected = memoryScene?.nodeMap.get(memoryScene?.selectedId); if (selected?.node_type === 'domain') parent = selected.id; if (selected?.node_type === 'surface') parent = selected.parent_id; if (!parent) parent = 'seed-personal-memory';
    const cloud = memoryScene?.nodeMap.get(parent) || memoryScene?.nodeMap.get('seed-personal-memory');
    const text = await promptDialog({ title: 'New durable memory', body: `Cloud: ${cloud?.title || 'Personal Memory'}${selected?.node_type === 'surface' ? ` · surface: ${selected.title}` : ''}`, label: 'Memory contents', multiline: true }); if (text === null || !text.trim()) return;
    const anchor = selected?.node_type === 'surface' ? selected : cloud;
    const node = { id: `draft-${crypto.randomUUID()}`, parent_id: cloud?.id || 'seed-personal-memory', surface_id: selected?.node_type === 'surface' ? selected.id : null, title: text.trim().slice(0, 72), node_type: 'memory', summary: text.trim().replace(/\s+/g, ' ').slice(0, 420), content: text.trim(), reference_triggers: '', tags: ['manual'], memory_kind: 'durable', tier: 'durable', x: (anchor?.x || 0) + 12, y: (anchor?.y || 0) + 8, z: (anchor?.z || 0) + 6, pinned: true, importance: 0.55, position_source: 'draft' };
    memoryScene.addDraftNode(node); markDirty('add node', node);
  };
  $('#memorySearchInput').addEventListener('input', () => { memorySearchIndex = 0; clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 180); });
  ['memorySearchKind', 'memorySearchSpeaker', 'memorySearchCloud', 'memorySearchSince', 'memorySearchUntil'].forEach((id) => $(`#${id}`).addEventListener('change', () => { memorySearchIndex = 0; runSearch(); }));
  $('#memorySearchPrev').onclick = () => focusSearchResult(memorySearchIndex - 1);
  $('#memorySearchNext').onclick = () => focusSearchResult(memorySearchIndex + 1);
  $('#memorySearchClear').onclick = () => { $('#memorySearchInput').value = ''; ['memorySearchKind', 'memorySearchSpeaker', 'memorySearchCloud', 'memorySearchSince', 'memorySearchUntil'].forEach((id) => { $(`#${id}`).value = ''; }); runSearch(); };
  $('#commitMemoryChanges').onclick = async () => {
    if (!memoryDraftDirty) return;
    const button = $('#commitMemoryChanges'); button.disabled = true; button.textContent = 'COMMITTING…'; setStatus('COMMITTING ATOMIC MEMORY TRANSACTION…', 'busy');
    try {
      const body = { nodes: memoryScene.draftNodes(), deleted_ids: [...memoryDeletedIds], note: 'memory viewer commit' };
      if (memoryDissolve) body.dissolve = true;
      const out = await api('/memory/transaction/commit', { method: 'POST', body });
      setStatus(`COMMIT COMPLETE · ${out.nodes} NODES · ${out.deleted} DELETED · ${out.text_embeddings_refreshed} RE-EMBEDDED`, 'clean'); button.textContent = 'COMMIT MEMORY CHANGES';
      toast(`Committed transaction ${String(out.transaction_id || '').slice(0, 8)}`, 'ok');
      await loadMemoryViewer();
    } catch (err) { button.disabled = false; button.textContent = 'COMMIT MEMORY CHANGES'; setStatus('COMMIT FAILED · DRAFT STILL LOCAL', 'error'); toast(`Commit failed, nothing was applied: ${errorDetail(err).message}`, 'danger', 8000); }
  };
  function showFatal(err) { const f = $('#viewerFatal'); f.textContent = `OPEN3D MEMORY VIEWER ERROR: ${errorDetail(err).message}`; f.hidden = false; setStatus('VIEWER FAILED', 'error'); }
  window.addEventListener('beforeunload', (e) => { if (memoryDraftDirty) { e.preventDefault(); e.returnValue = ''; } });
  window.addEventListener('hashchange', () => { const focus = new URLSearchParams(location.hash.replace(/^#/, '')).get('focus'); if (focus && memoryScene) memoryScene.focusNode(focus); });

  loadBuild();
  loadMemoryViewer().catch(showFatal);
})();
