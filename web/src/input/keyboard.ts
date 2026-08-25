/**
 * Keymap (plan §10): ↑/↓ variant · PgUp/PgDn ±10 · Home/End · Space · ←/→ ±5 s (Shift ±30 s) ·
 * X stop · V favorite · L loop · M mute · / search · Esc · [ ] song · P pin A · Tab A/B ·
 * F filters · Shift+F full screen · T theme · D debug · S settings · ? keymap.
 * ↑/↓ go through the InputPolicy (with e.repeat); everything else bypasses it.
 */
export interface KeyActions {
  step(delta: number, repeat: boolean, at: number): void;
  stepEnd(at: number): void;
  page(delta: number, at: number): void;
  home(at: number): void;
  end(at: number): void;
  toggle(): void;
  stop(): void;
  skip(seconds: number): void;
  loop(): void;
  mute(): void;
  favorite(): void;
  focusSearch(): void;
  escape(): void;
  song(delta: number): void;
  pinA(): void;
  ab(): void;
  filters(): void;
  fullscreen(): void;
  theme(): void;
  debug(): void;
  keymap(): void;
  settings(): void;
}

export const KEYMAP: [string, string][] = [
  ['↑ / ↓', 'previous / next variant (hold to scrub)'],
  ['PgUp / PgDn', '±10 variants'],
  ['Home / End', 'first / last variant'],
  ['Space', 'play / pause'],
  ['X', 'stop and rewind'],
  ['← / →', '−5 s / +5 s (Shift: ±30 s)'],
  ['L', 'loop'],
  ['M', 'mute'],
  ['V', 'favorite / unfavorite current variant'],
  ['/', 'search'],
  ['Esc', 'close / clear'],
  ['[ / ]', 'previous / next song'],
  ['P', 'pin current variant as A'],
  ['Tab', 'A/B with the pinned variant'],
  ['F', 'filters'],
  ['Shift + F', 'full screen'],
  ['T', 'theme'],
  ['D', 'debug panel'],
  ['S', 'settings'],
  ['?', 'this keymap'],
];

export function installKeyboard(target: Window, a: KeyActions): () => void {
  const isTyping = (e: KeyboardEvent) => {
    const t = e.target as HTMLElement | null;
    return !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
  };
  const down = (e: KeyboardEvent) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (isTyping(e)) {
      if (e.key === 'Escape') {
        a.escape();
      }
      return;
    }
    const at = e.timeStamp || performance.now();
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        a.step(1, e.repeat, at);
        return;
      case 'ArrowUp':
        e.preventDefault();
        a.step(-1, e.repeat, at);
        return;
      case 'PageDown':
        e.preventDefault();
        if (!e.repeat) a.page(1, at);
        return;
      case 'PageUp':
        e.preventDefault();
        if (!e.repeat) a.page(-1, at);
        return;
      case 'Home':
        e.preventDefault();
        a.home(at);
        return;
      case 'End':
        e.preventDefault();
        a.end(at);
        return;
      case ' ':
        e.preventDefault();
        if (!e.repeat) a.toggle();
        return;
      case 'ArrowLeft':
        e.preventDefault();
        a.skip(e.shiftKey ? -30 : -5);
        return;
      case 'ArrowRight':
        e.preventDefault();
        a.skip(e.shiftKey ? 30 : 5);
        return;
      case 'Tab':
        e.preventDefault();
        if (!e.repeat) a.ab();
        return;
      case 'Escape':
        a.escape();
        return;
      default:
        break;
    }
    if (e.repeat) return;
    switch (e.key.toLowerCase()) {
      case 'l':
        a.loop();
        break;
      case 'm':
        a.mute();
        break;
      case 'v':
        a.favorite();
        break;
      case 'x':
        a.stop();
        break;
      case '/':
        e.preventDefault();
        a.focusSearch();
        break;
      case '[':
        a.song(-1);
        break;
      case ']':
        a.song(1);
        break;
      case 'p':
        a.pinA();
        break;
      case 'f':
        // Shift is the only modifier the keymap uses: F opens the filters, Shift+F goes full screen
        if (e.shiftKey) a.fullscreen();
        else a.filters();
        break;
      case 't':
        a.theme();
        break;
      case 'd':
        a.debug();
        break;
      case 's':
        a.settings();
        break;
      case '?':
        a.keymap();
        break;
      default:
        break;
    }
  };
  const up = (e: KeyboardEvent) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') a.stepEnd(e.timeStamp || performance.now());
  };
  const blur = () => a.stepEnd(performance.now());
  target.addEventListener('keydown', down);
  target.addEventListener('keyup', up);
  target.addEventListener('blur', blur);
  return () => {
    target.removeEventListener('keydown', down);
    target.removeEventListener('keyup', up);
    target.removeEventListener('blur', blur);
  };
}
