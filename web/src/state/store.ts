/** Minimal observable store: set() merges, subscribers get (state, changedKeys). */
export type Listener<S> = (s: S, changed: Set<keyof S>) => void;

export class Store<S extends object> {
  private subs = new Set<Listener<S>>();
  constructor(public state: S) {}

  set(patch: Partial<S>): void {
    const changed = new Set<keyof S>();
    for (const k of Object.keys(patch) as (keyof S)[]) {
      if (this.state[k] !== patch[k]) {
        changed.add(k);
      }
    }
    if (!changed.size) return;
    this.state = { ...this.state, ...patch };
    for (const fn of this.subs) fn(this.state, changed);
  }

  subscribe(fn: Listener<S>): () => void {
    this.subs.add(fn);
    return () => this.subs.delete(fn);
  }
}
