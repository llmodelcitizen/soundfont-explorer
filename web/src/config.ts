/**
 * The numbers (plan §4) — single source of truth shared with render/engines.json.
 * All times are SECONDS unless the name says Ms.
 */
export const IS_TOUCH =
  typeof navigator !== 'undefined' &&
  (navigator.maxTouchPoints > 0 || /iPhone|iPad|Android/i.test(navigator.userAgent));

export const AUDIO = {
  /** commit lead: how far ahead of "now" a switch is scheduled */
  COMMIT_LEAD: 0.015,
  /** min lead for any fresh start(); replaced by max(15 ms, 2·baseLatency + 5 ms) at runtime */
  startLead(baseLatency: number): number {
    return Math.max(0.015, 2 * baseLatency + 0.005);
  },
  SWITCH_XFADE: 0.01,
  SEAM_XFADE: 0.005,
  TIER_XFADE: 0.03,
  SWITCH_TIMEOUT_MS: 8000,
  LOOKAHEAD: 4,
  VOICES: 8,
  FILL_TICK_MS: 250,
  RETRY_TICK_MS: 60,
} as const;

export const CACHE = {
  decodedBytes: IS_TOUCH ? 24 * 1024 * 1024 : 128 * 1024 * 1024,
  compressedBytes: IS_TOUCH ? 32 * 1024 * 1024 : 96 * 1024 * 1024,
} as const;

export const NET = {
  inflightCap: IS_TOUCH ? 4 : 8,
  radiusMin: 4,
  radiusMax: IS_TOUCH ? 24 : 64,
  radiusSeconds: 1.5,
  directionPenalty: 2.5,
  /** fetch the whole pack when at least this many members are wanted, else Range per member */
  wholePackThreshold: 6,
  decodeWorkers: IS_TOUCH ? 2 : 4,
} as const;

export const POLICY = {
  mode: 'adaptive' as const,
  commitHz: 12,
  minDwellMs: 83,
  settleMs: 120,
  hysteresisMs: 250,
  adaptiveAfterMs: 1000,
  pageStep: 10,
  touchRepeatMs: 33,
} as const;

export const LOUDNESS = { lufsTarget: -16, tpCeiling: -1.5 } as const;
