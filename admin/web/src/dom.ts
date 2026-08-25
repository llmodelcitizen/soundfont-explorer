// Tiny DOM helpers shared by the admin views (no framework, no dependencies).
// The public site has a richer sibling in web/src/ui/dom.ts (h(): boolean/event/dataset/style
// attributes); the two SPAs are separate npm projects with include: ["src"], so this is a
// deliberate copy of an idea, not a shared module.

/** createElement + attributes + children. `class` goes to className, everything else to
 *  setAttribute; children are appended in order. */
export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K, attrs: Record<string, string> = {}, ...children: (Node | string)[]
): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') e.className = v;
    else e.setAttribute(k, v);
  }
  e.append(...children);
  return e;
}

/** The per-view status line each view keeps in its toolbar and hands to note(). */
export function statusLine(): HTMLSpanElement {
  return el('span', { class: 'statusline' });
}

/** Show `msg` on a status line; `isError` flags it with the `error` class (red in style.css). */
export function note(status: HTMLElement, msg: string, isError = false): void {
  status.textContent = msg;
  status.classList.toggle('error', isError);
}
