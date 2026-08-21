/** Transport: ◀◀ ▶ ▶▶, clock, seek, loop, volume, mute. Sliders blur back to the list (MVP lesson). */
import { POLICY } from '../config';
import { fmtTime, h, setPressed } from './dom';
import { ICONS, setIcon } from './icons';

export interface TransportCallbacks {
  onToggle(): void;
  onSeek(pos: number): void;
  onSkip(delta: number): void;
  onLoop(on: boolean): void;
  onVolume(v: number): void;
  onMute(): void;
  onStep(delta: number, repeat: boolean): void;
  onStepEnd(): void;
}

export class Transport {
  readonly el: HTMLElement;
  private playBtn: HTMLButtonElement;
  private clock: HTMLElement;
  private seek: HTMLInputElement;
  private loopBtn: HTMLButtonElement;
  private muteBtn: HTMLButtonElement;
  private vol: HTMLInputElement;
  private status: HTMLElement;
  private seeking = false;
  private lastSeekEmit = 0;
  private repeatTimer: ReturnType<typeof setInterval> | null = null;

  constructor(private readonly duration: number, private cb: TransportCallbacks, private returnFocus: () => void) {
    this.playBtn = h('button', { class: 'btn play', type: 'button', title: 'play/pause (Space)', 'aria-label': 'play/pause' });
    setIcon(this.playBtn, 'play');
    this.playBtn.addEventListener('click', () => {
      cb.onToggle();
      returnFocus();
    });
    const back = h('button', { class: 'btn', type: 'button', title: '−5 s (←)', 'aria-label': 'back 5 seconds' });
    back.innerHTML = ICONS.back;
    back.addEventListener('click', () => {
      cb.onSkip(-5);
      returnFocus();
    });
    const fwd = h('button', { class: 'btn', type: 'button', title: '+5 s (→)', 'aria-label': 'forward 5 seconds' });
    fwd.innerHTML = ICONS.fwd;
    fwd.addEventListener('click', () => {
      cb.onSkip(5);
      returnFocus();
    });
    this.clock = h('span', { class: 'clock' }, `${fmtTime(0)} / ${fmtTime(duration)}`);
    this.seek = h('input', { type: 'range', class: 'seek', min: '0', max: String(duration), step: '0.01', value: '0', 'aria-label': 'position' });
    this.seek.addEventListener('pointerdown', () => (this.seeking = true));
    this.seek.addEventListener('input', () => {
      const now = performance.now();
      if (now - this.lastSeekEmit > 80) {
        this.lastSeekEmit = now;
        cb.onSeek(Number(this.seek.value));
      }
    });
    this.seek.addEventListener('change', () => {
      this.seeking = false;
      cb.onSeek(Number(this.seek.value));
      returnFocus();
    });
    this.loopBtn = h('button', { class: 'btn toggle', type: 'button', title: 'loop (L)', 'aria-pressed': 'false' }, 'loop');
    this.loopBtn.addEventListener('click', () => {
      const on = this.loopBtn.getAttribute('aria-pressed') !== 'true';
      this.setLoop(on);
      cb.onLoop(on);
      returnFocus();
    });
    this.muteBtn = h('button', { class: 'btn toggle', type: 'button', title: 'mute (M)', 'aria-pressed': 'false' }, 'mute');
    this.muteBtn.addEventListener('click', () => {
      cb.onMute();
      returnFocus();
    });
    this.vol = h('input', { type: 'range', class: 'vol', min: '0', max: '1', step: '0.01', value: '1', 'aria-label': 'volume' });
    this.vol.addEventListener('input', () => cb.onVolume(Number(this.vol.value)));
    this.vol.addEventListener('change', () => returnFocus());
    this.status = h('span', { class: 'status' }, '');
    // touch ▲/▼ repeat buttons (same repeat rate as a held key, through the same policy)
    const mkStep = (delta: number, icon: 'up' | 'down') => {
      const b = h('button', { class: 'btn step', type: 'button', title: delta < 0 ? 'previous variant (↑)' : 'next variant (↓)', 'aria-label': delta < 0 ? 'previous variant' : 'next variant' });
      setIcon(b, icon);
      const start = (e: Event) => {
        e.preventDefault();
        cb.onStep(delta, false);
        this.stopRepeat();
        this.repeatTimer = setInterval(() => cb.onStep(delta, true), POLICY.touchRepeatMs);
      };
      const stop = () => {
        if (this.repeatTimer) {
          this.stopRepeat();
          cb.onStepEnd();
        }
      };
      b.addEventListener('pointerdown', start);
      b.addEventListener('pointerup', stop);
      b.addEventListener('pointercancel', stop);
      b.addEventListener('pointerleave', stop);
      return b;
    };
    this.el = h(
      'div',
      { class: 'transport' },
      h('div', { class: 'steps' }, mkStep(-1, 'up'), mkStep(1, 'down')),
      back,
      this.playBtn,
      fwd,
      this.loopBtn,
      this.muteBtn,
      h('span', { class: 'break', 'aria-hidden': 'true' }),
      this.clock,
      this.seek,
      this.vol,
      this.status,
    );
  }

  private stopRepeat(): void {
    if (this.repeatTimer) clearInterval(this.repeatTimer);
    this.repeatTimer = null;
  }

  update(pos: number, playing: boolean): void {
    this.clock.textContent = `${fmtTime(pos)} / ${fmtTime(this.duration)}`;
    if (!this.seeking) this.seek.value = String(pos);
    const want = playing ? 'pause' : 'play';
    if (this.playBtn.dataset.icon !== want) {
      setIcon(this.playBtn, want);
      this.playBtn.dataset.icon = want;
    }
  }

  setLoop(on: boolean): void {
    setPressed(this.loopBtn, on);
  }

  setMuted(on: boolean): void {
    setPressed(this.muteBtn, on);
  }

  setVolume(v: number): void {
    this.vol.value = String(v);
  }

  setStatus(text: string, kind = ''): void {
    this.status.textContent = text;
    this.status.className = `status ${kind}`;
  }
}
