import { describe, expect, it } from 'vitest';
import type { SongEntry } from '../../src/contracts/songs';
import { adjacentTrackId, trackOrder } from '../../src/ui/tracklist';

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
