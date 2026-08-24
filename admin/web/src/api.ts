// Thin fetch helpers. Cookies ride along same-origin; a 401 means the session expired —
// bounce through the login flow.

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method };
  if (body !== undefined) {
    init.headers = { 'content-type': 'application/json' };
    init.body = JSON.stringify(body);
  }
  const r = await fetch(path, init);
  if (r.status === 401) {
    location.href = '/auth/login';
    throw new ApiError(401, 'session expired');
  }
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const j = await r.json();
      msg = j.error ?? j.detail ?? msg;
    } catch { /* not json */ }
    throw new ApiError(r.status, msg);
  }
  return r.json() as Promise<T>;
}

export const get = <T>(path: string) => req<T>('GET', path);
export const post = <T>(path: string, body?: unknown) => req<T>('POST', path, body);
export const patch = <T>(path: string, body: unknown) => req<T>('PATCH', path, body);
export const del = <T>(path: string) => req<T>('DELETE', path);

export interface BootstrapStatus {
  phase: string;
  pct: number;
  msg: string;
}

export interface Me {
  email: string;
  hostname: string;
}
