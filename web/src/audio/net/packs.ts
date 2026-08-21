/** SFPK v1 — "SFPK" · u8 version · u8 count · u16 reserved · u32le len[n] · members. */
export const SFPK_MAGIC = 0x4b504653; // "SFPK" little-endian u32

export interface PackHeader {
  count: number;
  lengths: number[];
  /** absolute byte offset of member j within the pack */
  offsets: number[];
  headerBytes: number;
}

export function headerBytesFor(count: number): number {
  return 8 + 4 * count;
}

/** Member count from the fixed 8-byte prefix (no validation: see parseHeader). */
export function memberCount(buf: ArrayBuffer, byteOffset = 0): number {
  return new DataView(buf, byteOffset).getUint8(5);
}

/** Inclusive byte range of member `slot` within the pack (for a Range request). */
export function memberRange(h: PackHeader, slot: number): { start: number; end: number } {
  const start = h.offsets[slot]!;
  return { start, end: start + h.lengths[slot]! - 1 };
}

export function parseHeader(buf: ArrayBuffer, byteOffset = 0): PackHeader {
  const dv = new DataView(buf, byteOffset);
  if (dv.byteLength < 8) throw new Error('SFPK: short header');
  if (dv.getUint32(0, true) !== SFPK_MAGIC) throw new Error('SFPK: bad magic');
  const version = dv.getUint8(4);
  if (version !== 1) throw new Error(`SFPK: unsupported version ${version}`);
  const count = memberCount(buf, byteOffset);
  const headerBytes = headerBytesFor(count);
  if (dv.byteLength < headerBytes) throw new Error('SFPK: truncated length table');
  const lengths: number[] = [];
  const offsets: number[] = [];
  let off = headerBytes;
  for (let j = 0; j < count; j++) {
    const len = dv.getUint32(8 + 4 * j, true);
    lengths.push(len);
    offsets.push(off);
    off += len;
  }
  return { count, lengths, offsets, headerBytes };
}

export function splitPack(buf: ArrayBuffer): ArrayBuffer[] {
  const h = parseHeader(buf);
  const total = h.offsets[h.count - 1]! + h.lengths[h.count - 1]!;
  if (h.count === 0) return [];
  if (buf.byteLength !== total) throw new Error(`SFPK: size mismatch ${buf.byteLength} != ${total}`);
  return h.lengths.map((len, j) => buf.slice(h.offsets[j]!, h.offsets[j]! + len));
}
