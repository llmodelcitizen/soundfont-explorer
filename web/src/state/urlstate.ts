/**
 * URL ⇄ state: ?song=&v=&f=engine:adlmidi,opnmidi;size:lt2m&q=&theme=&loop=1&t=
 * URL_PARAM_ORDER is the single source of truth for the order the app writes them in: `t`
 * (start time) is ALWAYS last, so a link can be cut back to "from the top" at the last `&`.
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

/** every parameter the app writes, in the order it writes them — `t` last, always */
export const URL_PARAM_ORDER = ['song', 'v', 'f', 'q', 'theme', 'loop', 't'] as const;
export type UrlParam = (typeof URL_PARAM_ORDER)[number];

/** what each parameter means; the share dialog renders these in URL_PARAM_ORDER */
export const URL_PARAM_HELP: Record<UrlParam, string> = {
  song: 'which track is playing (its id in songs.json).',
  v: 'the audible variant — the SoundFont or synth bank you are hearing.',
  f: 'the filter selection, as facet:value,value groups joined by ";". An empty f= means "no filters at all".',
  q: 'the text in the search box.',
  theme: 'the look: win95 or amiga. Absent means the modern theme.',
  loop: 'loop=1 repeats the track instead of stopping at the end.',
  t: 'where playback starts, in whole seconds. Always the last parameter.',
};

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
  // `f=` (present, empty) is a cleared selection; no `f` at all leaves the app's default filters
  if (p.has('f')) st.filters = decodeFilters(p.get('f') ?? '');
  if (p.get('q')) st.q = p.get('q')!;
  if (p.get('theme')) st.theme = p.get('theme')!;
  if (p.get('loop')) st.loop = p.get('loop') === '1';
  return st;
}

/** one writer per parameter (null = leave it out), so URL_PARAM_ORDER alone decides the order */
const WRITERS: Record<UrlParam, (st: UrlState) => string | null> = {
  song: (st) => st.song || null,
  v: (st) => st.variant || null,
  f: (st) => (st.filters ? encodeFilters(st.filters) : null), // an empty selection round-trips as `f=`
  q: (st) => st.q || null,
  theme: (st) => (st.theme && st.theme !== 'modern' ? st.theme : null),
  loop: (st) => (st.loop ? '1' : null),
  t: (st) => (st.t !== undefined && st.t > 0 ? String(Math.round(st.t)) : null),
};

export function buildSearch(st: UrlState): string {
  const p = new URLSearchParams();
  for (const key of URL_PARAM_ORDER) {
    const v = WRITERS[key](st);
    if (v !== null) p.set(key, v);
  }
  const s = p.toString();
  return s ? `?${s}` : '';
}

/** the parameters actually present in a search string, in URL_PARAM_ORDER */
export function paramsIn(search: string): UrlParam[] {
  const p = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search);
  return URL_PARAM_ORDER.filter((k) => p.has(k));
}

/** the page URL with no query and no fragment — the stem every share link is built on */
function shareBase(): string {
  return typeof location === 'undefined' ? '' : location.origin + location.pathname;
}

export interface ShareLinks {
  /** the current state, resuming where the listener is now */
  withTime: string;
  /** the same link, from the top of the track */
  withoutTime: string;
}

/** the two links the share dialog offers; they differ only in the trailing `t=` */
export function shareLinks(st: UrlState, base: string = shareBase()): ShareLinks {
  const fromTop: UrlState = { ...st };
  delete fromTop.t;
  return { withTime: base + buildSearch(st), withoutTime: base + buildSearch(fromTop) };
}

export function writeUrl(st: UrlState): void {
  if (typeof history === 'undefined') return;
  const next = buildSearch(st) + location.hash;
  const cur = location.search + location.hash;
  if (next !== cur) history.replaceState(null, '', next || location.pathname);
}
