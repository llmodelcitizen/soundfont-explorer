/** Theme: modern (default) / win95 via data-theme on <html>, persisted in localStorage + ?theme=. Explicit only — no 'system'. */
export type ThemeName = 'modern' | 'win95';
export const DEFAULT_THEME: ThemeName = 'modern';
const KEY = 'sfp.theme';

export function readTheme(fromUrl?: string | null): ThemeName {
  const v = fromUrl || safeGet();
  if (v === 'win95') return 'win95';
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
  return t === 'modern' ? 'win95' : 'modern';
}
