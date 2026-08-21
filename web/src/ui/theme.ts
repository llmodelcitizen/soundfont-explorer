/** Theme: dark (default) / win95 / system via data-theme on <html>, persisted in localStorage + ?theme=. */
export type ThemeName = 'dark' | 'win95' | 'system';
const KEY = 'sfp.theme';

export function readTheme(fromUrl?: string): ThemeName {
  const v = fromUrl ?? safeGet();
  return v === 'win95' || v === 'system' || v === 'dark' ? v : 'dark';
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
  if (t === 'system') {
    const light = typeof matchMedia !== 'undefined' && matchMedia('(prefers-color-scheme: light)').matches;
    root.dataset.theme = light ? 'win95' : 'dark';
  } else {
    root.dataset.theme = t;
  }
  root.dataset.themeChoice = t;
  try {
    localStorage.setItem(KEY, t);
  } catch {
    /* private mode */
  }
}

export function nextTheme(t: ThemeName): ThemeName {
  return t === 'dark' ? 'win95' : 'dark';
}
