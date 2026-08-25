/**
 * localStorage that never throws. Private mode, a full quota, storage blocked by policy and
 * "no localStorage at all" (the unit tests run in node) all read as a miss and swallow a
 * write: whatever the caller holds in memory still applies, the preference just does not
 * survive the reload.
 *
 * The global is read inside each call, never at module load, so a test that stubs
 * `localStorage` after import still sees its stub — and so a page that boots before the
 * store is available is not stuck with a broken reference.
 *
 * This module exists twice, byte for byte: web/src/state/storage.ts and
 * admin/web/src/storage.ts. The two SPAs are separate npm projects with include: ["src"] and
 * ship separately, so a helper both need is copied, not cross-imported;
 * web/test/unit/storage.test.ts compares the two files so the copies cannot drift.
 */
export const safeStorage = {
  /** The stored string, or null for a miss or a store that cannot be read. */
  get(key: string): string | null {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  },

  set(key: string, value: string): void {
    try {
      localStorage.setItem(key, value);
    } catch {
      /* private mode / quota: keep in memory only */
    }
  },

  remove(key: string): void {
    try {
      localStorage.removeItem(key);
    } catch {
      /* private mode */
    }
  },

  /** Parsed JSON, or null for a miss, an unreadable store or a malformed value. */
  getJson<T>(key: string): T | null {
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? null : (JSON.parse(raw) as T);
    } catch {
      return null;
    }
  },

  /** Serialise inside the guard, the way every call site did: an unserialisable value is
   *  dropped rather than thrown at the caller. */
  setJson(key: string, value: unknown): void {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* private mode / quota / unserialisable: keep in memory only */
    }
  },
};
