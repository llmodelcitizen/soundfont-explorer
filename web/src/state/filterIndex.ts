/**
 * Facet filtering over the song's `order[]` (what was rendered), with live counts.
 * OR within a facet, AND across facets; counts for a facet are computed with that facet's own
 * selection removed (standard faceted-search behaviour), so options never show zero just
 * because the user picked a sibling.
 */
import { tagNames, type CatalogDoc, type Variant } from '../contracts/catalog';

export const FACET_KEYS = ['engine', 'chip', 'type', 'completeness', 'bank_map', 'size', 'lineage', 'decade', 'quality'] as const;
export type FacetKey = (typeof FACET_KEYS)[number];

export const FACET_LABELS: Record<FacetKey, string> = {
  engine: 'Engine',
  chip: 'Chip',
  type: 'Type',
  completeness: 'Coverage',
  bank_map: 'Bank map',
  size: 'Size',
  lineage: 'Lineage',
  decade: 'Decade',
  quality: 'Quality',
};

export type Selection = Partial<Record<FacetKey, Set<string>>>;
export type FacetCounts = { value: string; count: number }[];

function facetValues(v: Variant, key: FacetKey): string[] {
  const f = v.facets ?? {};
  let raw: unknown = f[key];
  // the top-level engine/chip/type strings mirror single facet values; a multi-chip variant
  // (edm-all: chip 'opll', facets.chip ['opll', 'scc']) keeps the full list only in facets
  if (!Array.isArray(raw)) {
    if (key === 'engine') raw = v.engine || raw;
    if (key === 'chip') raw = v.chip || raw;
    if (key === 'type') raw = v.type || raw;
  }
  if (key === 'quality') {
    const tags = tagNames(raw);
    return tags.length ? tags : ['ok'];
  }
  if (Array.isArray(raw)) return raw.map(String);
  if (raw === null || raw === undefined || raw === '') return ['unknown'];
  return [String(raw)];
}

export class FilterIndex {
  readonly order: string[];
  private bits = new Map<FacetKey, Map<string, Uint8Array>>();
  private searchText: string[];

  constructor(order: string[], catalog: CatalogDoc) {
    this.order = order;
    for (const key of FACET_KEYS) this.bits.set(key, new Map());
    this.searchText = order.map((id) => {
      const v = catalog.byId.get(id);
      if (!v) return id.toLowerCase();
      const info = v.source?.sf2;
      return [v.id, v.label, v.slug, v.source?.file, info?.INAM, info?.IENG, v.bank?.family, v.bank?.name, ...(v.bank?.tags ?? []), ...v.aliases]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
    });
    order.forEach((id, i) => {
      const v = catalog.byId.get(id);
      if (!v) return;
      for (const key of FACET_KEYS) {
        const m = this.bits.get(key)!;
        for (const val of facetValues(v, key)) {
          let arr = m.get(val);
          if (!arr) {
            arr = new Uint8Array(order.length);
            m.set(val, arr);
          }
          arr[i] = 1;
        }
      }
    });
  }

  /** rows matching every whitespace-separated term of the text query; null when there is no query */
  private textMask(query: string): Uint8Array | null {
    const q = query.trim().toLowerCase();
    if (!q) return null;
    const terms = q.split(/\s+/);
    return Uint8Array.from(this.searchText, (t) => (terms.every((term) => t.includes(term)) ? 1 : 0));
  }

  /** mask of rows matching `sel` (optionally ignoring one facet) and the text query */
  mask(sel: Selection, query = '', ignore?: FacetKey): Uint8Array {
    return this.selMask(sel, ignore, this.textMask(query));
  }

  private selMask(sel: Selection, ignore: FacetKey | undefined, text: Uint8Array | null): Uint8Array {
    const n = this.order.length;
    const out = text ? text.slice() : new Uint8Array(n).fill(1);
    for (const key of FACET_KEYS) {
      if (key === ignore) continue;
      const chosen = sel[key];
      if (!chosen || chosen.size === 0) continue;
      const m = this.bits.get(key)!;
      const any = new Uint8Array(n);
      for (const val of chosen) {
        const arr = m.get(val);
        if (!arr) continue;
        for (let i = 0; i < n; i++) if (arr[i]) any[i] = 1;
      }
      for (let i = 0; i < n; i++) if (!any[i]) out[i] = 0;
    }
    return out;
  }

  apply(sel: Selection, query = ''): string[] {
    const m = this.mask(sel, query);
    const res: string[] = [];
    for (let i = 0; i < this.order.length; i++) if (m[i]) res.push(this.order[i]!);
    return res;
  }

  /** counts per value for one facet given the other facets' selections */
  counts(key: FacetKey, sel: Selection, query = ''): FacetCounts {
    return this.countsIn(key, sel, this.textMask(query));
  }

  /** counts for every facet; the text query is scanned once, not once per facet */
  allCounts(sel: Selection, query = ''): Record<FacetKey, FacetCounts> {
    const text = this.textMask(query);
    return Object.fromEntries(FACET_KEYS.map((key) => [key, this.countsIn(key, sel, text)])) as Record<FacetKey, FacetCounts>;
  }

  private countsIn(key: FacetKey, sel: Selection, text: Uint8Array | null): FacetCounts {
    const base = this.selMask(sel, key, text);
    const m = this.bits.get(key)!;
    const out: FacetCounts = [];
    for (const [val, arr] of m) {
      let c = 0;
      for (let i = 0; i < arr.length; i++) if (arr[i] && base[i]) c++;
      out.push({ value: val, count: c });
    }
    return out.sort((a, b) => a.value.localeCompare(b.value));
  }
}
