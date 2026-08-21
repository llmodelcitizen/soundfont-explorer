/**
 * "Clear all site data": the same scope a browser's own control covers for this origin —
 * localStorage, sessionStorage, IndexedDB, Cache Storage, cookies, service workers — then reload.
 */
export async function clearAllSiteData(): Promise<void> {
  try {
    localStorage.clear();
  } catch {
    /* blocked */
  }
  try {
    sessionStorage.clear();
  } catch {
    /* blocked */
  }
  try {
    if (indexedDB.databases) {
      const dbs = await indexedDB.databases();
      await Promise.all(dbs.map((d) => (d.name ? new Promise<void>((res) => { const r = indexedDB.deleteDatabase(d.name!); r.onsuccess = r.onerror = r.onblocked = () => res(); }) : Promise.resolve())));
    }
  } catch {
    /* not supported */
  }
  try {
    if (typeof caches !== 'undefined') {
      const keys = await caches.keys();
      await Promise.all(keys.map((k) => caches.delete(k)));
    }
  } catch {
    /* not supported */
  }
  try {
    for (const c of document.cookie.split(';')) {
      const name = c.split('=')[0]?.trim();
      if (name) document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
    }
  } catch {
    /* ignore */
  }
  try {
    const regs = await navigator.serviceWorker?.getRegistrations();
    await Promise.all((regs ?? []).map((r) => r.unregister()));
  } catch {
    /* none */
  }
  // fresh start, no query state carried over
  location.replace(location.pathname);
}
