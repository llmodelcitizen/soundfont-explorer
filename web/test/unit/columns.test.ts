import { describe, expect, it } from 'vitest';
import { parseCatalog } from '../../src/contracts/catalog';
import { cellText, displayLabel, sortIds, type CellContext } from '../../src/ui/columns';
import { makeSet } from './fakes';

const catalog = parseCatalog({
  schema: 1,
  engines: [],
  facets: {},
  variants: [
    { id: 'a', label: 'Alpha [OPL2]', engine: 'adlmidi', chip: 'opl2', type: 'fm', facets: { decade: '1990s' }, bank: { family: 'AIL' } },
    { id: 'b', label: 'Bravo', engine: 'fluidsynth', chip: 'sf2', type: 'sampled', facets: { decade: 'unknown' }, source: { file: 'b.sf2', bytes: 3_000_000 } },
    { id: 'c', label: 'Charlie', engine: 'fluidsynth', chip: 'sf2', type: 'sampled', facets: { decade: '2010s' }, source: { file: 'c.sf2', bytes: 1_000_000 } },
  ],
});
const { set } = makeSet(['a', 'b', 'c'], 8);
const ctx: CellContext = { catalog, set, isFavorite: (id) => id === 'c', listenedSeconds: (id) => (id === 'b' ? 5 : 0), listened: (id) => id === 'b' };
const canonical = new Map([['a', 0], ['b', 1], ['c', 2]]);

describe('columns', () => {
  it('strips chip tags from names and formats cells', () => {
    expect(displayLabel(catalog.byId.get('a'), 'a')).toBe('Alpha');
    expect(cellText('chip', 'a', 0, ctx)).toBe('OPL2');
    expect(cellText('size', 'a', 0, ctx)).toBe('');
    expect(cellText('size', 'b', 1, ctx)).toBe('2.9 MB');
    expect(cellText('decade', 'b', 1, ctx)).toBe('');
    expect(cellText('engine', 'b', 1, ctx)).toBe('FluidSynth');
  });

  it('sorts numbers/strings with blanks last in both directions, ties by catalog order', () => {
    expect(sortIds(['a', 'b', 'c'], 'size', 1, canonical, ctx)).toEqual(['c', 'b', 'a']);
    expect(sortIds(['a', 'b', 'c'], 'size', -1, canonical, ctx)).toEqual(['b', 'c', 'a']);
    expect(sortIds(['a', 'b', 'c'], 'decade', 1, canonical, ctx)).toEqual(['a', 'c', 'b']);
    expect(sortIds(['a', 'b', 'c'], 'decade', -1, canonical, ctx)).toEqual(['c', 'a', 'b']);
    expect(sortIds(['a', 'b', 'c'], 'fav', 1, canonical, ctx)).toEqual(['c', 'a', 'b']);
    expect(sortIds(['a', 'b', 'c'], 'dot', 1, canonical, ctx)).toEqual(['b', 'a', 'c']);
    expect(sortIds(['a', 'b', 'c'], 'label', -1, canonical, ctx)).toEqual(['c', 'b', 'a']);
  });
});
