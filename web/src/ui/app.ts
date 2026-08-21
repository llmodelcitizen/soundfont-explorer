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
import { applyTheme, nextTheme, readTheme, type ThemeName } from './theme';
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
  private policy!: InputPolicy;
  private theme: ThemeName;
  private pinnedA: string | null = null;
  private url: UrlState;
  private urlTimer: ReturnType<typeof setTimeout> | null = null;
  private decodeKind = 'native';
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
    if (location.hash.startsWith('#/credits')) {
      try {
        this.songs = parseSongs(await getJson('/songs.json'));
        this.catalog = parseCatalog(await getJson(this.songs.catalog));
      } catch (e) {
        this.fatal(`Could not load the catalog: ${(e as Error).message}`);
        return;
      }
      clear(this.root);
      this.root.appendChild(renderCredits(this.songs, this.catalog));
      window.addEventListener('hashchange', () => location.reload(), { once: true });
      return;
    }
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
    const songId = this.url.song && this.songs.songs.some((s) => s.id === this.url.song) ? this.url.song : (this.songs.defaults.song ?? this.songs.songs[0]!.id);
    await showGate(this.root, 'soundfonts.ericq.com', 'Hear one MIDI through hundreds of SoundFonts and synth chips. Hold ↓ to scrub; the music never stops.', 'Start');
    this.ctx = new AudioContext({ latencyHint: 'interactive' });
    if (this.ctx.state !== 'running') await this.ctx.resume().catch(() => undefined);
    await this.pickDecoder();
    await this.loadSong(songId, { variant: this.url.variant, t: this.url.t, initial: true });
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
      this.fatal(`Could not load the song set for ${id}: ${(e as Error).message}`);
      return;
    }
    const prevPos = this.engine ? this.engine.position() : (opts.t ?? 0);
    const wasPlaying = this.engine ? this.engine.playing : false;
    const prevFilters = this.filters ? this.filters.sel : (this.url.filters ?? { completeness: new Set(['full_gm']) });
    const prevQuery = this.filters ? this.filters.query : (this.url.q ?? '');
    const loop = this.engine ? this.engine.timeline.loop : !!this.url.loop;
    const volume = this.engine ? 1 : 1;
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
    this.syncUrl(true);
    this.tickUi();
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
    this.nowPlaying = new NowPlaying(this.catalog, this.set, this.song);
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
        onVolume: (v) => this.engine.setVolume(v),
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
    const themeSel = h('select', { class: 'themepick', 'aria-label': 'theme', title: 'theme (T)' }, h('option', { value: 'dark' }, 'dark'), h('option', { value: 'win95' }, 'win95'), h('option', { value: 'system' }, 'system')) as HTMLSelectElement;
    themeSel.value = this.theme;
    themeSel.addEventListener('change', () => {
      this.setTheme(themeSel.value as ThemeName);
      themeSel.blur();
    });
    const helpBtn = h('button', { class: 'btn', type: 'button', title: 'keys (?)' }, '?');
    helpBtn.addEventListener('click', () => this.keymap.toggle());
    this.header = h(
      'header',
      { class: 'top' },
      h('div', { class: 'title' }, h('span', { class: 'brand' }, 'soundfonts.ericq.com'), h('span', { class: 'sub' }, 'one MIDI · every synth')),
      this.picker.el,
      h('div', { class: 'spacer' }),
      themeSel,
      h('a', { class: 'btn link', href: '#/credits', title: 'credits, licenses, about' }, 'about'),
      helpBtn,
    );
    this.main = h('main', { class: 'main' }, h('section', { class: 'left' }, this.filters.el, this.list.el), this.nowPlaying.el);
    this.root.append(this.header, this.main, this.transport.el, this.debug.el, this.keymap.el);
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
        this.filters.toggle(false);
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
    });
    this.engine.on('status', (s) => this.onStatus(s));
    this.engine.on('audible', (v) => {
      this.list.setAudible(v);
      this.nowPlaying.show(v);
      this.syncUrl();
    });
    this.engine.on('tier', (t: Tier | null) => this.nowPlaying.setTier(t));
    this.applyFilters(sel, query, true);
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && this.ctx.state !== 'running') void this.ctx.resume();
    });
    this.ctx.addEventListener?.('statechange', () => {
      if (this.ctx.state === 'suspended' || (this.ctx.state as string) === 'interrupted') {
        void showGate(this.root, 'audio paused', 'The browser suspended audio. Tap to resume.', 'Resume').then(() => this.ctx.resume());
      }
    });
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

  private tickUi(): void {
    const loop = () => {
      if (!this.engine) return;
      this.transport.update(this.engine.position(), this.engine.playing);
      if (this.debug.visible) {
        const f = this.fetcher.stats;
        const d = this.decoder.stats;
        const proto = (performance.getEntriesByType?.('resource') as PerformanceResourceTiming[] | undefined)?.at(-1)?.nextHopProtocol ?? '';
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
        );
      }
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }
}
