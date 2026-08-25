import { describe, expect, it } from 'vitest';
import type { SongEntry } from '../../src/contracts/songs';
import { adjacentTrackId, autoAdvanceTarget, trackMetadata, trackOrder } from '../../src/ui/tracklist';

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
    expect(autoAdvanceTarget(songs, 'c', 'ended', on)).toBe('a'); // wraps, like [ ]
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

  it('never re-loads the only track there is', () => {
    expect(autoAdvanceTarget([song('solo', null)], 'solo', 'ended', on)).toBeUndefined();
    expect(autoAdvanceTarget([], 'solo', 'ended', on)).toBeUndefined();
  });
});
