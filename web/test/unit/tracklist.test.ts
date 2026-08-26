/**
 * @vitest-environment happy-dom
 *
 * The Tracks pane: the display-order rules, which are pure, and the caption's collapse gadget,
 * which is not — it needs rows to close and a store to write. What it looks like and where it sits
 * in the caption belong to scripts/geometry.mjs; what it does to the folders is here.
 */
import { beforeEach, describe, expect, it } from 'vitest';
import type { SongEntry } from '../../src/contracts/songs';
import { TrackList, adjacentTrackId, autoAdvanceTarget, trackMetadata, trackOrder } from '../../src/ui/tracklist';

const song = (id: string, path: string | null): SongEntry => ({ id, path } as SongEntry);

describe('track display order', () => {
  const songs = [
    song('first', null),
    song('folder-a', 'games/a'),
    song('last-folder-track', 'games/z'),
    // A top-level track can occur after folders in songs.json, but is displayed with the roots.
    song('starwars', null),
  ];

  it('groups top-level tracks before sorted folders', () => {
    expect(trackOrder(songs).map((s) => s.id)).toEqual([
      'first', 'starwars', 'folder-a', 'last-folder-track',
    ]);
  });

  it('wraps from the final displayed track to the first, in both directions', () => {
    expect(adjacentTrackId(songs, 'last-folder-track', 1)).toBe('first');
    expect(adjacentTrackId(songs, 'first', -1)).toBe('last-folder-track');
    expect(adjacentTrackId(songs, 'starwars', 1)).toBe('folder-a');
  });
});

describe('track metadata', () => {
  const metadata = (composer: string | null) => trackMetadata({ duration_s: 364, variant_count: 564, composer });

  it('omits the unknown-composer placeholder', () => {
    expect(metadata('unknown')).toBe('6:04 · 564 variants');
    expect(metadata(' Unknown ')).toBe('6:04 · 564 variants');
  });

  it('keeps known composers', () => {
    expect(metadata('John Williams')).toBe('6:04 · 564 variants · John Williams');
  });
});

describe('automatic next-track stepping', () => {
  const songs = [song('a', null), song('b', null), song('c', null)];
  const on = { loop: false, autoNext: true };

  it('steps to the next displayed track when the current one ends', () => {
    expect(autoAdvanceTarget(songs, 'a', 'ended', on)).toBe('b');
    expect(autoAdvanceTarget(songs, 'b', 'ended', on)).toBe('c');
  });

  it('follows the displayed order across folders', () => {
    const foldered = [song('root', null), song('x', 'demos'), song('y', 'demos')];
    expect(autoAdvanceTarget(foldered, 'root', 'ended', on)).toBe('x');
    expect(autoAdvanceTarget(foldered, 'x', 'ended', on)).toBe('y');
  });

  // [ and ] wrap because the user pressed them; stepping nobody asked for stops at the end of the
  // list instead of replaying the whole catalog for as long as the tab is open
  it('stops at the end of the list instead of wrapping round', () => {
    expect(autoAdvanceTarget(songs, 'c', 'ended', on)).toBeUndefined();
    expect(autoAdvanceTarget([song('solo', null)], 'solo', 'ended', on)).toBeUndefined();
  });

  it('stays put for every status other than the end of the song', () => {
    for (const kind of ['idle', 'loading', 'playing', 'paused', 'stopped', 'wontload'] as const) {
      expect(autoAdvanceTarget(songs, 'a', kind, on)).toBeUndefined();
    }
  });

  it('stays put while LOOP is on or the preference is off', () => {
    expect(autoAdvanceTarget(songs, 'a', 'ended', { loop: true, autoNext: true })).toBeUndefined();
    expect(autoAdvanceTarget(songs, 'a', 'ended', { loop: false, autoNext: false })).toBeUndefined();
  });

  it('stays put when the list does not hold the current track', () => {
    expect(autoAdvanceTarget([], 'solo', 'ended', on)).toBeUndefined();
    expect(autoAdvanceTarget(songs, 'gone', 'ended', on)).toBeUndefined();
  });
});

describe('collapsing every folder at once', () => {
  const OPEN_KEY = 'sfp.folders.v1';
  const catalog = [
    song('root-track', null),
    song('doom-1', 'games/doom'),
    song('doom-2', 'games/doom'),
    song('sierra-1', 'games/sierra'),
  ];

  /** a pane built over `songs` with `open` already in the store, as a reload would find it */
  const pane = (songs: SongEntry[], open: string[], current = 'root-track'): TrackList => {
    localStorage.setItem(OPEN_KEY, JSON.stringify(open));
    return new TrackList(songs, current, () => {}, {
      autoNext: { value: false, onChange: () => {} },
      preserve: { value: false, onChange: () => {} },
    });
  };
  const stored = (): string[] => JSON.parse(localStorage.getItem(OPEN_KEY) ?? 'null') as string[];
  const openFolders = (list: TrackList): string[] =>
    [...list.el.querySelectorAll('.track-folder')]
      .filter((folder) => folder.querySelector('.folder-twist')?.textContent === '▾')
      .map((folder) => folder.getAttribute('title') ?? '');
  const trackIds = (list: TrackList): (string | null)[] => [...list.el.querySelectorAll('.track')].map((row) => row.getAttribute('data-id'));

  beforeEach(() => localStorage.clear());

  it('closes every open folder, and their tracks go with them', () => {
    const list = pane(catalog, ['games/doom', 'games/sierra']);
    expect(openFolders(list)).toEqual(['games/doom', 'games/sierra']);
    list.collapseBtn.click();
    expect(openFolders(list)).toEqual([]);
    // both folder headers stay, closed; the only track left on show is the unfoldered one
    expect(list.el.querySelectorAll('.track-folder')).toHaveLength(2);
    expect(trackIds(list)).toEqual(['root-track']);
  });

  it('leaves the store saying exactly what the pane shows', () => {
    const list = pane(catalog, ['games/doom', 'games/sierra']);
    list.collapseBtn.click();
    expect(stored()).toEqual([]);
    // and a folder opened afterwards is the only one that comes back
    list.el.querySelector<HTMLElement>('.track-folder')!.click();
    expect(stored()).toEqual(['games/doom']);
    expect(openFolders(list)).toEqual(['games/doom']);
  });

  it('closes the folder holding the current track too — nothing is exempt', () => {
    const list = pane(catalog, ['games/doom'], 'doom-2');
    list.collapseBtn.click();
    expect(stored()).toEqual([]);
    // the invariant the pane already had: landing on that track opens its folder again
    list.setCurrent('doom-2');
    expect(stored()).toEqual(['games/doom']);
    expect(openFolders(list)).toEqual(['games/doom']);
  });

  it('offers nothing when there is nothing to close', () => {
    expect(pane(catalog, []).collapseBtn.disabled).toBe(true); // folders, all closed already
    expect(pane([song('a', null), song('b', null)], []).collapseBtn.disabled).toBe(true); // no folders at all
  });

  it('offers nothing when the store only names folders this catalogue does not have', () => {
    const list = pane(catalog, ['games/quake']);
    expect(list.collapseBtn.disabled).toBe(true);
    expect(stored()).toEqual(['games/quake']); // and the stale name is left alone, not quietly rewritten
  });

  it('goes dead as the last folder closes and lives again when one opens', () => {
    const list = pane(catalog, ['games/doom']);
    expect(list.collapseBtn.disabled).toBe(false);
    list.collapseBtn.click();
    expect(list.collapseBtn.disabled).toBe(true);
    list.el.querySelector<HTMLElement>('.track-folder')!.click();
    expect(list.collapseBtn.disabled).toBe(false);
  });

  it('announces what it does rather than shipping a bare drawing', () => {
    const button = pane(catalog, []).collapseBtn;
    expect(button.getAttribute('aria-label')).toBe('collapse all folders');
    expect(button.getAttribute('title')).toBe('collapse all folders');
    expect(button.textContent).toBe(''); // the glyph is an aria-hidden <svg>, not a text character
    expect(button.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true');
  });

  it('writes nothing for a click the disabled attribute cannot refuse', () => {
    const list = pane(catalog, []);
    localStorage.removeItem(OPEN_KEY);
    list.collapseBtn.click(); // a scripted click reaches a disabled button in some engines
    expect(localStorage.getItem(OPEN_KEY)).toBeNull(); // not even an empty set
    expect(stored()).toBeNull();
  });
});
