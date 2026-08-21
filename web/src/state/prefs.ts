/**
 * User preferences (localStorage `sfp.prefs.v1`) and the per-track "listened" ledger
 * (`sfp.listened.v1`: seconds of actual playback per song × variant). Both survive reloads;
 * both tolerate a missing/blocked localStorage.
 */
export interface Prefs {
  /** seconds of audible playback before a variant's dot lights up */
  listenedAfterS: number;
  /** keep the playhead position when stepping to another track (false = start from the beginning) */
  preserveTrackPosition: boolean;
  /** visible list columns (keys from ui/columns.ts); always-on columns are implied */
  columns: string[];
  /** the same for the compact (mobile) layout */
  mobileColumns: string[];
}

export const DEFAULT_PREFS: Prefs = {
  listenedAfterS: 2,
  preserveTrackPosition: true,
  columns: ['chip', 'engine', 'decade', 'fav', 'dot'],
  mobileColumns: ['chip', 'fav', 'dot'],
};
const PREFS_KEY = 'sfp.prefs.v1';
const LISTENED_KEY = 'sfp.listened.v1';

function read<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

function write(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* private mode / quota: keep in memory only */
  }
}

export function loadPrefs(): Prefs {
  const p = read<Partial<Prefs>>(PREFS_KEY) ?? {};
  const n = Number(p.listenedAfterS);
  return {
    listenedAfterS: Number.isFinite(n) && n >= 0 ? Math.min(60, n) : DEFAULT_PREFS.listenedAfterS,
    preserveTrackPosition: typeof p.preserveTrackPosition === 'boolean' ? p.preserveTrackPosition : DEFAULT_PREFS.preserveTrackPosition,
    columns: Array.isArray(p.columns) ? p.columns.map(String) : [...DEFAULT_PREFS.columns],
    mobileColumns: Array.isArray(p.mobileColumns) ? p.mobileColumns.map(String) : [...DEFAULT_PREFS.mobileColumns],
  };
}

export function savePrefs(p: Prefs): void {
  write(PREFS_KEY, p);
}

type Ledger = Record<string, Record<string, number>>;

export class ListenedLedger {
  private data: Ledger;
  private dirty = false;
  private lastFlush = 0;

  constructor(private readonly now: () => number = () => Date.now()) {
    this.data = read<Ledger>(LISTENED_KEY) ?? {};
  }

  seconds(song: string, variant: string): number {
    return this.data[song]?.[variant] ?? 0;
  }

  /** add `dt` seconds of playback; returns the new total */
  add(song: string, variant: string, dt: number): number {
    if (!(dt > 0)) return this.seconds(song, variant);
    const s = (this.data[song] ??= {});
    const v = (s[variant] ?? 0) + dt;
    s[variant] = v;
    this.dirty = true;
    if (this.now() - this.lastFlush > 2000) this.flush();
    return v;
  }

  listened(song: string, threshold: number): Set<string> {
    const out = new Set<string>();
    const s = this.data[song];
    if (s) for (const [v, sec] of Object.entries(s)) if (sec >= threshold) out.add(v);
    return out;
  }

  resetSong(song: string): void {
    delete this.data[song];
    this.dirty = true;
    this.flush();
  }

  resetAll(): void {
    this.data = {};
    this.dirty = true;
    this.flush();
  }

  flush(): void {
    if (!this.dirty) return;
    write(LISTENED_KEY, this.data);
    this.dirty = false;
    this.lastFlush = this.now();
  }
}

const FAV_KEY = 'sfp.favorites.v1';

/** Favourite variants (global: a SoundFont you like is a favourite for every track). */
export class Favorites {
  private ids: Set<string>;
  private listeners = new Set<(ids: Set<string>) => void>();

  constructor() {
    const raw = read<string[]>(FAV_KEY);
    this.ids = new Set(Array.isArray(raw) ? raw.map(String) : []);
  }

  has(id: string): boolean {
    return this.ids.has(id);
  }

  all(): Set<string> {
    return new Set(this.ids);
  }

  toggle(id: string): boolean {
    if (this.ids.has(id)) this.ids.delete(id);
    else this.ids.add(id);
    write(FAV_KEY, [...this.ids]);
    for (const l of this.listeners) l(this.all());
    return this.ids.has(id);
  }

  onChange(fn: (ids: Set<string>) => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
}
