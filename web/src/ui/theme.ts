/** Explicit themes applied through data-theme on <html>, persisted in localStorage + ?theme=. */
export type ThemeName = 'modern' | 'win95' | 'amiga';
export const DEFAULT_THEME: ThemeName = 'modern';
const KEY = 'sfp.theme';

export function readTheme(fromUrl?: string | null): ThemeName {
  const v = fromUrl || safeGet();
  if (v === 'win95' || v === 'amiga') return v;
  return DEFAULT_THEME; // includes legacy 'dark' / 'system' values
}

function safeGet(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function applyTheme(t: ThemeName): void {
  const root = document.documentElement;
  root.dataset.theme = t;
  try {
    localStorage.setItem(KEY, t);
  } catch {
    /* private mode */
  }
}

export function nextTheme(t: ThemeName): ThemeName {
  if (t === 'modern') return 'win95';
  if (t === 'win95') return 'amiga';
  return 'modern';
}
