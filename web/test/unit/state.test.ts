import { describe, expect, it } from 'vitest';
import { SF2_INFO_KEYS, engineLabel, parseCatalog, tagNames } from '../../src/contracts/catalog';
import { parseSet } from '../../src/contracts/set';
import { ContractError, parseSongs, songTitle } from '../../src/contracts/songs';
import { FilterIndex } from '../../src/state/filterIndex';
import { URL_PARAM_HELP, URL_PARAM_ORDER, buildSearch, decodeFilters, encodeFilters, paramsIn, parseUrl, shareLinks, type UrlState } from '../../src/state/urlstate';

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

describe('the SF2 comment chunk is never surfaced', () => {
  // ICMT is free prose scraped from third-party fonts and full of e-mail addresses, so
  // catalog/variants.py deliberately does not publish it (issue #2). Nothing in the client may
  // display or index it, even if a stale catalog.json still carries the field.
  const catalog = parseCatalog({
    schema: 1,
    engines: [],
    facets: {},
    variants: [{ id: 'sf2-cccccccccc', label: 'Commented', engine: 'fluidsynth', chip: 'sf2', type: 'sampled', facets: {}, source: { file: 'C.sf2', sf2: { INAM: 'Commented', ICMT: 'mail me at someone@example.invalid' } }, aliases: [] }],
  });
  const idx = new FilterIndex(['sf2-cccccccccc'], catalog);

  it('is not part of the search text', () => {
    expect(idx.apply({}, 'commented')).toEqual(['sf2-cccccccccc']);
    expect(idx.apply({}, 'example.invalid')).toEqual([]);
    expect(idx.apply({}, 'mail me')).toEqual([]);
  });

  it('is not one of the INFO rows the now-playing panel renders', () => {
    expect(SF2_INFO_KEYS as readonly string[]).not.toContain('ICMT');
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

describe('FilterIndex facet arrays', () => {
  // The published catalog record for edm-all, verbatim: it carries `chip_family`, NOT a
  // top-level `chip`, so parseCatalog's fallback makes Variant.chip the joined 'opll,scc' and
  // only facets.chip holds the real list. (Before the fix that string was the whole chip facet
  // for edm-all: it appeared under neither OPLL nor SCC, and 'opll,scc' showed up as its own
  // option in the counts.)
  const catalog = parseCatalog({
    schema: 1,
    engines: [],
    facets: {},
    variants: [
      { id: 'edm-opll', label: 'OPLL', engine: 'edmidi', chip: 'opll', type: 'fm', facets: { chip: 'opll' }, aliases: [] },
      { id: 'edm-scc', label: 'SCC', engine: 'edmidi', chip: 'scc', type: 'fm', facets: { chip: 'scc' }, aliases: [] },
      { id: 'edm-all', label: 'OPLL + SCC', engine: 'edmidi', chip_family: 'opll', type: 'fm', facets: { chip: ['opll', 'scc'] }, aliases: [] },
      // empty facet lists occur in the published catalog (facets.quality is [] on every SF2
      // variant): they mean "nothing recorded here", not "no chip"
      { id: 'edm-empty', label: 'Empty list', engine: 'edmidi', chip: 'scc', type: 'fm', facets: { chip: [] }, aliases: [] },
      { id: 'edm-none', label: 'No chip at all', engine: 'edmidi', type: 'fm', facets: { chip: [] }, aliases: [] },
    ],
  });
  const idx = new FilterIndex(['edm-opll', 'edm-scc', 'edm-all', 'edm-empty', 'edm-none'], catalog);

  it('keeps every chip of a multi-chip variant (edm-all is listed under SCC as well as OPLL)', () => {
    expect(catalog.byId.get('edm-all')!.chip).toBe('opll,scc'); // what the published document parses to
    expect(idx.apply({ chip: new Set(['scc']) })).toEqual(['edm-scc', 'edm-all', 'edm-empty']);
    expect(idx.apply({ chip: new Set(['opll']) })).toEqual(['edm-opll', 'edm-all']);
    const counts = Object.fromEntries(idx.counts('chip', {}).map((c) => [c.value, c.count]));
    expect(counts).toEqual({ opll: 2, scc: 3, unknown: 1 }); // no phantom 'opll,scc' option
  });

  it('falls back to the top-level value for an empty facet list, and buckets the rest as unknown', () => {
    expect(idx.apply({ chip: new Set(['unknown']) })).toEqual(['edm-none']); // never invisible
  });
});

describe('URL state: cleared filters', () => {
  it('round-trips an empty selection as `f=` (distinct from no `f`, which means the default filters)', () => {
    expect(buildSearch({ filters: {} })).toBe('?f=');
    expect(parseUrl('?f=').filters).toEqual({});
    expect(parseUrl('').filters).toBeUndefined();
    expect(parseUrl(buildSearch({ song: 'x', filters: {} })).filters).toEqual({});
    expect(parseUrl(buildSearch({ filters: { engine: new Set(['adlmidi']) } })).filters).toEqual({ engine: new Set(['adlmidi']) });
  });
});

describe('URL parameter order', () => {
  // issue #30: one order everywhere the app writes its URL, with the start time always last so a
  // link can be trimmed back to "from the top" by cutting at the final `&`.
  const full: UrlState = { song: 'x', variant: 'adl-b0', filters: { engine: new Set(['adlmidi']) }, q: 'fat man', theme: 'win95', loop: true, t: 42 };

  it('writes every parameter in URL_PARAM_ORDER, t last', () => {
    expect(URL_PARAM_ORDER.at(-1)).toBe('t');
    const search = buildSearch(full);
    expect([...new URLSearchParams(search).keys()]).toEqual(['song', 'v', 'f', 'q', 'theme', 'loop', 't']);
    expect(search.endsWith('&t=42')).toBe(true);
    expect(paramsIn(search)).toEqual([...URL_PARAM_ORDER]);
  });

  it('keeps t last whichever other parameters are present', () => {
    for (const st of [{ t: 5 }, { song: 'x', t: 5 }, { theme: 'amiga', t: 5 }, { loop: true, t: 5 }, { q: 'z', t: 5 }, { filters: {}, t: 5 }] as UrlState[]) {
      expect([...new URLSearchParams(buildSearch(st)).keys()].at(-1)).toBe('t');
    }
  });

  it('still round-trips through parseUrl after the reorder', () => {
    const back = parseUrl(buildSearch(full));
    expect(back).toMatchObject({ song: 'x', variant: 'adl-b0', q: 'fat man', theme: 'win95', loop: true, t: 42 });
    expect([...back.filters!.engine!]).toEqual(['adlmidi']);
  });

  it('leaves out a start time that rounds to zero', () => {
    // the *written* value decides: `t=0` is not a start time, it is a useless extra parameter
    // that makes the share dialog's two links differ for no reason
    expect(buildSearch({ song: 'x', t: 0.4 })).toBe('?song=x');
    expect(buildSearch({ song: 'x', t: 0.5 })).toBe('?song=x&t=1');
    expect(paramsIn(buildSearch({ song: 'x', t: 0.4 }))).toEqual(['song']);
    expect(shareLinks({ song: 'x', t: 0.4 }, 'https://e.test/').withTime).toBe('https://e.test/?song=x');
  });

  it('lists only the parameters a link actually carries', () => {
    expect(paramsIn(buildSearch({ song: 'x', t: 9 }))).toEqual(['song', 't']);
    expect(paramsIn('')).toEqual([]);
    expect(paramsIn('?nonsense=1')).toEqual([]);
  });
});

describe('share links', () => {
  const base = 'https://example.test/';
  const st: UrlState = { song: 'x', variant: 'adl-b0', theme: 'amiga', t: 42.6 };

  it('offers the same link with and without the timestamp', () => {
    const { withTime, withoutTime } = shareLinks(st, base);
    expect(withoutTime).toBe(`${base}?song=x&v=adl-b0&theme=amiga`);
    expect(withTime).toBe(`${base}?song=x&v=adl-b0&theme=amiga&t=43`);
    // the two differ only by the trailing t=, because t is written last
    expect(withTime.startsWith(withoutTime)).toBe(true);
    expect(withTime.slice(withoutTime.length)).toBe('&t=43');
    expect(parseUrl(withTime.slice(base.length)).t).toBe(43);
    expect(parseUrl(withoutTime.slice(base.length)).t).toBeUndefined();
  });

  it('leaves the state it was given alone, and collapses to one link at the top of the track', () => {
    const input: UrlState = { song: 'x', t: 42 };
    shareLinks(input, base);
    expect(input.t).toBe(42);
    const top = shareLinks({ song: 'x', t: 0 }, base);
    expect(top.withTime).toBe(top.withoutTime);
    expect(top.withTime).toBe(`${base}?song=x`);
  });

  it('explains every parameter it can write', () => {
    expect(Object.keys(URL_PARAM_HELP).sort()).toEqual([...URL_PARAM_ORDER].sort());
    for (const key of URL_PARAM_ORDER) expect(URL_PARAM_HELP[key].length).toBeGreaterThan(10);
  });
});
