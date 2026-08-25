/**
 * Full screen (Shift + F, or the button in Settings): the standard Fullscreen API with the
 * WebKit-prefixed spelling, and a graceful no-op where neither exists — iPhone Safari has no
 * element fullscreen at all, so the control disables itself and says what to do instead (#34).
 * The shapes below are structural on purpose: the app passes `document`, the tests pass a stub.
 */
export interface FullscreenTarget {
  requestFullscreen?: () => Promise<void> | void;
  webkitRequestFullscreen?: () => Promise<void> | void;
}

export interface FullscreenDocument {
  fullscreenEnabled?: boolean;
  webkitFullscreenEnabled?: boolean;
  fullscreenElement?: Element | null;
  webkitFullscreenElement?: Element | null;
  exitFullscreen?: () => Promise<void> | void;
  webkitExitFullscreen?: () => Promise<void> | void;
  documentElement: FullscreenTarget;
}

/** what to tell someone whose browser cannot do it (iPhone Safari, or an iframe without allowfullscreen) */
export const FULLSCREEN_UNSUPPORTED = 'This browser has no full-screen mode for web pages. On iPhone, add the site to your Home Screen to get a window without browser chrome.';

export function fullscreenSupported(doc: FullscreenDocument | undefined = typeof document === 'undefined' ? undefined : (document as unknown as FullscreenDocument)): boolean {
  const el = doc?.documentElement;
  if (!el || (typeof el.requestFullscreen !== 'function' && typeof el.webkitRequestFullscreen !== 'function')) return false;
  // present but forbidden (iframe without allowfullscreen): the document flag is the authority
  const enabled = doc.fullscreenEnabled ?? doc.webkitFullscreenEnabled;
  return enabled !== false;
}

export function isFullscreen(doc: FullscreenDocument | undefined = typeof document === 'undefined' ? undefined : (document as unknown as FullscreenDocument)): boolean {
  return !!(doc?.fullscreenElement ?? doc?.webkitFullscreenElement);
}

/**
 * Enter/leave full screen. Resolves true when the browser accepted the request, false when it
 * cannot (unsupported, or the request was rejected) — the caller shows FULLSCREEN_UNSUPPORTED.
 */
export async function toggleFullscreen(doc: FullscreenDocument | undefined = typeof document === 'undefined' ? undefined : (document as unknown as FullscreenDocument), force?: boolean): Promise<boolean> {
  if (!doc) return false;
  const want = force ?? !isFullscreen(doc);
  const el = doc.documentElement;
  const enter = fullscreenSupported(doc) ? (el.requestFullscreen ?? el.webkitRequestFullscreen) : undefined;
  const exit = doc.exitFullscreen ?? doc.webkitExitFullscreen;
  const call = want ? enter?.bind(el) : exit?.bind(doc);
  if (!call) return false;
  try {
    await call();
    return true;
  } catch {
    return false; // a rejected request (no user gesture, or a permissions policy) is not a crash
  }
}
