/**
 * iOS/Safari audio unlocking. Everything here must run synchronously inside a user gesture.
 *
 *  - the AudioContext is created (and resumed) in the gesture's own call stack;
 *  - `navigator.audioSession.type = 'playback'` (WebKit Audio Session API, iOS 17+) routes Web
 *    Audio through the media "playback" category so the ringer/silent switch does not mute it;
 *  - a one-sample silent buffer is started: the classic unlock for older WebKit builds.
 */
export interface AudioSessionLike {
  type: string;
  state?: string;
}

export function audioSession(): AudioSessionLike | null {
  const nav = navigator as unknown as { audioSession?: AudioSessionLike };
  return nav.audioSession ?? null;
}

export function requestPlaybackSession(): string {
  const s = audioSession();
  if (!s) return 'n/a';
  try {
    s.type = 'playback';
    return s.type;
  } catch (e) {
    return `error: ${(e as Error).message}`;
  }
}

let silentEl: HTMLAudioElement | null = null;

/** 1 s of silent 8 kHz mono PCM WAV as a blob URL (CSP allows media-src blob:). */
function silentWavUrl(): string {
  const sr = 8000;
  const n = sr;
  const buf = new ArrayBuffer(44 + n * 2);
  const dv = new DataView(buf);
  const str = (o: number, t: string) => {
    for (let i = 0; i < t.length; i++) dv.setUint8(o + i, t.charCodeAt(i));
  };
  str(0, 'RIFF');
  dv.setUint32(4, 36 + n * 2, true);
  str(8, 'WAVE');
  str(12, 'fmt ');
  dv.setUint32(16, 16, true);
  dv.setUint16(20, 1, true);
  dv.setUint16(22, 1, true);
  dv.setUint32(24, sr, true);
  dv.setUint32(28, sr * 2, true);
  dv.setUint16(32, 2, true);
  dv.setUint16(34, 16, true);
  str(36, 'data');
  dv.setUint32(40, n * 2, true);
  return URL.createObjectURL(new Blob([buf], { type: 'audio/wav' }));
}

/**
 * Pre-iOS-17 fallback: a playing <audio> element puts the page in the media "playback" category,
 * which makes Web Audio audible with the ringer switch on silent. Harmless elsewhere.
 */
export function startSilentMediaElement(): string {
  if (audioSession()) return 'audioSession api present';
  try {
    if (!silentEl) {
      silentEl = document.createElement('audio');
      silentEl.src = silentWavUrl();
      silentEl.loop = true;
      silentEl.setAttribute('playsinline', '');
      silentEl.volume = 0.01;
      silentEl.style.display = 'none';
      document.body.appendChild(silentEl);
    }
    const p = silentEl.play();
    if (p && typeof p.catch === 'function') p.catch(() => undefined);
    return 'started';
  } catch (e) {
    return `error: ${(e as Error).message}`;
  }
}

/** Create the context. Inside a gesture this also unlocks it; outside one it comes up suspended
 *  and installResumeOnGesture() unlocks it on the first pointer/touch/key event. */
export function createContextInGesture(): AudioContext {
  requestPlaybackSession();
  const Ctor = (window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext)!;
  const ctx = new Ctor({ latencyHint: 'interactive' });
  if (navigator.userActivation?.isActive) {
    startSilentMediaElement();
    unlock(ctx);
  }
  return ctx;
}

export function unlock(ctx: AudioContext): void {
  try {
    const buf = ctx.createBuffer(1, 1, ctx.sampleRate);
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    src.start(0);
  } catch {
    /* ignore */
  }
  if (ctx.state !== 'running') void ctx.resume().catch(() => undefined);
}

/** Resume on any later gesture if the context fell asleep (backgrounding, phone call, ringer). */
export function installResumeOnGesture(ctx: AudioContext, onResume?: () => void): () => void {
  const handler = () => {
    if (ctx.state === 'running') return;
    requestPlaybackSession();
    startSilentMediaElement();
    unlock(ctx);
    onResume?.();
  };
  const evs: (keyof WindowEventMap)[] = ['pointerdown', 'touchend', 'keydown'];
  for (const e of evs) window.addEventListener(e, handler, { capture: true, passive: true });
  return () => {
    for (const e of evs) window.removeEventListener(e, handler, { capture: true });
  };
}
