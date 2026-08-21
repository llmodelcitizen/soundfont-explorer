// AudioWorklet tap for ab-switch.html.
// inputs[0] = variant A audio, inputs[1] = variant B audio  -> summed to the output (what you hear)
// inputs[2] = DC mirror of A's switch gain, inputs[3] = DC mirror of B's switch gain (never audible)
// Per 128-frame block it records: rmsA, rmsB, maxAbsFirstDiff(mix), maxAbsSecondDiff(mix), gA, gB, maxAbsGainStep, blockIndex
// (blockIndex = (currentFrame - startFrame) / 128, so skipped render quanta are visible instead of shifting the timeline)
// and posts Float32 logs to the main thread every ~1 s.
class ABTap extends AudioWorkletProcessor {
  constructor() {
    super();
    this.startFrame = null;
    this.rows = [];
    this.prev1 = [0, 0]; // last sample of previous block per channel
    this.prev2 = [0, 0];
    this.blocks = 0;
    this.port.onmessage = e => { if (e.data === 'flush') this.flush(true); };
  }
  flush(final) {
    if (this.rows.length || final) {
      const arr = new Float32Array(this.rows.length * 8);
      for (let i = 0; i < this.rows.length; i++) arr.set(this.rows[i], i * 8);
      this.port.postMessage({ startFrame: this.startFrame, firstBlock: this.blocks - this.rows.length, rows: arr, final: !!final, sampleRate,
                              nowFrame: currentFrame, blocks: this.blocks }, [arr.buffer]);
      this.rows = [];
    }
  }
  process(inputs, outputs) {
    if (this.startFrame === null) this.startFrame = currentFrame;
    const out = outputs[0];
    const a = inputs[0], b = inputs[1], ga = inputs[2], gb = inputs[3];
    const n = out[0] ? out[0].length : 128;
    let sa = 0, sb = 0, na = 0, nb = 0, d1 = 0, d2 = 0;
    for (let c = 0; c < out.length; c++) {
      const o = out[c];
      const ac = a[c] || a[0], bc = b[c] || b[0];
      let p1 = this.prev1[c] || 0, p2 = this.prev2[c] || 0;
      for (let i = 0; i < n; i++) {
        const va = ac ? ac[i] : 0, vb = bc ? bc[i] : 0;
        const m = va + vb;
        o[i] = m;
        sa += va * va; sb += vb * vb;
        const f = Math.abs(m - p1); if (f > d1) d1 = f;
        const s = Math.abs(m - 2 * p1 + p2); if (s > d2) d2 = s;
        p2 = p1; p1 = m;
      }
      this.prev1[c] = p1; this.prev2[c] = p2;
      na += n; nb += n;
    }
    // per-sample gain steps on the DC mirrors (a switch click would show up here as a step >> 1/480)
    let gs = 0;
    for (const [g, key] of [[ga[0], 'pa'], [gb[0], 'pb']]) {
      let prev = this[key] || 0;
      if (g) { for (let i = 0; i < n; i++) { const d = Math.abs(g[i] - prev); if (d > gs) gs = d; prev = g[i]; } }
      this[key] = prev;
    }
    const gav = ga[0] ? ga[0][n - 1] : 0, gbv = gb[0] ? gb[0][n - 1] : 0;
    this.rows.push([Math.sqrt(sa / Math.max(1, na)), Math.sqrt(sb / Math.max(1, nb)), d1, d2, gav, gbv, gs, (currentFrame - this.startFrame) / 128]);
    this.blocks++;
    if (this.rows.length >= 375) this.flush(false);
    return true;
  }
}
registerProcessor('ab-tap', ABTap);
