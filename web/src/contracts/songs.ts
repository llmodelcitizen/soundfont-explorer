export interface License {
  id: string;
  url: string;
  notice_text: string;
}

export interface SongEntry {
  id: string;
  title: string;
  composer: string | null;
  sequencer: string | null;
  source_url: string | null;
  license: License;
  modifications: string;
  duration_s: number;
  variant_count: number;
  set: string;
}

export interface SongsDoc {
  schema: 1;
  generated_at: string;
  catalog: string;
  defaults: { song: string | null; variant: string };
  songs: SongEntry[];
}

export class ContractError extends Error {}

function req<T>(v: unknown, pred: (x: unknown) => boolean, what: string): T {
  if (!pred(v)) throw new ContractError(`bad ${what}: ${JSON.stringify(v)?.slice(0, 80)}`);
  return v as T;
}
const isObj = (x: unknown): x is Record<string, unknown> => typeof x === 'object' && x !== null;
const isStr = (x: unknown): x is string => typeof x === 'string';
const isNum = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x);

export function parseSongs(raw: unknown): SongsDoc {
  const d = req<Record<string, unknown>>(raw, isObj, 'songs.json');
  if (d.schema !== 1) throw new ContractError(`unsupported songs schema ${String(d.schema)}`);
  const songs = req<unknown[]>(d.songs, Array.isArray, 'songs[]').map((s, i) => {
    const o = req<Record<string, unknown>>(s, isObj, `songs[${i}]`);
    const lic = req<Record<string, unknown>>(o.license ?? {}, isObj, `songs[${i}].license`);
    return {
      id: req<string>(o.id, isStr, 'song.id'),
      title: isStr(o.title) ? o.title : String(o.id),
      composer: isStr(o.composer) ? o.composer : null,
      sequencer: isStr(o.sequencer) ? o.sequencer : null,
      source_url: isStr(o.source_url) ? o.source_url : null,
      license: {
        id: isStr(lic.id) ? lic.id : 'unknown',
        url: isStr(lic.url) ? lic.url : '',
        notice_text: isStr(lic.notice_text) ? lic.notice_text : '',
      },
      modifications: isStr(o.modifications) ? o.modifications : 'none',
      duration_s: req<number>(o.duration_s, isNum, 'song.duration_s'),
      variant_count: isNum(o.variant_count) ? o.variant_count : 0,
      set: req<string>(o.set, isStr, 'song.set'),
    } satisfies SongEntry;
  });
  const defaults = isObj(d.defaults) ? d.defaults : {};
  return {
    schema: 1,
    generated_at: isStr(d.generated_at) ? d.generated_at : '',
    catalog: req<string>(d.catalog, isStr, 'catalog'),
    defaults: {
      song: isStr(defaults.song) ? defaults.song : (songs[0]?.id ?? null),
      variant: isStr(defaults.variant) ? defaults.variant : 'adl-b58',
    },
    songs,
  };
}
