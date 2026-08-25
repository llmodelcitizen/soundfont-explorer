/**
 * User preferences (localStorage `sfp.prefs.v1`) and the per-track "listened" ledger
 * (`sfp.listened.v1`: seconds of actual playback per song × variant). Both survive reloads;
 * both tolerate a missing/blocked localStorage.
 */
export interface Prefs {
  /** seconds of audible playback before a variant's dot lights up */
  listenedAfterS: number;
  /** step to the next track when the current one plays to its end (ignored while LOOP is on) */
  autoNextTrack: boolean;
  /** remember a separate playhead position for each track (false = start from the beginning) */
  preserveTrackPosition: boolean;
  /** visible list columns (keys from ui/columns.ts); always-on columns are implied */
  columns: string[];
  /** the same for the compact (mobile) layout */
  mobileColumns: string[];
}

export const DEFAULT_PREFS: Prefs = {
  listenedAfterS: 2,
  autoNextTrack: true,
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
    // a pre-existing sfp.prefs.v1 has no autoNextTrack: it gets the default, like a fresh browser
    autoNextTrack: typeof p.autoNextTrack === 'boolean' ? p.autoNextTrack : DEFAULT_PREFS.autoNextTrack,
    preserveTrackPosition: typeof p.preserveTrackPosition === 'boolean' ? p.preserveTrackPosition : DEFAULT_PREFS.preserveTrackPosition,
    columns: Array.isArray(p.columns) ? p.columns.map(String) : [...DEFAULT_PREFS.columns],
    mobileColumns: Array.isArray(p.mobileColumns) ? p.mobileColumns.map(String) : [...DEFAULT_PREFS.mobileColumns],
  };
}

export function savePrefs(p: Prefs): void {
  write(PREFS_KEY, p);
}

/** Page-session playhead memory. A track with no entry has never been visited and starts at zero. */
export class TrackPositions {
  private positions = new Map<string, number>();

  remember(track: string, position: number): void {
    if (Number.isFinite(position)) this.positions.set(track, Math.max(0, position));
  }

  recall(track: string): number {
    return this.positions.get(track) ?? 0;
  }

  clear(): void {
    this.positions.clear();
  }
}

/**
 * React to a change of "preserve track position". Turning it off resets every saved position to
 * zero rather than only stopping new ones from being saved: the remembered song → seconds map is
 * dropped, and `rewind` zeroes the one position no map holds yet — the live playhead of the track
 * being listened to (and with it the `t` the URL would otherwise restore on the next reload).
 * Only the true → false transition acts: settings changes arrive for every option, the listened
 * slider included, and none of the others may move the playhead.
 */
export function applyPreservePreference(was: boolean, now: boolean, positions: TrackPositions, rewind: () => void): void {
  if (!was || now) return;
  positions.clear();
  rewind();
}

type Ledger = Record<string, Record<string, number>>;

/**
 * Keep only the {song: {variant: seconds}} shape. Anything else in the stored value (an older
 * build's layout, a hand-edited entry) is dropped: add() writes `this.data[song][variant]`
 * from the rAF loop, and a number or string where an object is expected would throw there and
 * stop the whole UI loop.
 *
 * The maps are prototype-less: JSON.parse keeps a stored key of '__proto__' as an own property,
 * but `out[song] = entry` on a plain object would set the object's prototype instead of a key
 * (silently losing that song, and every variant of it, on the next flush).
 */
export function sanitizeLedger(raw: unknown): Ledger {
  const out = Object.create(null) as Ledger;
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return out;
  for (const [song, vs] of Object.entries(raw as Record<string, unknown>)) {
    if (!vs || typeof vs !== 'object' || Array.isArray(vs)) continue;
    const entry = Object.create(null) as Record<string, number>;
    for (const [v, sec] of Object.entries(vs as Record<string, unknown>)) {
      if (typeof sec === 'number' && Number.isFinite(sec) && sec >= 0) entry[v] = sec;
    }
    if (Object.keys(entry).length) out[song] = entry;
  }
  return out;
}

/** periodic flush while playing; the app also flushes on song change, visibilitychange→hidden and pagehide */
const LEDGER_FLUSH_MS = 15_000;

export class ListenedLedger {
  private data: Ledger;
  private dirty = false;
  private lastFlush = 0;

  constructor(private readonly now: () => number = () => Date.now()) {
    this.data = sanitizeLedger(read<unknown>(LISTENED_KEY));
  }

  seconds(song: string, variant: string): number {
    return this.data[song]?.[variant] ?? 0;
  }

  /** add `dt` seconds of playback; returns the new total */
  add(song: string, variant: string, dt: number): number {
    if (!(dt > 0)) return this.seconds(song, variant);
    const s = (this.data[song] ??= Object.create(null) as Record<string, number>);
    const v = (s[variant] ?? 0) + dt;
    s[variant] = v;
    this.dirty = true;
    if (this.now() - this.lastFlush > LEDGER_FLUSH_MS) this.flush();
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
    this.data = Object.create(null) as Ledger;
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

/** Favorite variants (global: a SoundFont you like is a favorite for every track). */
export class Favorites {
  private ids: Set<string>;

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
    return this.ids.has(id);
  }
}
