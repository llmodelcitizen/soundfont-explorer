/// <reference lib="webworker" />
/**
 * WASM Opus decode worker (only used when the native probe fails, e.g. Safari).
 * Messages in: { id, bytes: ArrayBuffer }  → out: { id, channelData: Float32Array[], sampleRate } | { id, error }
 */
import { OggOpusDecoder } from 'ogg-opus-decoder';

let decoder: OggOpusDecoder | null = null;
let ready: Promise<unknown> | null = null;

async function getDecoder(): Promise<OggOpusDecoder> {
  if (!decoder) {
    decoder = new OggOpusDecoder({ forceStereo: true });
    ready = decoder.ready;
  }
  await ready;
  return decoder!;
}

self.onmessage = async (ev: MessageEvent<{ id: number; bytes: ArrayBuffer }>) => {
  const { id, bytes } = ev.data;
  try {
    const d = await getDecoder();
    const { channelData, sampleRate, samplesDecoded } = await d.decodeFile(new Uint8Array(bytes));
    await d.reset();
    const out = channelData.map((c) => c.slice(0, samplesDecoded));
    (self as unknown as Worker).postMessage({ id, channelData: out, sampleRate }, out.map((c) => c.buffer as ArrayBuffer));
  } catch (e) {
    try {
      decoder?.free();
    } catch {
      /* ignore */
    }
    decoder = null;
    (self as unknown as Worker).postMessage({ id, error: String((e as Error)?.message ?? e) });
  }
};
