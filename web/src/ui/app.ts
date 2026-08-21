/**
 * App: loads songs.json → catalog → set, gates the AudioContext, wires engine ⇄ UI ⇄ URL.
 * Song switches keep variant id (or nearest index), position and filters (plan §10).
 */
import { Engine, type Status } from '../audio/engine';
import { NativeDecoder, WasmDecoder, probeNative, type Decoder } from '../audio/decode';
import { Fetcher } from '../audio/net/fetcher';
import { SegmentStore } from '../audio/store';
import type { ContextLike, Tier } from '../audio/types';
import { NET, POLICY } from '../config';
import { parseCatalog, type CatalogDoc } from '../contracts/catalog';
import { parseSet, type SetDoc } from '../contracts/set';
import { parseSongs, type SongEntry, type SongsDoc } from '../contracts/songs';
import { installKeyboard } from '../input/keyboard';
import { InputPolicy } from '../input/policy';
import { FilterIndex, type Selection } from '../state/filterIndex';
import { parseUrl, writeUrl, type UrlState } from '../state/urlstate';
import { renderCredits } from './credits';
import { DebugPanel } from './debug';
import { clear, h } from './dom';
import { FilterBar } from './filters';
import { showGate } from './gate';
import { KeymapOverlay } from './keymapOverlay';
import { VariantList } from './list';
import { NowPlaying } from './nowplaying';
import { SongPicker } from './songpicker';
import { SettingsModal } from './settings';
import { Favorites, ListenedLedger, loadPrefs, savePrefs, type Prefs } from '../state/prefs';
import { applyTheme, nextTheme, readTheme, type ThemeName } from './theme';
import { audioSession, createContextInGesture, installResumeOnGesture, unlock } from '../audio/unlock';
import { Transport } from './transport';

import probeUrl from '../assets/probe-1k-40ms.opus?url';

async function getJson(url: string): Promise<unknown> {
  const r = await fetch(url, { cache: url.endsWith('songs.json') ? 'no-cache' : 'default' });
  if (!r.ok) throw new Error(`HTTP ${r.status} for ${url}`);
  return r.json();
}

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
  private debug = new DebugPanel();
  private keymap = new KeymapOverlay();
  private prefs: Prefs = loadPrefs();
  private ledger = new ListenedLedger();
  private settings = new SettingsModal(this.prefs, {
    onChange: (p) => {
      this.prefs = p;
      savePrefs(p);
      this.refreshListened();
    },
    onResetTrack: () => {
      this.ledger.resetSong(this.song.id);
      this.refreshListened();
    },
    onResetAll: () => {
      this.ledger.resetAll();
      this.refreshListened();
    },
    trackTitle: () => this.song?.title ?? '',
  });
  private lastListenTick = 0;
  private favorites = new Favorites();
  private policy!: InputPolicy;
  private theme: ThemeName;
  private pinnedA: string | null = null;
  private url: UrlState;
  private urlTimer: ReturnType<typeof setTimeout> | null = null;
  private decodeKind = 'native';
  /** phone-readable diagnostics (debug panel) */
  private diag: Record<string, string | number> = {};
  private volume = 1;
  private main!: HTMLElement;
  private header!: HTMLElement;
  private uninstallKeys: (() => void) | null = null;

  constructor(root: HTMLElement) {
    this.root = root;
    this.url = parseUrl();
    this.theme = readTheme(this.url.theme);
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
    try {
      this.catalog = parseCatalog(await getJson(this.songs.catalog));
    } catch (e) {
      this.fatal(`Could not load the catalog: ${(e as Error).message}`);
      return;
    }
    const known = (id: string | null | undefined) => !!id && this.songs.songs.some((s) => s.id === id);
    const songId = known(this.url.song) ? this.url.song! : known(this.songs.defaults.song) ? this.songs.defaults.song! : this.songs.songs[0]!.id;
    await showGate(this.root, 'soundfonts.ericq.com', 'One song, hundreds of sound cards. Same notes, different decade — switch instruments mid-phrase and hear what your PC could have sounded like.', 'Start Listening', () => {
      // inside the tap: create + unlock the context synchronously (iOS requirement)
      this.ctx = createContextInGesture();
    });
    if (!this.ctx) this.ctx = createContextInGesture();
    if (this.ctx.state !== 'running') await this.ctx.resume().catch(() => undefined);
    this.diag.ctxCreatedState = this.ctx.state;
    await this.pickDecoder();
    this.installGlobalListeners();
    await this.loadSong(songId, { variant: this.url.variant, t: this.url.t, initial: true });
    this.tickUi();
    this.onHashChange();
  }

  /** window/document/AudioContext listeners — installed exactly once, not per song. */
  private installGlobalListeners(): void {
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && this.ctx.state !== 'running') unlock(this.ctx);
    });
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
    this.root.appendChild(h('div', { class: 'fatal' }, h('h1', null, 'soundfonts.ericq.com'), h('p', null, msg)));
  }

  private async pickDecoder(): Promise<void> {
    let bytes: ArrayBuffer | null = null;
    try {
      bytes = await (await fetch(probeUrl)).arrayBuffer();
    } catch {
      /* no probe → assume native */
    }
    const probe = bytes ? await probeNative(this.ctx as unknown as ContextLike, bytes) : { ok: true, reason: 'no probe', ms: 0 };
    this.diag.probe = `${probe.ok ? 'native ok' : 'native FAILED'}: ${probe.reason} (${probe.ms} ms)`;
    if (probe.ok) {
      this.decoder = new NativeDecoder(this.ctx as unknown as ContextLike, NET.decodeWorkers);
      this.decodeKind = 'native';
    } else {
      this.decoder = new WasmDecoder(this.ctx as unknown as ContextLike, NET.decodeWorkers);
      this.decodeKind = `wasm ×${NET.decodeWorkers} (${probe.reason})`;
    }
  }

  // ---------------------------------------------------------------- song lifecycle

  private async loadSong(id: string, opts: { variant?: string; t?: number; initial?: boolean; keepIndex?: number }): Promise<void> {
    const entry = this.songs.songs.find((s) => s.id === id);
    if (!entry) return;
    let set: SetDoc;
    try {
      set = parseSet(await getJson(entry.set));
    } catch (e) {
      const msg = `could not load ${entry.title}: ${(e as Error).message}`;
      if (this.engine) {
        // keep playing the current song; tell the user
        this.transport.setStatus(msg, 'wontload');
        this.picker.set(this.song.id);
        return;
      }
      this.fatal(`Could not load the song set for ${id}: ${(e as Error).message}`);
      return;
    }
    const prevPos = this.engine ? this.engine.position() : (opts.t ?? 0);
    const wasPlaying = this.engine ? this.engine.playing : false;
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
    this.engine = new Engine(this.ctx as unknown as ContextLike, set, this.store);
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
    const pos = Math.min(prevPos, set.duration_s);
    this.engine.seek(pos);
    if (wasPlaying || opts.initial) this.engine.play();
    this.transport.setMuted(muted);
    this.transport.setVolume(volume);
    this.syncUrl(true);
  }

  private buildUi(sel: Selection, query: string): void {
    if (this.uninstallKeys) this.uninstallKeys();
    clear(this.root);
    this.list = new VariantList(this.catalog, this.set, {
      onClick: (i) => this.policy.jump(i, performance.now()),
      onStickyClick: (id) => {
        this.filters.clearAll();
        const i = this.visible.indexOf(id);
        if (i >= 0) this.policy.jump(i, performance.now());
      },
    });
    this.filters = new FilterBar(this.index, sel, query, {
      onChange: (s, q) => this.applyFilters(s, q),
      onSearchEnter: () => {
        if (this.visible.length) this.policy.jump(0, performance.now());
        this.focusList();
      },
    });
    this.nowPlaying = new NowPlaying(this.catalog, this.set, this.song, {
      isFavorite: (id) => this.favorites.has(id),
      toggleFavorite: (id) => {
        const on = this.favorites.toggle(id);
        this.list.setFavorites(this.favorites.all());
        return on;
      },
    });
    this.transport = new Transport(
      this.set.duration_s,
      {
        onToggle: () => this.engine.toggle(),
        onSeek: (p) => this.engine.seek(p),
        onSkip: (d) => this.engine.seek(this.engine.position() + d),
        onLoop: (on) => {
          this.engine.setLoop(on);
          this.syncUrl();
        },
        onVolume: (v) => {
          this.volume = v;
          this.engine.setVolume(v);
        },
        onMute: () => {
          this.engine.setMuted(!this.engine.isMuted);
          this.transport.setMuted(this.engine.isMuted);
        },
        onStep: (d, rep) => this.policy.step(d, rep, performance.now()),
        onStepEnd: () => this.policy.keyup(performance.now()),
      },
      () => this.focusList(),
    );
    this.transport.setLoop(this.engine.timeline.loop);
    this.picker = new SongPicker(this.songs.songs, this.song.id, (id) => void this.loadSong(id, { keepIndex: this.cursor, variant: this.engine.audible ?? undefined }));
    const themeSel = h('select', { class: 'themepick', 'aria-label': 'theme', title: 'theme (T)' }, h('option', { value: 'modern' }, 'modern'), h('option', { value: 'win95' }, 'win95')) as HTMLSelectElement;
    themeSel.value = this.theme;
    themeSel.addEventListener('change', () => {
      this.setTheme(themeSel.value as ThemeName);
      themeSel.blur();
    });
    const helpBtn = h('button', { class: 'btn', type: 'button', title: 'keys (?)' }, '?');
    helpBtn.addEventListener('click', () => this.keymap.toggle());
    const dbgBtn = h('button', { class: 'btn dbg-btn', type: 'button', title: 'debug panel (D)' }, 'dbg');
    dbgBtn.addEventListener('click', () => this.debug.toggle());
    const settingsBtn = h('button', { class: 'btn', type: 'button', title: 'settings (S)', 'aria-label': 'settings' }, '⚙');
    settingsBtn.addEventListener('click', () => this.settings.toggle());
    this.header = h(
      'header',
      { class: 'top' },
      h('div', { class: 'title' }, h('span', { class: 'brand' }, 'soundfonts.ericq.com'), h('span', { class: 'sub' }, 'one MIDI · every synth')),
      this.picker.el,
      h('div', { class: 'spacer' }),
      themeSel,
      h('a', { class: 'btn link', href: '#/credits', title: 'credits, licenses, about' }, 'about'),
      settingsBtn,
      dbgBtn,
      helpBtn,
    );
    this.main = h('main', { class: 'main' }, h('section', { class: 'left' }, this.filters.el, this.list.el), this.nowPlaying.el);
    this.root.append(this.header, this.main, this.transport.el, this.debug.el, this.keymap.el, this.settings.el);
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
      toggle: () => this.engine.toggle(),
      skip: (s) => this.engine.seek(this.engine.position() + s),
      loop: () => {
        this.engine.setLoop(!this.engine.timeline.loop);
        this.transport.setLoop(this.engine.timeline.loop);
        this.syncUrl();
      },
      mute: () => {
        this.engine.setMuted(!this.engine.isMuted);
        this.transport.setMuted(this.engine.isMuted);
      },
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
    this.engine.on('status', (s) => this.onStatus(s));
    this.engine.on('audible', (v) => {
      this.list.setAudible(v);
      this.nowPlaying.show(v);
      this.syncUrl();
    });
    this.engine.on('tier', (t: Tier | null) => this.nowPlaying.setTier(t));
    this.applyFilters(sel, query, true);
    if (this.creditsEl) this.root.appendChild(this.creditsEl); // keep the About page on top across song loads
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

  private applyFilters(sel: Selection, query: string, initial = false): void {
    const audible = this.engine.audible;
    this.visible = this.index.apply(sel, query);
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
    this.ledger.flush();
    const ids = this.songs.songs.map((s) => s.id);
    const i = ids.indexOf(this.song.id);
    const next = ids[(i + d + ids.length) % ids.length]!;
    void this.loadSong(next, { keepIndex: this.cursor, variant: this.engine.audible ?? undefined });
  }

  private setTheme(t: ThemeName): void {
    this.theme = t;
    applyTheme(t);
    const sel = this.header.querySelector('.themepick') as HTMLSelectElement | null;
    if (sel) sel.value = t;
    this.syncUrl();
  }

  private onStatus(s: Status): void {
    const txt = s.kind === 'loading' ? `loading ${s.target ?? ''}…` : s.kind === 'wontload' ? s.message : s.kind === 'ended' ? 'end' : s.kind === 'paused' ? 'paused' : '';
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

  private uiLoopStarted = false;

  /** credit the audible variant with real playback time (not while paused, loading or suspended) */
  private accumulateListened(): void {
    const now = performance.now();
    const dt = this.lastListenTick ? (now - this.lastListenTick) / 1000 : 0;
    this.lastListenTick = now;
    const v = this.engine.audible;
    if (!v || !this.engine.playing || this.ctx.state !== 'running' || this.engine.status.kind !== 'playing' || dt <= 0 || dt > 1) return;
    const total = this.ledger.add(this.song.id, v, dt);
    if (total >= this.prefs.listenedAfterS) this.list.markListened(v);
  }

  private refreshListened(): void {
    if (this.list && this.song) this.list.setListened(this.ledger.listened(this.song.id, this.prefs.listenedAfterS));
  }

  private tickUi(): void {
    if (this.uiLoopStarted) return;
    this.uiLoopStarted = true;
    const loop = () => {
      if (!this.engine) {
        requestAnimationFrame(loop);
        return;
      }
      this.transport.update(this.engine.position(), this.engine.playing);
      if (this.ctx.state !== 'running' && this.engine.playing) this.transport.setStatus(`audio ${this.ctx.state} — tap to resume`, 'wontload');
      this.accumulateListened();
      if (this.debug.visible) {
        const f = this.fetcher.stats;
        const d = this.decoder.stats;
        const proto = (performance.getEntriesByType?.('resource') as PerformanceResourceTiming[] | undefined)?.at(-1)?.nextHopProtocol ?? '';
        const sess = audioSession();
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
            ctxState: this.ctx.state,
            audioSession: sess ? `${sess.type}/${sess.state ?? '?'}` : 'n/a',
            decodedOk: this.store.stats.decodedOk,
            decodeErrors: this.store.stats.decodeErrors,
            fetchErrors: this.store.stats.fetchErrors,
            lastError: this.store.lastError ?? '',
            audibleRms: this.engine.audibleRms(),
            status: `${this.engine.status.kind} ${this.engine.status.message}`.trim(),
            ua: navigator.userAgent.slice(0, 90),
          },
        );
      }
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }
}
