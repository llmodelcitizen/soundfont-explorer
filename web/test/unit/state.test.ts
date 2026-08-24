import { describe, expect, it } from 'vitest';
import { engineLabel, parseCatalog, tagNames } from '../../src/contracts/catalog';
import { parseSet } from '../../src/contracts/set';
import { ContractError, parseSongs, songTitle } from '../../src/contracts/songs';
import { FilterIndex } from '../../src/state/filterIndex';
import { buildSearch, decodeFilters, encodeFilters, parseUrl } from '../../src/state/urlstate';

const catalogDoc = {
  schema: 1,
  engines: [{ id: 'adlmidi', label: 'libADLMIDI', version: '1.6.2' }],
  facets: {},
  variants: [
    { id: 'adl-b0', label: 'AIL', engine: 'adlmidi', chip: 'opl3', type: 'fm', facets: { completeness: 'full_gm', lineage: 'fm_bank', size: '<2MB', decade: '1990s', quality: ['non_gm'] }, bank: { family: 'AIL', name: 'Fat Man', tags: { non_gm: true, fourop: false } }, aliases: [] },
    { id: 'adl-b1', label: 'Bisqwit', engine: 'adlmidi', chip: 'opl3', type: 'fm', facets: { completeness: 'full_gm', lineage: 'fm_bank', size: '<2MB', decade: 'unknown', quality: [] }, aliases: [] },
    { id: 'sf2-aaaaaaaaaa', label: 'Windows', engine: 'fluidsynth', chip: 'sf2', type: 'sampled', facets: { completeness: 'full_gm', lineage: 'roland', size: '2-16MB', decade: '1990s', quality: [] }, source: { file: 'Windows.sf2', sha256: 'a'.repeat(64), bytes: 3_000_000, sf2: { INAM: 'GS sound set (16 bit)', IENG: 'Roland' } }, aliases: [{ file: 'Twin.sf2', label: 'Twin' }] },
    { id: 'sf2-bbbbbbbbbb', label: 'Piano', engine: 'fluidsynth', chip: 'sf2', type: 'sampled', facets: { completeness: 'single_instrument', instrument: 'piano', lineage: 'generic', size: '16-100MB', decade: '2010s', quality: [] }, source: { file: 'Piano_X.sf2' }, aliases: [] },
  ],
};

describe('contracts', () => {
  it('parses songs.json and rejects bad shapes', () => {
    const d = parseSongs({ schema: 1, catalog: '/c/x.json', defaults: { song: 's', variant: 'v' }, songs: [{ id: 's', title: 'T', license: { id: 'CC0' }, duration_s: 10, set: '/s/s/h.json' }] });
    expect(d.songs[0]!.license.id).toBe('CC0');
    expect(d.songs[0]!.path).toBeNull(); // pre-path docs: top level
    expect(songTitle(d.songs[0]!)).toBe('T');
    expect(songTitle({ title: 'T', composer: 'C' })).toBe('T — C');
    const withPath = parseSongs({ schema: 1, catalog: '/c/x.json', songs: [{ id: 'p', title: 'L', license: {}, duration_s: 5, set: '/s/p/h.json', path: 'games/doom' }, { id: 'r', title: 'R', license: {}, duration_s: 5, set: '/s/r/h.json', path: '' }] });
    expect(withPath.songs[0]!.path).toBe('games/doom');
    expect(withPath.songs[1]!.path).toBeNull(); // empty string normalises to root
    expect(parseSongs({ schema: 1, catalog: '/c/x.json', songs: [] }).songs).toEqual([]); // empty boots gracefully
    expect(() => parseSongs({ schema: 2, catalog: '', songs: [] })).toThrow(ContractError);
    expect(() => parseSongs({ schema: 1, catalog: '/c', songs: [{ id: 's' }] })).toThrow(ContractError);
  });

  it('parses the catalog, normalising alias objects to strings and bank tags to names', () => {
    const c = parseCatalog(catalogDoc);
    expect(c.byId.get('sf2-aaaaaaaaaa')!.aliases).toEqual(['Twin.sf2 Twin']);
    expect(c.byId.get('sf2-aaaaaaaaaa')!.source!.sf2!.INAM).toBe('GS sound set (16 bit)');
    expect(c.byId.get('adl-b0')!.bank).toEqual({ kind: null, number: null, family: 'AIL', name: 'Fat Man', tags: ['non_gm'] });
    expect(c.byId.get('adl-b1')!.bank).toBeNull();
    expect(c.variants.length).toBe(4);
    expect(tagNames(['a', 'b'])).toEqual(['a', 'b']);
    expect(tagNames(null)).toEqual([]);
    expect(engineLabel(c.engines[0]!)).toBe('libADLMIDI 1.6.2');
    expect(engineLabel({ ...c.engines[0]!, commit: 'abc' })).toBe('libADLMIDI 1.6.2 (abc)');
  });

  it('validates set order/group/slot consistency', () => {
    const base = { schema: 1, song: 's', sr: 48000, duration_s: 8, slice_s: 2, slices: 4, lead_in_s: 0.12, lead_out_s: 0.02, segment_samples: 102720, listen: { slice_s: 10, slices: 1 }, scrub: { pack_size: 24 }, lufs_target: -16 };
    const ok = parseSet({ ...base, order: ['a'], groups: [{ hash: 'g', variants: ['a'] }], variants: { a: { render_hash: 'r', lufs: -20, gain_db: 4, tp: -1.5, group: 0, slot: 0 } } });
    expect(ok.order).toEqual(['a']);
    expect(() => parseSet({ ...base, order: ['a'], groups: [{ hash: 'g', variants: ['b'] }], variants: { a: { render_hash: 'r', lufs: -20, gain_db: 4, tp: -1.5, group: 0, slot: 0 } } })).toThrow(/group\/slot/);
    expect(() => parseSet({ ...base, order: ['zz'], groups: [], variants: {} })).toThrow(/unknown variant/);
  });
});

describe('FilterIndex', () => {
  const catalog = parseCatalog(catalogDoc);
  const order = ['adl-b0', 'adl-b1', 'sf2-aaaaaaaaaa', 'sf2-bbbbbbbbbb'];
  const idx = new FilterIndex(order, catalog);

  it('ORs within a facet and ANDs across facets, with counts ignoring own selection', () => {
    expect(idx.apply({})).toEqual(order);
    expect(idx.apply({ engine: new Set(['fluidsynth']) })).toEqual(['sf2-aaaaaaaaaa', 'sf2-bbbbbbbbbb']);
    expect(idx.apply({ engine: new Set(['fluidsynth', 'adlmidi']), decade: new Set(['1990s']) })).toEqual(['adl-b0', 'sf2-aaaaaaaaaa']);
    const counts = Object.fromEntries(idx.counts('engine', { engine: new Set(['fluidsynth']), decade: new Set(['1990s']) }).map((c) => [c.value, c.count]));
    expect(counts).toEqual({ adlmidi: 1, fluidsynth: 1 }); // own facet ignored, decade applied
    const q = Object.fromEntries(idx.counts('quality', {}).map((c) => [c.value, c.count]));
    expect(q).toEqual({ non_gm: 1, ok: 3 });
  });

  it('searches label, file, INFO, bank tags and aliases', () => {
    expect(idx.apply({}, 'twin')).toEqual(['sf2-aaaaaaaaaa']);
    expect(idx.apply({}, 'gs sound')).toEqual(['sf2-aaaaaaaaaa']);
    expect(idx.apply({}, 'non_gm')).toEqual(['adl-b0']);
    expect(idx.apply({}, 'piano_x')).toEqual(['sf2-bbbbbbbbbb']);
    expect(idx.apply({}, 'fat man')).toEqual(['adl-b0']);
    expect(idx.apply({ engine: new Set(['adlmidi']) }, 'piano')).toEqual([]);
  });

  it('ignores variants missing from the catalog', () => {
    const i2 = new FilterIndex(['adl-b0', 'ghost'], catalog);
    expect(i2.apply({})).toEqual(['adl-b0', 'ghost']);
    expect(i2.apply({ engine: new Set(['adlmidi']) })).toEqual(['adl-b0']);
  });
});

describe('URL state', () => {
  it('round-trips filters and query params', () => {
    const sel = { engine: new Set(['adlmidi', 'opnmidi']), size: new Set(['<2MB', '2-16MB']) };
    const enc = encodeFilters(sel);
    expect(enc).toBe('engine:adlmidi,opnmidi;size:2-16MB,%3C2MB'); // values sorted
    const dec = decodeFilters(enc);
    expect([...dec.engine!]).toEqual(['adlmidi', 'opnmidi']);
    expect([...dec.size!].sort()).toEqual(['2-16MB', '<2MB']);
    const s = buildSearch({ song: 'x', variant: 'adl-b0', t: 12.7, filters: sel, q: 'fat man', theme: 'win95', loop: true });
    const back = parseUrl(s);
    expect(back).toMatchObject({ song: 'x', variant: 'adl-b0', t: 13, q: 'fat man', theme: 'win95', loop: true });
    expect([...back.filters!.engine!]).toEqual(['adlmidi', 'opnmidi']);
    expect(buildSearch({ theme: 'modern', t: 0 })).toBe('');
  });

  it('round-trips the Amiga theme', () => {
    const search = buildSearch({ theme: 'amiga' });
    expect(search).toBe('?theme=amiga');
    expect(parseUrl(search).theme).toBe('amiga');
  });

  it('ignores hostile or malformed values', () => {
    const st = parseUrl('?t=-5&f=bogus:1;engine:adlmidi;;:x&loop=yes&song=<script>');
    expect(st.t).toBeUndefined();
    expect([...st.filters!.engine!]).toEqual(['adlmidi']);
    expect((st.filters as Record<string, unknown>)['bogus']).toBeUndefined();
    expect(st.loop).toBe(false);
    expect(st.song).toBe('<script>'); // harmless: only compared against known ids
    expect(parseUrl('?t=abc').t).toBeUndefined();
  });
});
