/**
 * Structural subsets of Web Audio used by the engine, so tests can supply fakes
 * (FakeAudioContext in test/unit/fakes.ts) without jsdom.
 */
export interface ParamLike {
  value: number;
  cancelScheduledValues(t: number): unknown;
  setValueAtTime(v: number, t: number): unknown;
  linearRampToValueAtTime(v: number, t: number): unknown;
}

export interface NodeLike {
  connect(dst: NodeLike | unknown): unknown;
  disconnect(): unknown;
}

export interface GainLike extends NodeLike {
  gain: ParamLike;
}

export interface BufferLike {
  duration: number;
  length: number;
  sampleRate: number;
  numberOfChannels: number;
}

export interface SourceLike extends NodeLike {
  buffer: BufferLike | null;
  start(when?: number, offset?: number, duration?: number): void;
  stop(when?: number): void;
  onended: ((ev?: unknown) => void) | null;
}

export interface ContextLike {
  readonly currentTime: number;
  readonly sampleRate: number;
  readonly baseLatency: number;
  readonly outputLatency?: number;
  readonly state: string;
  readonly destination: NodeLike;
  createGain(): GainLike;
  createBufferSource(): SourceLike;
  createBuffer(channels: number, length: number, sampleRate: number): BufferLike & {
    copyToChannel(src: Float32Array, channel: number): void;
  };
  decodeAudioData(data: ArrayBuffer): Promise<BufferLike>;
  resume?(): Promise<void>;
}

export type Tier = 's' | 'l';

export interface SegKey {
  /** variant id */
  v: string;
  tier: Tier;
  /** slice index within the song for that tier */
  i: number;
}

export const keyStr = (k: SegKey): string => `${k.v}/${k.tier}/${k.i}`;

export const bufferBytes = (b: BufferLike): number => b.length * b.numberOfChannels * 4;
