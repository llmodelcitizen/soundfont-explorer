/** Transport: ◀◀ ▶ ▶▶, clock, seek, loop, volume, mute. Sliders blur back to the list (MVP lesson). */
import { POLICY } from '../config';
import { fmtTime, h, setPressed } from './dom';
import { ICONS, setIcon } from './icons';

/**
 * What the transport should show for a (possibly suspended) AudioContext this frame.
 *
 * A suspended context — an iOS interruption, a tab the browser froze — gets a 'tap to resume'
 * notice in place of the engine's own status. The engine only repaints its status when the
 * status changes, so the frame the context comes back has to take the notice down itself:
 * without that edge, a context that resumed without an engine event left 'tap to resume' on
 * screen for the rest of the session while audio played underneath it.
 *
 * `was` is the `suspended` this returned for the previous frame.
 */
export function resumeNotice(state: string, playing: boolean, was: boolean): { suspended: boolean; notice: string | null; restore: boolean } {
  const suspended = state !== 'running' && playing;
  return { suspended, notice: suspended ? `audio ${state} — tap to resume` : null, restore: !suspended && was };
}

export interface TransportCallbacks {
  onToggle(): void;
  onStop(): void;
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
  private clockCurrentChars: HTMLElement[];
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
    const stop = h('button', { class: 'btn stop', type: 'button', title: 'stop and rewind (X)', 'aria-label': 'stop and rewind' });
    setIcon(stop, 'stop');
    stop.addEventListener('click', () => {
      cb.onStop();
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
    const durationText = fmtTime(duration);
    const clockChars = Math.max(fmtTime(0).length, durationText.length);
    const makeClockTime = (text: string, className: string) => {
      const padded = text.padStart(clockChars);
      const chars = Array.from(padded, (char) => h('span', { class: 'clock-char' }, char === ' ' ? '' : char));
      return { el: h('span', { class: `clock-time ${className}` }, chars), chars };
    };
    const current = makeClockTime(fmtTime(0), 'clock-current');
    const total = makeClockTime(durationText, 'clock-duration');
    this.clockCurrentChars = current.chars;
    this.clock = h(
      'span',
      { class: 'clock' },
      current.el,
      h('span', { class: 'clock-separator' }, ' / '),
      total.el,
    );
    // Only the Win95 stylesheet consumes this. Reserve enough character cells for either
    // time field before playback starts; its bitmap face has proportional digits.
    this.clock.style.setProperty('--clock-field-width', `${clockChars}ch`);
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
      stop,
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
    const currentText = fmtTime(pos).padStart(this.clockCurrentChars.length);
    this.clockCurrentChars.forEach((char, index) => {
      const value = currentText[index];
      char.textContent = value === ' ' ? '' : (value ?? '');
    });
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
