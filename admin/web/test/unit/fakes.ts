// Test doubles for the admin views: a route-table fetch() so the views exercise the real
// api.ts helpers (status handling, error messages), plus library document factories.
import type { Entry, LibraryDoc } from '../../src/library';

/** Thrown by a route handler to answer with an HTTP error (JSON `{error}` body). */
export class Fail extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

type Handler = (body: unknown) => unknown;

export interface Call {
  method: string;
  path: string;
  body: unknown;
}

export class FakeFetch {
  calls: Call[] = [];
  private routes = new Map<string, Handler>();

  /** Route `method path` to `handler`; its return value is the JSON body. A `Fail` becomes an
   *  HTTP error response, any other throw rejects fetch() like a network failure would. */
  on(method: string, path: string, handler: Handler): this {
    this.routes.set(`${method} ${path}`, handler);
    return this;
  }

  count(method: string, path: string): number {
    return this.calls.filter((c) => c.method === method && c.path === path).length;
  }

  fn = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const path = typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url;
    const method = init?.method ?? 'GET';
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body;
    this.calls.push({ method, path, body });
    const h = this.routes.get(`${method} ${path}`);
    if (!h) throw new Error(`unrouted ${method} ${path}`);
    let out: unknown;
    try {
      out = await h(body);
    } catch (e) {
      if (e instanceof Fail) return json({ error: e.message }, e.status);
      throw e;
    }
    return json(out, 200);
  };
}

function json(v: unknown, status: number): Response {
  // A plain object rather than a real Response: the code under test only reads
  // ok/status/statusText/json(), and a real body stream needs macrotasks that the
  // fake-timer tests never run.
  const ok = status >= 200 && status < 300;
  return { ok, status, statusText: `HTTP ${status}`, json: async () => v } as unknown as Response;
}

export function entry(id: string, path: string, over: Partial<Entry> = {}): Entry {
  return {
    id,
    path,
    name: path.slice(path.lastIndexOf('/') + 1),
    sha256: 'deadbeef'.repeat(8),
    size: 4096,
    composer: null,
    sequencer: null,
    source_url: null,
    hidden: false,
    inject: null,
    trim: null,
    notes: null,
    canon: { status: 'ok', reason: null, canonical_sha256: null, duration_s: 60, checked_at: null },
    ...over,
  };
}

export function libraryDoc(entries: Entry[]): LibraryDoc {
  return { updated_at: null, entries, preview: { fluidsynth: true, ffmpeg: true, gm_sf2: true } };
}

/** Let already-scheduled microtasks settle, e.g. before asserting that a rejection was
 *  deliberately ignored (there is no state change to wait for in that case). */
export async function drain(turns = 20): Promise<void> {
  for (let i = 0; i < turns; i++) await Promise.resolve();
}

/** Resolve once `pred` holds (polling microtasks), or fail after `tries` turns. */
export async function until(pred: () => boolean, tries = 50): Promise<void> {
  for (let i = 0; i < tries; i++) {
    if (pred()) return;
    await Promise.resolve();
  }
  throw new Error('condition not met');
}
