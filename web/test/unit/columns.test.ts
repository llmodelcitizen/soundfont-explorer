import { describe, expect, it } from 'vitest';
import { parseCatalog } from '../../src/contracts/catalog';
import { DEFAULT_PREFS } from '../../src/state/prefs';
import { COLUMNS, cellText, columnDef, displayLabel, nextSort, rowMinWidth, sortIds, visibleColumns, type CellContext, type ColKey } from '../../src/ui/columns';
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

  it('keeps the cell classes the stylesheet keys on and renders fav/dot as fixed glyphs', () => {
    const expected: Record<ColKey, string> = { idx: 'idx', chip: 'badge', label: 'label', engine: 'meta', id: 'meta', bank: 'meta', size: 'meta', decade: 'meta', lineage: 'meta', coverage: 'meta', lufs: 'num meta', gain: 'num meta', fav: 'fav', dot: 'dot' };
    for (const c of COLUMNS) expect([c.key, c.cls]).toEqual([c.key, expected[c.key]]);
    expect(columnDef('fav').glyph).toBe('♥');
    expect(columnDef('dot').glyph).toBe('');
    expect(COLUMNS.filter((c) => c.glyph !== undefined).map((c) => c.key)).toEqual(['fav', 'dot']);
  });

  it('visibleColumns forces the always-on columns and keeps model order', () => {
    expect(visibleColumns(DEFAULT_PREFS.columns).map((c) => c.key)).toEqual(['idx', 'chip', 'label', 'engine', 'decade', 'fav', 'dot']);
    expect(visibleColumns(DEFAULT_PREFS.mobileColumns).map((c) => c.key)).toEqual(['idx', 'chip', 'label', 'fav', 'dot']);
    expect(visibleColumns(['dot', 'size', 'bogus']).map((c) => c.key)).toEqual(['idx', 'label', 'size', 'dot']);
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

describe('header clicks', () => {
  it('cycles a sortable column ascending, descending, catalog order', () => {
    expect(nextSort({ key: null, dir: 1 }, 'size')).toEqual({ key: 'size', dir: 1 });
    expect(nextSort({ key: 'size', dir: 1 }, 'size')).toEqual({ key: 'size', dir: -1 });
    expect(nextSort({ key: 'size', dir: -1 }, 'size')).toEqual({ key: null, dir: 1 });
    expect(nextSort({ key: 'size', dir: -1 }, 'decade')).toEqual({ key: 'decade', dir: 1 });
  });

  it('goes straight to the catalog order for # — row numbers are never reversed', () => {
    expect(columnDef('idx').resetsSort).toBe(true);
    expect(nextSort({ key: null, dir: 1 }, 'idx')).toEqual({ key: null, dir: 1 });
    expect(nextSort({ key: 'size', dir: 1 }, 'idx')).toEqual({ key: null, dir: 1 });
    expect(nextSort({ key: 'size', dir: -1 }, 'idx')).toEqual({ key: null, dir: 1 });
  });

  it('starts an ascending sort on a key it does not know instead of throwing', () => {
    expect(nextSort({ key: null, dir: 1 }, 'nope' as ColKey)).toEqual({ key: 'nope', dir: 1 });
  });

  it('leaves every other column on the three-step cycle', () => {
    for (const c of COLUMNS.filter((x) => x.key !== 'idx')) {
      expect([c.key, nextSort({ key: null, dir: 1 }, c.key)]).toEqual([c.key, { key: c.key, dir: 1 }]);
    }
  });
});

describe('row min width', () => {
  it('is computed from the column definitions, not measured on a detached list', () => {
    // idx 3.2 + chip 4.6 + label minmax(14em) + fav 1.4 + dot 1.4 = 24.6em at the 14px design em;
    // gaps 8px × 4 + 26px padding
    expect(rowMinWidth(visibleColumns(['chip', 'fav', 'dot']))).toBe('402px');
    // always-on columns only: idx + label
    expect(rowMinWidth(visibleColumns([]))).toBe('275px');
  });

  it('does not depend on the theme font size (geometry.mjs pins every theme to the modern one)', () => {
    // a value in em would resolve against the row's own font size: 11px under Windows 95, where
    // the same viewport would then not scroll sideways while the modern theme does
    expect(rowMinWidth(visibleColumns(['chip', 'fav', 'dot']))).not.toMatch(/em/);
  });
});
