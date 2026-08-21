/**
 * URL ⇄ state: ?song=&v=&t=&f=engine:adlmidi,opnmidi;size:lt2m&q=&theme=&loop=1
 * Facet values are encoded verbatim except ',' ';' ':' which are percent-encoded.
 */
import { FACET_KEYS, type FacetKey, type Selection } from './filterIndex';

export interface UrlState {
  song?: string;
  variant?: string;
  t?: number;
  filters?: Selection;
  q?: string;
  theme?: string;
  loop?: boolean;
}

const encV = (s: string) => encodeURIComponent(s).replace(/%20/g, '+');
const decV = (s: string): string => {
  try {
    return decodeURIComponent(s.replace(/\+/g, ' '));
  } catch {
    return s; // malformed %-escape: keep the raw text, it just won't match anything
  }
};

export function encodeFilters(sel: Selection): string {
  const parts: string[] = [];
  for (const key of FACET_KEYS) {
    const vals = sel[key];
    if (vals && vals.size) parts.push(`${key}:${[...vals].sort().map(encV).join(',')}`);
  }
  return parts.join(';');
}

export function decodeFilters(s: string): Selection {
  const sel: Selection = {};
  if (!s) return sel;
  for (const part of s.split(';')) {
    const i = part.indexOf(':');
    if (i < 0) continue;
    const key = part.slice(0, i) as FacetKey;
    if (!FACET_KEYS.includes(key)) continue;
    const vals = part
      .slice(i + 1)
      .split(',')
      .filter(Boolean)
      .map(decV);
    if (vals.length) sel[key] = new Set(vals);
  }
  return sel;
}

export function parseUrl(search: string = typeof location !== 'undefined' ? location.search : ''): UrlState {
  const p = new URLSearchParams(search);
  const st: UrlState = {};
  if (p.get('song')) st.song = p.get('song')!;
  if (p.get('v')) st.variant = p.get('v')!;
  if (p.get('t')) {
    const t = Number(p.get('t'));
    if (Number.isFinite(t) && t >= 0) st.t = t;
  }
  if (p.get('f')) st.filters = decodeFilters(p.get('f')!);
  if (p.get('q')) st.q = p.get('q')!;
  if (p.get('theme')) st.theme = p.get('theme')!;
  if (p.get('loop')) st.loop = p.get('loop') === '1';
  return st;
}

export function buildSearch(st: UrlState): string {
  const p = new URLSearchParams();
  if (st.song) p.set('song', st.song);
  if (st.variant) p.set('v', st.variant);
  if (st.t !== undefined && st.t > 0) p.set('t', String(Math.round(st.t)));
  const f = st.filters ? encodeFilters(st.filters) : '';
  if (f) p.set('f', f);
  if (st.q) p.set('q', st.q);
  if (st.theme && st.theme !== 'modern') p.set('theme', st.theme);
  if (st.loop) p.set('loop', '1');
  const s = p.toString();
  return s ? `?${s}` : '';
}

export function writeUrl(st: UrlState): void {
  if (typeof history === 'undefined') return;
  const next = buildSearch(st) + location.hash;
  const cur = location.search + location.hash;
  if (next !== cur) history.replaceState(null, '', next || location.pathname);
}
