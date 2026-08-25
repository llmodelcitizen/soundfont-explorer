/**
 * App: loads songs.json, then catalog + decoder probe + set concurrently; creates the AudioContext
 * at boot (suspended, unlocked by the first gesture) and wires engine ⇄ UI ⇄ URL.
 * Song switches keep variant id (or nearest index), per-track position and filters (plan §10).
 */
import { Engine, type Status } from '../audio/engine';
import { NativeDecoder, WasmDecoder, probeNative, type Decoder } from '../audio/decode';
import { Fetcher } from '../audio/net/fetcher';
import { SegmentStore } from '../audio/store';
import type { ContextLike, Tier } from '../audio/types';
import { NET, POLICY, isCompact } from '../config';
import { parseCatalog, type CatalogDoc } from '../contracts/catalog';
import { parseSet, type SetDoc } from '../contracts/set';
import { parseSongs, type SongEntry, type SongsDoc } from '../contracts/songs';
import { installKeyboard } from '../input/keyboard';
import { InputPolicy } from '../input/policy';
import { FilterIndex, type Selection } from '../state/filterIndex';
import { parseUrl, writeUrl, type UrlState } from '../state/urlstate';
import { renderCredits } from './credits';
import { DebugPanel } from './debug';
import { clear, fmtBytes, h } from './dom';
import { FilterBar } from './filters';
import { showGate } from './gate';
import { KeymapOverlay } from './keymapOverlay';
import { VariantList } from './list';
import { sortIds, visibleColumns, type CellContext, type ColKey } from './columns';
import { NowPlaying } from './nowplaying';
import { SongPicker } from './songpicker';
import { adjacentTrackId, autoAdvanceTarget, TrackList } from './tracklist';
import { SettingsModal } from './settings';
import { Favorites, ListenedLedger, TrackPositions, loadPrefs, savePrefs, type Prefs } from '../state/prefs';
import { applyTheme, nextTheme, readTheme, type ThemeName } from './theme';
import { applyModernFont, clearModernFontPreference, modernFont, nextModernFont, readModernFont, saveModernFont, type ModernFontId } from './modernFont';
import { audioSession, createContext, installResumeOnGesture, unlock } from '../audio/unlock';
import { Transport, resumeNotice } from './transport';

import probeUrl from '../assets/probe-1k-40ms.opus?url';

async function getJson(url: string): Promise<unknown> {
  const r = await fetch(url, { cache: url.endsWith('songs.json') ? 'no-cache' : 'default' });
  if (!r.ok) throw new Error(`HTTP ${r.status} for ${url}`);
  return r.json();
}

const sameKeys = (a: readonly string[], b: readonly string[]): boolean => a.length === b.length && a.every((k, i) => k === b[i]);

export class App {
  private root: HTMLElement;
  private songs!: SongsDoc;
  private catalog!: CatalogDoc;
  private song!: SongEntry;
  private set!: SetDoc;
  private ctx!: AudioContext;
  private decoder!: Decoder;
  private fetcher = new Fetcher(NET.inflightCap);
  private store!: SegmentStore;
  private engine!: Engine;
  private index!: FilterIndex;
  private visible: string[] = [];
  private cursor = 0;
  private list!: VariantList;
  private filters!: FilterBar;
  private transport!: Transport;
  private nowPlaying!: NowPlaying;
  private picker!: SongPicker;
  private volTop: HTMLInputElement | null = null;
  /** set once the user has started playback in this page load (first ▶ or first row tap) */
  private everPlayed = false;
  /** Stop/end → another track arms its first explicit render click as a request to play. */
  private playOnRenderClick = false;
  /** Survives the engine/UI rebuild caused by changing tracks after an explicit Stop. */
  private stoppedByUser = false;
  private tracks!: TrackList;
  private trackScrollTop = 0;
  private rightPane!: HTMLElement;
  private debug = new DebugPanel();
  private keymap = new KeymapOverlay();
  private prefs: Prefs = loadPrefs();
  private trackPositions = new TrackPositions();
  private ledger = new ListenedLedger();
  private settings = new SettingsModal(this.prefs, {
    onChange: (p) => {
      if (this.prefs.preserveTrackPosition && !p.preserveTrackPosition) this.trackPositions.clear();
      this.prefs = p;
      savePrefs(p);
      this.refreshListened();
      if (this.tracks) {
        this.tracks.autoNextBox.checked = p.autoNextTrack;
        this.tracks.preserveBox.checked = p.preserveTrackPosition;
      }
      // the 'listened after' slider fires this on every tick: rebuild the rows only when the column set changed
      const cols = this.effectiveColumns();
      if (this.list && !sameKeys(this.list.columns, visibleColumns(cols).map((c) => c.key))) {
        this.list.setColumns(cols);
        this.applyHighlightsAfterColumns();
      }
    },
    onResetTrack: () => {
      this.ledger.resetSong(this.song.id);
      this.refreshListened();
    },
    onResetAll: () => {
      this.ledger.resetAll();
      this.refreshListened();
    },
    onResetFont: () => {
      this.modernFontId = clearModernFontPreference();
      applyModernFont(this.modernFontId);
      this.syncFontCycler();
    },
    trackTitle: () => this.song?.title ?? '',
  });
  private lastListenTick = 0;
  /** the UI loop is showing the 'tap to resume' notice in place of the engine status */
  private suspendedNotice = false;
  private favorites = new Favorites();
  /** per-row state the list and the sorter ask for (favorite / listened seconds / listened ≥ threshold) */
  private readonly rowHooks: Pick<CellContext, 'isFavorite' | 'listenedSeconds' | 'listened'> = {
    isFavorite: (id) => this.favorites.has(id),
    listenedSeconds: (id) => this.ledger.seconds(this.song.id, id),
    listened: (id) => this.ledger.seconds(this.song.id, id) >= this.prefs.listenedAfterS,
  };
  private policy!: InputPolicy;
  private theme: ThemeName;
  private modernFontId: ModernFontId = readModernFont();
  private pinnedA: string | null = null;
  private sort: { key: ColKey | null; dir: 1 | -1 } = { key: null, dir: 1 };
  private favoritesOnly = false;
  private canonicalIndex = new Map<string, number>();
  private url: UrlState;
  private urlTimer: ReturnType<typeof setTimeout> | null = null;
  private decodeKind = 'native';
  /** phone-readable diagnostics (debug panel) */
  private diag: Record<string, string | number> = {};
  private volume = 1;
  private main!: HTMLElement;
  private header!: HTMLElement;
  private title!: HTMLElement;
  private uninstallKeys: (() => void) | null = null;

  constructor(root: HTMLElement) {
    this.root = root;
    this.url = parseUrl();
    this.theme = readTheme(this.url.theme);
    applyModernFont(this.modernFontId);
    applyTheme(this.theme);
  }

  async boot(): Promise<void> {
    try {
      this.songs = parseSongs(await getJson('/songs.json'));
    } catch (e) {
      this.fatal(`Could not load songs.json: ${(e as Error).message}`);
      return;
    }
    if (!this.songs.songs.length) {
      this.fatal('No songs have been published yet.');
      return;
    }
    const known = (id: string | null | undefined) => !!id && this.songs.songs.some((s) => s.id === id);
    const songId = known(this.url.song) ? this.url.song! : known(this.songs.defaults.song) ? this.songs.defaults.song! : this.songs.songs[0]!.id;
    const entry = this.songs.songs.find((s) => s.id === songId)!;
    // No gate: the context is created now (browsers allow that, suspended) and unlocked by the
    // first real gesture — ▶, a tap on a list row, any key — through installResumeOnGesture().
    // Nothing plays until the user asks.
    this.ctx = createContext();
    this.diag.ctxCreatedState = this.ctx.state;
    // catalog, decoder probe and the first set only depend on songs.json: fetch them together
    const [catalog, decoder, set] = await Promise.allSettled([getJson(this.songs.catalog).then(parseCatalog), this.pickDecoder(), getJson(entry.set).then(parseSet)]);
    if (catalog.status === 'rejected') {
      this.fatal(`Could not load the catalog: ${(catalog.reason as Error).message}`);
      return;
    }
    if (decoder.status === 'rejected') throw decoder.reason;
    if (set.status === 'rejected') {
      this.fatal(`Could not load the song set for ${songId}: ${(set.reason as Error).message}`);
      return;
    }
    this.catalog = catalog.value;
    this.installGlobalListeners();
    await this.loadSong(songId, { setDoc: set.value, variant: this.url.variant, t: this.url.t });
    this.tickUi();
    this.onHashChange();
  }

  /** window/document/AudioContext listeners — installed exactly once, not per song. */
  private installGlobalListeners(): void {
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') this.ledger.flush();
      else if (document.visibilityState === 'visible' && this.ctx.state !== 'running') unlock(this.ctx);
    });
    window.addEventListener('pagehide', () => this.ledger.flush());
    let gateUp = false;
    this.ctx.addEventListener?.('statechange', () => {
      this.diag.ctxState = this.ctx.state;
      if ((this.ctx.state === 'suspended' || (this.ctx.state as string) === 'interrupted') && !gateUp && this.engine?.playing) {
        gateUp = true;
        void showGate(this.root, 'audio paused', 'The browser suspended audio. Tap to resume.', 'Resume', () => unlock(this.ctx)).then(() => {
          gateUp = false;
        });
      }
    });
    installResumeOnGesture(this.ctx, () => {
      this.diag.resumedOnGesture = (Number(this.diag.resumedOnGesture) || 0) + 1;
    });
    window.addEventListener('hashchange', () => this.onHashChange());
  }

  private creditsEl: HTMLElement | null = null;

  /** #/credits opens the About page over the player without stopping the music. */
  private onHashChange(): void {
    const want = location.hash.startsWith('#/credits');
    if (want && !this.creditsEl) {
      this.creditsEl = h('div', { class: 'page' }, renderCredits(this.songs, this.catalog));
      this.root.appendChild(this.creditsEl);
      this.creditsEl.scrollTop = 0;
    } else if (!want && this.creditsEl) {
      this.creditsEl.remove();
      this.creditsEl = null;
    }
  }

  private fatal(msg: string): void {
    clear(this.root);
    this.root.appendChild(h('div', { class: 'fatal' }, h('h1', null, 'Soundfont Explorer'), h('p', null, msg)));
  }

  /** the audio side sees the context through the narrower, test-friendly ContextLike */
  private get ctxLike(): ContextLike {
    return this.ctx as unknown as ContextLike;
  }

  private async pickDecoder(): Promise<void> {
    let bytes: ArrayBuffer | null = null;
    try {
      bytes = await (await fetch(probeUrl)).arrayBuffer();
    } catch {
      /* no probe → assume native */
    }
    const probe = bytes ? await probeNative(this.ctxLike, bytes) : { ok: true, reason: 'no probe', ms: 0 };
    this.diag.probe = `${probe.ok ? 'native ok' : 'native FAILED'}: ${probe.reason} (${probe.ms} ms)`;
    if (probe.ok) {
      this.decoder = new NativeDecoder(this.ctxLike, NET.decodeWorkers);
      this.decodeKind = 'native';
    } else {
      this.decoder = new WasmDecoder(this.ctxLike, NET.decodeWorkers);
      this.decodeKind = `wasm ×${NET.decodeWorkers} (${probe.reason})`;
    }
  }

  // ---------------------------------------------------------------- song lifecycle

  /** switch to another song (picker, track list, [ / ]) keeping the audible variant and the cursor */
  private switchSong(id: string, opts: { play?: boolean } = {}): void {
    const ended = this.engine.status.kind === 'ended';
    const stopped = this.stoppedByUser || this.engine.status.kind === 'stopped' || ended;
    if (id !== this.song.id && stopped) this.playOnRenderClick = true;
    this.trackScrollTop = this.tracks?.scrollTop ?? this.trackScrollTop;
    this.ledger.flush();
    const preserve = this.prefs.preserveTrackPosition;
    // a track that ran to its end has no position left to resume from: play() would restart it anyway
    if (preserve) this.trackPositions.remember(this.song.id, ended ? 0 : this.engine.position());
    const position = preserve ? this.trackPositions.recall(id) : 0;
    void this.loadSong(id, { keepIndex: this.cursor, variant: this.engine.audible ?? undefined, t: position, play: opts.play });
  }

  /** `setDoc`: the set boot() already fetched; later switches fetch their own */
  private async loadSong(id: string, opts: { setDoc?: SetDoc; variant?: string; t?: number; keepIndex?: number; play?: boolean }): Promise<void> {
    const entry = this.songs.songs.find((s) => s.id === id);
    if (!entry) return;
    let set = opts.setDoc;
    if (!set) {
      try {
        set = parseSet(await getJson(entry.set));
      } catch (e) {
        // keep playing the current song; tell the user
        this.transport.setStatus(`could not load ${entry.title}: ${(e as Error).message}`, 'wontload');
        this.picker.set(this.song.id);
        return;
      }
    }
    // Boot honors the URL position; later switches pass this track's own saved position (or zero).
    const targetPos = opts.t ?? 0;
    // `play` forces playback on the new song: the auto-step happens after the old one has ended (not playing)
    const wasPlaying = opts.play ?? (this.engine ? this.engine.playing : false);
    const prevFilters = this.filters ? this.filters.sel : (this.url.filters ?? { completeness: new Set(['full_gm']) });
    const prevQuery = this.filters ? this.filters.query : (this.url.q ?? '');
    const loop = this.engine ? this.engine.timeline.loop : !!this.url.loop;
    const volume = this.volume;
    const muted = this.engine ? this.engine.isMuted : false;
    if (this.engine) {
      this.engine.dispose();
      this.policy.reset();
    }
    this.song = entry;
    this.set = set;
    this.store = new SegmentStore(set, this.fetcher, this.decoder);
    this.engine = new Engine(this.ctxLike, set, this.store);
    this.engine.setLoop(loop);
    this.engine.setVolume(volume);
    this.engine.setMuted(muted);
    this.index = new FilterIndex(set.order, this.catalog);
    this.buildUi(prevFilters, prevQuery);
    // choose the variant: requested id → nearest index → default → first
    let target: string | undefined = opts.variant && set.variants[opts.variant] ? opts.variant : undefined;
    if (!target && opts.keepIndex !== undefined) target = this.visible[Math.min(opts.keepIndex, this.visible.length - 1)];
    if (!target && set.variants[this.songs.defaults.variant]) target = this.songs.defaults.variant;
    if (!target) target = this.visible[0] ?? set.order[0];
    this.cursor = Math.max(0, this.visible.indexOf(target ?? ''));
    this.policy.syncCursor(this.cursor);
    this.list.select(this.cursor);
    this.engine.setOrder(this.visible, this.cursor);
    this.engine.start();
    if (target) this.engine.select(target);
    const pos = Math.min(Math.max(targetPos, 0), set.duration_s);
    this.engine.seek(pos);
    if (wasPlaying) this.engine.play();
    this.transport.setMuted(muted);
    this.transport.setVolume(volume);
    if (this.volTop) this.volTop.value = String(volume);
    this.syncUrl(true);
  }

  private buildUi(sel: Selection, query: string): void {
    if (this.uninstallKeys) this.uninstallKeys();
    clear(this.root);
    // the new FilterBar starts closed: a `filters-open` left over from the previous song would
    // keep Now Playing hidden on phones (and the scrim it pointed at is gone with the old root)
    this.onFiltersOpen(false);
    this.canonicalIndex = new Map(this.set.order.map((id, i) => [id, i]));
    const clickRender = (i: number) => {
      const shouldPlay = !this.everPlayed || this.playOnRenderClick;
      this.policy.jump(i, performance.now());
      if (shouldPlay) {
        this.playOnRenderClick = false;
        this.engine.play(); // first-ever tap, or Stop/end → track → render, asks to hear it
      }
    };
    this.list = new VariantList(
      this.catalog,
      this.set,
      {
        onClick: clickRender,
        onStickyClick: (id) => {
          this.filters.clearAll();
          const i = this.visible.indexOf(id);
          if (i >= 0) clickRender(i);
        },
        onSort: (key) => this.toggleSort(key),
      },
      this.rowHooks,
    );
    this.list.setColumns(this.effectiveColumns());
    this.list.setSort(this.sort);
    this.filters = new FilterBar(this.index, sel, query, {
      onChange: (s, q) => this.applyFilters(s, q),
      onSearchEnter: () => {
        if (this.visible.length) this.policy.jump(0, performance.now());
        this.focusList();
      },
      onFavoritesOnly: (on) => {
        this.favoritesOnly = on;
        this.applyFilters(this.filters.sel, this.filters.query);
      },
      onOpenChange: (open) => this.onFiltersOpen(open),
    });
    this.filters.setFavoritesOnly(this.favoritesOnly);
    this.nowPlaying = new NowPlaying(this.catalog, this.set, this.song, {
      isFavorite: (id) => this.favorites.has(id),
      toggleFavorite: (id) => this.toggleFavorite(id),
    });
    this.transport = new Transport(
      this.set.duration_s,
      {
        onToggle: () => this.togglePlayback(),
        onStop: () => this.stopPlayback(),
        onSeek: (p) => this.engine.seek(p),
        onSkip: (d) => this.engine.seek(this.engine.position() + d),
        onLoop: (on) => this.setLoop(on),
        onVolume: (v) => {
          this.volume = v;
          this.engine.setVolume(v);
          if (this.volTop) this.volTop.value = String(v);
        },
        onMute: () => this.toggleMute(),
        onStep: (d, rep) => this.policy.step(d, rep, performance.now()),
        onStepEnd: () => this.policy.keyup(performance.now()),
      },
      () => this.focusList(),
    );
    this.transport.setLoop(this.engine.timeline.loop);
    this.picker = new SongPicker(this.songs.songs, this.song.id, (id) => this.switchSong(id));
    const themeSel = h(
      'select',
      { class: 'themepick', 'aria-label': 'theme', title: 'theme (T)' },
      h('option', { value: 'modern' }, 'modern'),
      h('option', { value: 'win95' }, 'win95'),
      h('option', { value: 'amiga' }, 'amiga'),
    ) as HTMLSelectElement;
    themeSel.value = this.theme;
    themeSel.addEventListener('change', () => {
      this.setTheme(themeSel.value as ThemeName);
      themeSel.blur();
    });
    const helpBtn = h('button', { class: 'btn', type: 'button', title: 'keys (?)' }, '?');
    helpBtn.addEventListener('click', () => this.keymap.toggle());
    // phone-portrait volume (the transport's slider is hidden there); mirrors the transport's value
    const volTop = h('input', { type: 'range', class: 'vol vol-top', min: '0', max: '1', step: '0.01', value: String(this.volume), 'aria-label': 'volume' }) as HTMLInputElement;
    volTop.addEventListener('input', () => {
      this.volume = Number(volTop.value);
      this.engine.setVolume(this.volume);
      this.transport.setVolume(this.volume);
    });
    volTop.addEventListener('change', () => volTop.blur());
    this.volTop = volTop;
    const dbgBtn = h('button', { class: 'btn icon dbg-btn', type: 'button', title: 'debug panel (D)', 'aria-label': 'debug panel' });
    dbgBtn.innerHTML =
      '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M8 2l1.88 1.88M14.12 3.88L16 2M9 7.13v-1a3 3 0 0 1 6 0v1"/>' +
      '<path d="M12 20c-3.3 0-6-2.7-6-6v-3a6 6 0 0 1 12 0v3c0 3.3-2.7 6-6 6z"/>' +
      '<path d="M12 20v-9M6.53 9C4.6 8.8 3 7.1 3 5M6 13H2M3 21c0-2.1 1.7-3.9 3.8-4M20.97 5c0 2.1-1.6 3.8-3.5 4M22 13h-4M17.2 17c2.1.1 3.8 1.9 3.8 4"/></svg>';
    dbgBtn.addEventListener('click', () => this.debug.toggle());
    const settingsBtn = h('button', { class: 'btn icon', type: 'button', title: 'settings (S)', 'aria-label': 'settings' });
    settingsBtn.innerHTML =
      '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
      '<circle cx="12" cy="12" r="3.2"/>' +
      '<path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h.1a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v.1a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>';
    settingsBtn.addEventListener('click', () => this.settings.toggle());
    this.title = h('div', { class: 'title font-cycler' }, h('span', { class: 'brand' }, 'Soundfont Explorer'), h('span', { class: 'domain' }, ` - ${location.host}`));
    this.title.addEventListener('click', () => this.cycleModernFont());
    this.title.addEventListener('keydown', (event) => {
      if (this.theme !== 'modern' || (event.key !== 'Enter' && event.key !== ' ')) return;
      event.preventDefault();
      event.stopPropagation(); // handled here: Space must not also reach the window keymap (play/pause)
      this.cycleModernFont();
    });
    this.header = h(
      'header',
      { class: 'top' },
      this.title,
      this.picker.el,
      h('div', { class: 'spacer' }),
      themeSel,
      settingsBtn,
      dbgBtn,
      helpBtn,
      h('a', { class: 'btn link', href: '#/credits', title: 'credits, licenses, about' }, 'about'),
      volTop,
    );
    this.syncFontCycler();
    this.tracks = new TrackList(this.songs.songs, this.song.id, (id) => this.switchSong(id), {
      autoNext: {
        value: this.prefs.autoNextTrack,
        onChange: (v) => {
          this.prefs = { ...this.prefs, autoNextTrack: v };
          savePrefs(this.prefs);
          this.settings.setPrefs(this.prefs);
        },
      },
      preserve: {
        value: this.prefs.preserveTrackPosition,
        onChange: (v) => {
          if (!v) this.trackPositions.clear();
          this.prefs = { ...this.prefs, preserveTrackPosition: v };
          savePrefs(this.prefs);
          this.settings.setPrefs(this.prefs);
        },
      },
    });
    this.rightPane = h('section', { class: 'right' }, this.tracks.el, this.splitHandle(), this.nowPlaying.el);
    this.main = h('main', { class: 'main' }, h('section', { class: 'left' }, this.filters.el, this.list.el), this.vSplitHandle(), this.hSplitHandle(), this.rightPane);
    this.applySplit();
    this.applyPaneSizes();
    this.root.append(this.header, this.main, this.transport.el, this.debug.el, this.keymap.el, this.settings.el);
    this.tracks.restoreView(this.trackScrollTop);
    const trackScrollTop = this.trackScrollTop;
    // applySplit() measures on the next frame and can resize this pane; restore again after
    // that measurement so the temporary height cannot clamp a deep scroll position.
    requestAnimationFrame(() => this.tracks.restoreView(trackScrollTop));
    this.policy = new InputPolicy({
      move: (d) => this.moveCursor(this.cursor + d),
      moveTo: (i) => this.moveCursor(i),
      commit: (i, at) => {
        const id = this.visible[i];
        if (id) this.engine.select(id, at);
      },
      setTimeout: (fn, ms) => setTimeout(fn, ms),
      clearTimeout: (t) => clearTimeout(t as ReturnType<typeof setTimeout>),
    });
    this.uninstallKeys = installKeyboard(window, {
      step: (d, rep, at) => this.policy.step(d, rep, at),
      stepEnd: (at) => this.policy.keyup(at),
      page: (d, at) => this.policy.jumpBy(d * POLICY.pageStep, at),
      home: (at) => this.policy.jump(0, at),
      end: (at) => this.policy.jump(this.visible.length - 1, at),
      toggle: () => this.togglePlayback(),
      stop: () => this.stopPlayback(),
      skip: (s) => this.engine.seek(this.engine.position() + s),
      loop: () => this.setLoop(!this.engine.timeline.loop),
      mute: () => this.toggleMute(),
      favorite: () => this.toggleCurrentFavorite(),
      focusSearch: () => this.filters.search.focus(),
      escape: () => {
        this.keymap.toggle(false);
        this.settings.toggle(false);
        this.filters.toggle(false);
        if (this.creditsEl) history.replaceState(null, '', location.pathname + location.search), this.onHashChange();
        this.focusList();
      },
      song: (d) => this.stepSong(d),
      pinA: () => {
        this.pinnedA = this.engine.audible;
        this.transport.setStatus(this.pinnedA ? `A = ${this.pinnedA}` : '');
      },
      ab: () => {
        if (!this.pinnedA) return;
        const cur = this.engine.audible;
        const i = this.visible.indexOf(this.pinnedA);
        if (i >= 0) {
          this.policy.jump(i, performance.now());
          this.pinnedA = cur;
        } else {
          this.engine.select(this.pinnedA);
          this.pinnedA = cur;
        }
      },
      filters: () => this.filters.toggle(),
      theme: () => this.setTheme(nextTheme(this.theme)),
      debug: () => this.debug.toggle(),
      keymap: () => this.keymap.toggle(),
      settings: () => this.settings.toggle(),
    });
    this.refreshListened();
    this.list.setFavorites(this.favorites.all());
    this.engine.on('status', (s) => {
      if (s.kind === 'playing') {
        this.everPlayed = true;
        this.playOnRenderClick = false;
        this.stoppedByUser = false;
      }
      this.onStatus(s);
      const next = autoAdvanceTarget(this.songs.songs, this.song.id, s.kind, { loop: this.engine.timeline.loop, autoNext: this.prefs.autoNextTrack });
      if (next) this.switchSong(next, { play: true });
    });
    this.engine.on('audible', (v) => {
      this.list.setAudible(v);
      this.nowPlaying.show(v);
      this.applySplit();
      this.syncUrl();
    });
    this.engine.on('tier', (t: Tier | null) => this.nowPlaying.setTier(t));
    this.applyFilters(sel, query, true);
    if (this.creditsEl) this.root.appendChild(this.creditsEl); // keep the About page on top across song loads
  }

  // ---- pane sizes: right-pane width (desktop/landscape) and Now Playing height (phone portrait) ----
  private static RIGHT_W_KEY = 'sfp.right-width.v1';
  private static NP_MOBILE_KEY = 'sfp.np-mobile-height.v1';

  private dragHandle(handle: HTMLElement, axis: 'x' | 'y', apply: (delta: number, start: number) => void, start: () => number, reset: () => void): void {
    let origin = 0;
    let base = 0;
    const onMove = (e: PointerEvent) => apply(axis === 'x' ? origin - e.clientX : origin - e.clientY, base);
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
    };
    handle.addEventListener('pointerdown', (e) => {
      origin = axis === 'x' ? e.clientX : e.clientY;
      base = start();
      handle.setPointerCapture?.(e.pointerId);
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
      window.addEventListener('pointercancel', onUp);
      e.preventDefault();
    });
    handle.addEventListener('dblclick', reset);
  }

  /** vertical bar between the list and the right pane: drag left/right to resize the right pane (desktop, landscape) */
  private vSplitHandle(): HTMLElement {
    const handle = h('div', { class: 'vsplit', role: 'separator', 'aria-orientation': 'vertical', 'aria-label': 'resize side pane', title: 'drag to resize · double-click to reset' });
    this.dragHandle(
      handle,
      'x',
      (delta, base) => {
        const w = Math.max(240, Math.min(this.main.clientWidth * 0.7, base + delta));
        this.main.style.setProperty('--right-w', `${Math.round(w)}px`);
        this.save(App.RIGHT_W_KEY, `${Math.round(w)}px`);
      },
      () => this.rightPane.getBoundingClientRect().width,
      () => {
        this.main.style.removeProperty('--right-w');
        this.save(App.RIGHT_W_KEY, null);
      },
    );
    return handle;
  }

  /** horizontal bar above Now Playing on phones in portrait: drag up/down to resize it */
  private hSplitHandle(): HTMLElement {
    const handle = h('div', { class: 'hsplit', role: 'separator', 'aria-orientation': 'horizontal', 'aria-label': 'resize now playing', title: 'drag to resize · double-tap to reset' });
    this.dragHandle(
      handle,
      'y',
      (delta, base) => {
        const hh = Math.max(60, Math.min(this.main.clientHeight - 120, base + delta));
        this.main.style.setProperty('--np-mobile-h', `${Math.round(hh)}px`);
        this.save(App.NP_MOBILE_KEY, `${Math.round(hh)}px`);
      },
      () => this.rightPane.getBoundingClientRect().height,
      () => {
        this.main.style.removeProperty('--np-mobile-h');
        this.save(App.NP_MOBILE_KEY, null);
      },
    );
    // double-tap reset for touch
    let lastTap = 0;
    handle.addEventListener('pointerup', () => {
      const now = performance.now();
      if (now - lastTap < 350) {
        this.main.style.removeProperty('--np-mobile-h');
        this.save(App.NP_MOBILE_KEY, null);
      }
      lastTap = now;
    });
    return handle;
  }

  private save(key: string, value: string | null): void {
    try {
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch {
      /* ignore */
    }
  }

  private load(key: string): string | null {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  }

  private applyPaneSizes(): void {
    const w = this.load(App.RIGHT_W_KEY);
    if (w) this.main.style.setProperty('--right-w', w);
    const hh = this.load(App.NP_MOBILE_KEY);
    if (hh) this.main.style.setProperty('--np-mobile-h', hh);
  }

  // ---- right-pane split (tracks above, now-playing below) ------------------------------
  private static SPLIT_KEY = 'sfp.np-height.v1';
  /** the user's saved Now Playing height (null = size to content); mirrors localStorage so applySplit() never reads it */
  private splitSaved: string | null = this.load(App.SPLIT_KEY);
  private splitMeasurePending = false;

  private rememberSplit(v: string | null): void {
    this.splitSaved = v;
    this.save(App.SPLIT_KEY, v);
  }

  /** custom drag (no pointer capture, no pointercancel) kept as is; unlike dragHandle() it saves once, on release */
  private splitHandle(): HTMLElement {
    const handle = h('div', { class: 'split', role: 'separator', 'aria-orientation': 'horizontal', 'aria-label': 'resize now playing', tabindex: '0', title: 'drag to resize · double-click to reset' });
    let startY = 0;
    let startH = 0;
    const onMove = (e: PointerEvent) => {
      const h2 = Math.max(80, Math.min(this.rightPane.clientHeight - 80, startH + (startY - e.clientY)));
      this.rightPane.style.setProperty('--np-h', `${h2}px`);
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      this.rememberSplit(this.rightPane.style.getPropertyValue('--np-h'));
    };
    handle.addEventListener('pointerdown', (e) => {
      startY = e.clientY;
      startH = this.nowPlaying.el.getBoundingClientRect().height;
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
      e.preventDefault();
    });
    handle.addEventListener('dblclick', () => {
      this.rememberSplit(null);
      this.rightPane.style.removeProperty('--np-h');
      this.applySplit(true);
    });
    handle.addEventListener('keydown', (e) => {
      const cur = this.nowPlaying.el.getBoundingClientRect().height;
      if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
        const h2 = Math.max(80, cur + (e.key === 'ArrowUp' ? 24 : -24));
        this.rightPane.style.setProperty('--np-h', `${h2}px`);
        e.preventDefault();
      }
    });
    return handle;
  }

  /** Default split: Now Playing gets exactly its content height (never cut off, capped at 70 %); the tracks take the rest.
   *  Called on every 'audible' event, so the layout measurement is deferred to one coalesced frame. */
  private applySplit(force = false): void {
    if (this.splitSaved && !force) {
      this.rightPane.style.setProperty('--np-h', this.splitSaved);
      return;
    }
    if (this.splitMeasurePending) return;
    this.splitMeasurePending = true;
    requestAnimationFrame(() => {
      this.splitMeasurePending = false;
      const pane = this.rightPane.clientHeight;
      if (!pane) return;
      const content = this.nowPlaying.el.scrollHeight + 2;
      const h2 = Math.min(content, Math.round(pane * 0.7));
      this.rightPane.style.setProperty('--np-h', `${Math.max(80, h2)}px`);
    });
  }

  // ---- mobile: filters panel hides Now Playing and closes on a tap anywhere below it ---------
  private scrim: HTMLElement | null = null;

  private onFiltersOpen(open: boolean): void {
    this.root.classList.toggle('filters-open', open);
    if (!open || !isCompact()) {
      this.scrim?.remove();
      this.scrim = null;
      return;
    }
    // a transparent layer over everything below the filter bar: the tap only closes the panel
    const scrim = h('div', { class: 'filter-scrim', 'aria-hidden': 'true' });
    const place = () => {
      const r = this.filters.el.getBoundingClientRect();
      scrim.style.top = `${r.bottom}px`;
    };
    const close = (e: Event) => {
      e.preventDefault();
      e.stopPropagation();
      this.filters.toggle(false);
    };
    scrim.addEventListener('pointerdown', close);
    scrim.addEventListener('click', close);
    scrim.addEventListener('touchstart', close, { passive: false });
    place();
    this.root.appendChild(scrim);
    this.scrim = scrim;
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(place) : null;
    ro?.observe(this.filters.el);
    const obs = new MutationObserver(() => {
      if (!scrim.isConnected) {
        ro?.disconnect();
        obs.disconnect();
      }
    });
    obs.observe(this.root, { childList: true });
  }

  private focusList(): void {
    (this.list.el as HTMLElement).focus?.();
    (document.activeElement as HTMLElement | null)?.blur?.();
  }

  private moveCursor(i: number): number {
    const n = this.visible.length;
    this.cursor = n ? Math.min(n - 1, Math.max(0, i)) : 0;
    this.list.select(this.cursor);
    this.engine.cursor(this.cursor);
    return this.cursor;
  }

  private togglePlayback(): void {
    this.stoppedByUser = false;
    this.playOnRenderClick = false;
    this.engine.toggle();
  }

  private stopPlayback(): void {
    this.stoppedByUser = true;
    this.playOnRenderClick = false;
    this.engine.stop();
  }

  private toggleCurrentFavorite(): void {
    const id = this.engine.audible ?? this.visible[this.cursor];
    if (id) this.toggleFavorite(id);
  }

  private toggleFavorite(id: string): boolean {
    const on = this.favorites.toggle(id);
    this.list.setFavorites(this.favorites.all());
    this.nowPlaying.refreshFavorite(id);
    if (this.favoritesOnly || this.sort.key === 'fav') this.applyFilters(this.filters.sel, this.filters.query);
    return on;
  }

  private effectiveColumns(): ColKey[] {
    return (isCompact() ? this.prefs.mobileColumns : this.prefs.columns) as ColKey[];
  }

  private setLoop(on: boolean): void {
    this.engine.setLoop(on);
    this.transport.setLoop(this.engine.timeline.loop);
    this.syncUrl();
  }

  private toggleMute(): void {
    this.engine.setMuted(!this.engine.isMuted);
    this.transport.setMuted(this.engine.isMuted);
  }

  private toggleSort(key: ColKey): void {
    if (this.sort.key !== key) this.sort = { key, dir: 1 };
    else if (this.sort.dir === 1) this.sort = { key, dir: -1 };
    else this.sort = { key: null, dir: 1 };
    this.list.setSort(this.sort);
    this.applyFilters(this.filters.sel, this.filters.query);
  }

  private orderVisible(ids: string[]): string[] {
    let out = ids;
    if (this.favoritesOnly) out = out.filter((id) => this.favorites.has(id));
    if (this.sort.key) {
      out = sortIds(out, this.sort.key, this.sort.dir, this.canonicalIndex, { catalog: this.catalog, set: this.set, ...this.rowHooks });
    }
    return out;
  }

  private applyFilters(sel: Selection, query: string, initial = false): void {
    const audible = this.engine.audible;
    this.visible = this.orderVisible(this.index.apply(sel, query));
    this.list.setItems(this.visible);
    this.filters.updateHidden(this.set.order.length, this.visible.length);
    const keep = audible ?? this.visible[this.cursor];
    const i = keep ? this.visible.indexOf(keep) : -1;
    this.cursor = i >= 0 ? i : Math.min(this.cursor, Math.max(0, this.visible.length - 1));
    this.policy.syncCursor(this.cursor);
    this.list.select(this.cursor, !initial);
    this.engine.setOrder(this.visible, this.cursor);
    this.syncUrl();
  }

  private stepSong(d: number): void {
    const id = adjacentTrackId(this.songs.songs, this.song.id, d);
    if (id) this.switchSong(id);
  }

  private setTheme(t: ThemeName): void {
    this.theme = t;
    applyTheme(t);
    this.syncFontCycler();
    const sel = this.header.querySelector('.themepick') as HTMLSelectElement | null;
    if (sel) sel.value = t;
    this.syncUrl();
  }

  private cycleModernFont(): void {
    if (this.theme !== 'modern') return;
    this.modernFontId = nextModernFont(this.modernFontId);
    applyModernFont(this.modernFontId);
    saveModernFont(this.modernFontId);
    this.syncFontCycler();
  }

  private syncFontCycler(): void {
    if (!this.title) return;
    if (this.theme !== 'modern') {
      this.title.removeAttribute('role');
      this.title.removeAttribute('tabindex');
      this.title.removeAttribute('title');
      this.title.removeAttribute('aria-label');
      return;
    }
    const font = modernFont(this.modernFontId);
    this.title.setAttribute('role', 'button');
    this.title.tabIndex = 0;
    this.title.title = `Modern font: ${font.label}. Click to cycle.`;
    this.title.setAttribute('aria-label', `Soundfont Explorer - ${location.host}. Modern font: ${font.label}. Click to cycle.`);
  }

  private onStatus(s: Status): void {
    const txt = s.kind === 'loading' ? `loading ${s.target ?? ''}…` : s.kind === 'wontload' ? s.message : s.kind === 'ended' ? 'end' : s.kind === 'paused' ? 'paused' : s.kind === 'stopped' ? 'stopped' : '';
    this.transport.setStatus(txt, s.kind);
    this.nowPlaying.setStatus(txt, s.kind);
    this.list.setLoading(s.kind === 'loading' ? (s.target ?? null) : null);
  }

  private syncUrl(immediate = false): void {
    if (this.urlTimer) clearTimeout(this.urlTimer);
    const write = () => {
      this.urlTimer = null;
      writeUrl({
        song: this.song.id,
        variant: this.engine.audible ?? undefined,
        t: this.engine.position(),
        filters: this.filters.sel,
        q: this.filters.query,
        theme: this.theme,
        loop: this.engine.timeline.loop,
      });
    };
    if (immediate) write();
    else this.urlTimer = setTimeout(write, POLICY.settleMs + 30);
  }

  /** credit the audible variant with real playback time (not while paused, loading or suspended) */
  private accumulateListened(now: number): void {
    const dt = this.lastListenTick ? (now - this.lastListenTick) / 1000 : 0;
    this.lastListenTick = now;
    const v = this.engine.audible;
    if (!v || !this.engine.playing || this.ctx.state !== 'running' || this.engine.status.kind !== 'playing' || dt <= 0 || dt > 1) return;
    const total = this.ledger.add(this.song.id, v, dt);
    if (total >= this.prefs.listenedAfterS) this.list.markListened(v);
  }

  /** setColumns() rebuilds rows: restore cursor/audible/listened/favorite marks */
  private applyHighlightsAfterColumns(): void {
    this.list.setFavorites(this.favorites.all());
    this.refreshListened();
    this.list.setAudible(this.engine.audible);
    this.list.select(this.cursor, false);
  }

  private refreshListened(): void {
    if (this.list && this.song) this.list.setListened(this.ledger.listened(this.song.id, this.prefs.listenedAfterS));
  }

  /** debug panel refresh rate; the transport and the listened ledger still update every frame */
  private static DEBUG_HZ = 5;
  private debugAt = 0;

  /** per-frame UI loop; started once by boot() after the first song (and the engine) exist */
  private tickUi(): void {
    const loop = () => {
      const now = performance.now();
      this.transport.update(this.engine.position(), this.engine.playing);
      const resume = resumeNotice(this.ctx.state, this.engine.playing, this.suspendedNotice);
      if (resume.notice) this.transport.setStatus(resume.notice, 'wontload');
      // the context came back without an engine status change: repaint the engine's own status
      else if (resume.restore) this.onStatus(this.engine.status);
      this.suspendedNotice = resume.suspended;
      this.accumulateListened(now);
      if (this.debug.visible && now - this.debugAt >= 1000 / App.DEBUG_HZ) {
        this.debugAt = now;
        const f = this.fetcher.stats;
        const d = this.decoder.stats;
        const proto = (performance.getEntriesByType?.('resource') as PerformanceResourceTiming[] | undefined)?.at(-1)?.nextHopProtocol ?? '';
        const sess = audioSession();
        const nav = navigator as Navigator & {
          deviceMemory?: number;
          connection?: { effectiveType?: string; downlink?: number; rtt?: number; saveData?: boolean };
        };
        const conn = nav.connection;
        const heap = (performance as Performance & { memory?: { usedJSHeapSize: number; jsHeapSizeLimit: number } }).memory;
        this.debug.update(
          this.engine.snapshot({
            decodeKind: this.decodeKind,
            decodeAvgMs: d.decoded ? d.msTotal / d.decoded : 0,
            fetchAvgMs: f.completed ? f.msTotal / f.completed : 0,
            fetchBytes: f.bytes,
            fetchErrors: f.errors,
            inflight: this.fetcher.inflightCount,
            queued: this.fetcher.queuedCount,
          }),
          proto,
          {
            ...this.diag,
            track: this.song.id,
            variant: this.engine.audible ?? 'none',
            selection: `${this.cursor + 1}/${this.visible.length} visible (${this.set.order.length} total)`,
            position: `${this.engine.position().toFixed(2)} / ${this.set.duration_s.toFixed(2)} s`,
            tier: this.engine.currentTier ?? 'n/a',
            ctxState: this.ctx.state,
            audioSession: sess ? `${sess.type}/${sess.state ?? '?'}` : 'n/a',
            decodeQueue: `${d.active} active / ${d.queued} queued / ${d.errors} errors`,
            fetchTotal: `${f.completed}/${f.requests} completed / ${f.aborted} aborted`,
            decodedOk: this.store.stats.decodedOk,
            decodeErrors: this.store.stats.decodeErrors,
            fetchErrors: this.store.stats.fetchErrors,
            coverageStalls: this.store.stats.coverageStalls,
            lastError: this.store.lastError ?? '',
            audibleRms: this.engine.audibleRms(),
            status: `${this.engine.status.kind} ${this.engine.status.message}`.trim(),
            network: `${navigator.onLine ? 'online' : 'offline'}${conn ? ` / ${conn.effectiveType ?? '?'} / ${conn.downlink ?? '?'} Mbps / ${conn.rtt ?? '?'} ms RTT${conn.saveData ? ' / save-data' : ''}` : ''}`,
            page: `${innerWidth}×${innerHeight} @${devicePixelRatio}x / ${document.visibilityState}`,
            device: `${nav.hardwareConcurrency ?? '?'} cores / ${nav.deviceMemory ?? '?'} GiB`,
            jsHeap: heap ? `${fmtBytes(heap.usedJSHeapSize)} / ${fmtBytes(heap.jsHeapSizeLimit)}` : 'n/a',
            ua: navigator.userAgent.slice(0, 90),
          },
        );
      }
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }
}
