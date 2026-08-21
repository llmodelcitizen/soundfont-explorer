import { ContractError, isObj } from './songs';

export interface SetVariant {
  render_hash: string;
  lufs: number;
  gain_db: number;
  tp: number;
  group: number;
  slot: number;
}

export interface SetDoc {
  schema: 1;
  song: string;
  sr: number;
  duration_s: number;
  slice_s: number;
  slices: number;
  lead_in_s: number;
  lead_out_s: number;
  segment_samples: number;
  listen: { slice_s: number; slices: number; bitrate: number; segment_samples?: number };
  scrub: { bitrate: number; pack_size: number };
  lufs_target: number;
  order: string[];
  groups: { hash: string; variants: string[] }[];
  variants: Record<string, SetVariant>;
  excluded: { id: string; reason: string }[];
}

const num = (x: unknown, what: string): number => {
  if (typeof x !== 'number' || !Number.isFinite(x)) throw new ContractError(`set: bad ${what}`);
  return x;
};

export function parseSet(raw: unknown): SetDoc {
  if (!isObj(raw) || raw.schema !== 1) throw new ContractError('bad set document');
  const listen = isObj(raw.listen) ? raw.listen : {};
  const scrub = isObj(raw.scrub) ? raw.scrub : {};
  const order = Array.isArray(raw.order) ? raw.order.map(String) : [];
  const groups = (Array.isArray(raw.groups) ? raw.groups : []).filter(isObj).map((g) => ({
    hash: String(g.hash),
    variants: Array.isArray(g.variants) ? g.variants.map(String) : [],
  }));
  const variants: Record<string, SetVariant> = Object.create(null) as Record<string, SetVariant>;
  if (isObj(raw.variants)) {
    for (const [id, v] of Object.entries(raw.variants)) {
      if (!isObj(v)) continue;
      variants[id] = {
        render_hash: String(v.render_hash),
        lufs: Number(v.lufs),
        gain_db: Number(v.gain_db),
        tp: Number(v.tp),
        group: num(v.group, `variants.${id}.group`),
        slot: num(v.slot, `variants.${id}.slot`),
      };
    }
  }
  for (const id of order) {
    const v = Object.hasOwn(variants, id) ? variants[id] : undefined;
    if (!v) throw new ContractError(`set: order references unknown variant ${id}`);
    const g = groups[v.group];
    if (!g || g.variants[v.slot] !== id) throw new ContractError(`set: group/slot mismatch for ${id}`);
  }
  return {
    schema: 1,
    song: String(raw.song),
    sr: num(raw.sr, 'sr'),
    duration_s: num(raw.duration_s, 'duration_s'),
    slice_s: num(raw.slice_s, 'slice_s'),
    slices: num(raw.slices, 'slices'),
    lead_in_s: num(raw.lead_in_s, 'lead_in_s'),
    lead_out_s: num(raw.lead_out_s, 'lead_out_s'),
    segment_samples: num(raw.segment_samples, 'segment_samples'),
    listen: {
      slice_s: num(listen.slice_s, 'listen.slice_s'),
      slices: num(listen.slices, 'listen.slices'),
      bitrate: Number(listen.bitrate) || 0,
      segment_samples: typeof listen.segment_samples === 'number' ? listen.segment_samples : undefined,
    },
    scrub: { bitrate: Number(scrub.bitrate) || 0, pack_size: num(scrub.pack_size, 'scrub.pack_size') },
    lufs_target: Number(raw.lufs_target) || -16,
    order,
    groups,
    variants,
    excluded: (Array.isArray(raw.excluded) ? raw.excluded : []).filter(isObj).map((e) => ({ id: String(e.id), reason: String(e.reason) })),
  };
}

/** URL helpers — same origin, immutable objects. */
export function packUrl(set: SetDoc, groupHash: string, slice: number): string {
  return `/a/${set.song}/g/${groupHash}/${String(slice).padStart(4, '0')}.pk`;
}
export function listenUrl(set: SetDoc, renderHash: string, k: number): string {
  return `/a/${set.song}/l/${renderHash}/${String(k).padStart(3, '0')}.opus`;
}
