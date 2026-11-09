/* Off-main-thread edge builder for the memory web. Loads both as a Web Worker and as a page script
   (window.PODAEdges) so tiny scenes can build synchronously with the exact same algorithm.

   Input: packed node positions plus, for the two-tier view, which nodes are surfaces, which surface each
   in-depth member belongs to, and how revealed each surface currently is (0..1). Output: packed GL line buffers. */
'use strict';

(function (root) {
  const KNN_DEFAULT = 12;

  function dist(p, i, j) {
    const dx = p[i * 3] - p[j * 3], dy = p[i * 3 + 1] - p[j * 3 + 1], dz = p[i * 3 + 2] - p[j * 3 + 2];
    return Math.sqrt(dx * dx + dy * dy + dz * dz);
  }
  // pairs among a list of node indices: complete graph, or k-nearest when the list is large / mode asks for it
  function pairsAmong(p, idx, mode, k, forceComplete) {
    const out = [];
    const n = idx.length;
    if (n < 2) return out;
    const complete = forceComplete || mode === 'complete';
    if (complete) {
      for (let a = 0; a < n; a++) for (let b = a + 1; b < n; b++) out.push(idx[a], idx[b], dist(p, idx[a], idx[b]));
      return out;
    }
    const kk = mode === 'minimal' ? Math.min(4, k) : k;
    const seen = new Set();
    for (let a = 0; a < n; a++) {
      const order = [];
      for (let b = 0; b < n; b++) if (b !== a) order.push([dist(p, idx[a], idx[b]), b]);
      order.sort((x, y) => x[0] - y[0]);
      for (const [d, b] of order.slice(0, Math.min(kk, n - 1))) {
        const key = a < b ? a * n + b : b * n + a;
        if (seen.has(key)) continue;
        seen.add(key); out.push(idx[a], idx[b], d);
      }
    }
    return out;
  }
  function sigmaOf(pairs) {
    const ds = [];
    for (let i = 2; i < pairs.length; i += 3) if (pairs[i] > 1e-6) ds.push(pairs[i]);
    ds.sort((a, b) => a - b);
    const median = ds.length ? ds[Math.floor(ds.length * 0.5)] : 90;
    return Math.max(24, median * 0.82);
  }

  /**
   * @param {Float32Array} positions  xyz per node
   * @param {number} count            node count
   * @param {string} mode             'complete' | 'neighbors' | 'minimal'
   * @param {number} k                neighbours for knn modes
   * @param {object} [tiers]          optional two-tier description
   *   tiers.kinds      Uint8Array  0 = surface, 1 = member (in-depth), 2 = free memory (no surface)
   *   tiers.surfaceOf  Int32Array  for members: index of owning surface node, else -1
   *   tiers.reveal     Float32Array per node (only meaningful for surfaces): 0..1 reveal alpha
   */
  function buildEdges(positions, count, mode, k, tiers) {
    k = k || KNN_DEFAULT;
    const segs = []; // [i, j, d, alphaMul, palette]
    if (!tiers) {
      const all = []; for (let i = 0; i < count; i++) all.push(i);
      const pairs = pairsAmong(positions, all, mode, k, false);
      const sigma = sigmaOf(pairs);
      for (let q = 0; q < pairs.length; q += 3) segs.push(pairs[q], pairs[q + 1], pairs[q + 2], 1, 0, sigma);
    } else {
      const { kinds, surfaceOf, reveal } = tiers;
      const surfaces = [], free = [], membersBySurface = new Map();
      for (let i = 0; i < count; i++) {
        if (kinds[i] === 0) surfaces.push(i);
        else if (kinds[i] === 1 && surfaceOf[i] >= 0) { if (!membersBySurface.has(surfaceOf[i])) membersBySurface.set(surfaceOf[i], []); membersBySurface.get(surfaceOf[i]).push(i); }
        else free.push(i);
      }
      // surface web: complete up to 400 surfaces, knn beyond (or when the user picked a sparser mode)
      const sp = pairsAmong(positions, surfaces, mode, k, mode === 'complete' && surfaces.length <= 400);
      const ssig = sigmaOf(sp);
      for (let q = 0; q < sp.length; q += 3) segs.push(sp[q], sp[q + 1], sp[q + 2], 1, 1, ssig);
      // free memories (no surface) keep a classic web among themselves and to nearby surfaces
      const fp = pairsAmong(positions, free, mode, k, mode === 'complete' && free.length <= 400);
      const fsig = sigmaOf(fp);
      for (let q = 0; q < fp.length; q += 3) segs.push(fp[q], fp[q + 1], fp[q + 2], 1, 0, fsig);
      // member webs inside revealed surfaces, faded by the surface reveal alpha
      for (const [s, members] of membersBySurface) {
        const a = reveal ? reveal[s] : 1;
        if (a <= 0.02) continue;
        const mp = pairsAmong(positions, members, mode, k, members.length <= 60);
        const msig = Math.max(6, sigmaOf(mp) * 0.9);
        for (let q = 0; q < mp.length; q += 3) segs.push(mp[q], mp[q + 1], mp[q + 2], a, 0, msig);
        for (const m of members) segs.push(m, s, dist(positions, m, s), a * 0.85, 2, msig);
      }
    }
    const n = segs.length / 6;
    const pos = new Float32Array(n * 6), colors = new Float32Array(n * 6), alphas = new Float32Array(n * 2);
    for (let p = 0; p < n; p++) {
      const i = segs[p * 6], j = segs[p * 6 + 1], d = segs[p * 6 + 2], mul = segs[p * 6 + 3], pal = segs[p * 6 + 4], sigma = segs[p * 6 + 5];
      const strength = Math.exp(-0.5 * Math.pow(d / sigma, 2));
      let alpha = (0.018 + 0.72 * Math.pow(strength, 1.35)) * mul;
      let r, g, b;
      if (pal === 1) { r = 0.42 + 0.40 * strength; g = 0.40 + 0.34 * strength; b = 0.78 + 0.22 * strength; alpha *= 0.9; }       // surface web: lavender
      else if (pal === 2) { r = 0.50 + 0.30 * strength; g = 0.52 + 0.30 * strength; b = 0.90; alpha = (0.05 + 0.45 * strength) * mul; } // spokes to surface
      else { r = 0.10 + 0.66 * strength; g = 0.22 + 0.63 * strength; b = 0.48 + 0.50 * strength; }                               // classic web
      pos[p * 6] = positions[i * 3]; pos[p * 6 + 1] = positions[i * 3 + 1]; pos[p * 6 + 2] = positions[i * 3 + 2];
      pos[p * 6 + 3] = positions[j * 3]; pos[p * 6 + 4] = positions[j * 3 + 1]; pos[p * 6 + 5] = positions[j * 3 + 2];
      colors[p * 6] = r; colors[p * 6 + 1] = g; colors[p * 6 + 2] = b; colors[p * 6 + 3] = r; colors[p * 6 + 4] = g; colors[p * 6 + 5] = b;
      alphas[p * 2] = alpha; alphas[p * 2 + 1] = alpha;
    }
    return { pos, colors, alphas, count: n * 2, edges: n };
  }

  const isWorker = typeof WorkerGlobalScope !== 'undefined' && root instanceof WorkerGlobalScope;
  if (isWorker) {
    root.onmessage = (e) => {
      const { positions, count, mode, k, seq, tiers } = e.data;
      const out = buildEdges(positions, count, mode, k || KNN_DEFAULT, tiers || null);
      root.postMessage({ seq, mode, ...out }, [out.pos.buffer, out.colors.buffer, out.alphas.buffer]);
    };
  } else {
    root.PODAEdges = { buildEdges };
  }
})(typeof self !== 'undefined' ? self : window);
