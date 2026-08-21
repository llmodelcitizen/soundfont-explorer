// Classic Worker: runs the whole ogg-opus-decoder (Ogg parsing + libopus WASM) off the main thread.
// Message in:  { id, bytes: ArrayBuffer }   (transferred)
// Message out: { id, ms, samplesDecoded, sampleRate, channelData: Float32Array[], errors }  (channelData transferred)
// This is the shape the real client's audio/decode/worker.ts will take: the library's own
// OggOpusDecoderWebWorker keeps the Ogg demux on the main thread and cannot accept queued decodeFile() calls.
importScripts('vendor/ogg-opus-decoder.min.js');
const lib = self['ogg-opus-decoder'];
const decoder = new lib.OggOpusDecoder();
const ready = decoder.ready;
let chain = ready; // serialize jobs on this worker
self.onmessage = ({ data: { id, bytes } }) => {
  chain = chain.then(async () => {
    const t0 = performance.now();
    try {
      const r = await decoder.decodeFile(new Uint8Array(bytes));
      const out = { id, ms: performance.now() - t0, samplesDecoded: r.samplesDecoded, sampleRate: r.sampleRate,
                    channelData: r.channelData, errors: r.errors.length };
      self.postMessage(out, r.channelData.map(c => c.buffer));
    } catch (e) {
      self.postMessage({ id, ms: performance.now() - t0, error: String(e && e.message || e) });
    }
  });
};
ready.then(() => self.postMessage({ id: -1, ready: true }));
