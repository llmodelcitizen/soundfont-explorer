/** Explicit themes applied through data-theme on <html>, persisted in localStorage + ?theme=. */
import { safeStorage } from '../state/storage';

export type ThemeName = 'modern' | 'win95' | 'amiga';
export const DEFAULT_THEME: ThemeName = 'modern';
const KEY = 'sfp.theme';

export function readTheme(fromUrl?: string | null): ThemeName {
  const v = fromUrl || safeStorage.get(KEY);
  if (v === 'win95' || v === 'amiga') return v;
  return DEFAULT_THEME; // includes legacy 'dark' / 'system' values
}

export function applyTheme(t: ThemeName): void {
  const root = document.documentElement;
  root.dataset.theme = t;
  safeStorage.set(KEY, t);
}

export function nextTheme(t: ThemeName): ThemeName {
  if (t === 'modern') return 'win95';
  if (t === 'win95') return 'amiga';
  return 'modern';
}
