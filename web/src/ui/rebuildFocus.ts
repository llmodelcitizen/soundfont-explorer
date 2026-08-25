/**
 * Keyboard focus across a rebuild of the whole player.
 *
 * Distinct from ./focus, which traps focus inside an open dialog: this remembers where
 * focus WAS so a rebuilt control can take it back (hence restoreFocusMemento).
 *
 * `App.buildUi()` empties the root and builds every control again, so `document.activeElement`
 * falls back to `<body>`. That is harmless when the user asked for the switch, but automatic
 * next-track stepping rebuilds by itself, at the end of every track, while the user may be
 * typing: from `<body>` the keymap reads plain letters as global shortcuts, so a soundfont name
 * typed into the filter box would toggle LOOP, the theme, favorites… instead of filtering.
 *
 * The rebuild puts the same controls back in the same shape, so remembering *where* focus was —
 * the child indices from the root down — is enough to find the replacement of whatever held it.
 */

export interface FocusMemento {
  /** child index at each level, from the root down to the element that had focus */
  path: number[];
  /** caret of a text field: the rebuilt search box carries the same query, so it still means something */
  selection: { start: number; end: number } | null;
}

/** the caret of a text field, or null for anything without one (reading it can throw) */
function caretOf(el: Element): { start: number; end: number } | null {
  try {
    const field = el as Partial<HTMLInputElement>;
    if (typeof field.selectionStart === 'number' && typeof field.selectionEnd === 'number') {
      return { start: field.selectionStart, end: field.selectionEnd };
    }
  } catch {
    /* an <input> whose type has no selection throws on access */
  }
  return null;
}

function indexIn(parent: Element, child: Element): number {
  const kids = parent.children;
  for (let i = 0; i < kids.length; i += 1) {
    if (kids[i] === child) return i;
  }
  return -1;
}

/** Where the keyboard is, if it is anywhere inside `root`; null when a rebuild has nothing to keep. */
export function captureFocus(root: Element, active: Element | null | undefined): FocusMemento | null {
  if (!active || active === root) return null;
  const path: number[] = [];
  let node: Element | null = active;
  while (node && node !== root) {
    const parent: Element | null = node.parentElement;
    if (!parent) return null; // detached, or focus was outside this root
    const i = indexIn(parent, node);
    if (i < 0) return null;
    path.unshift(i);
    node = parent;
  }
  if (node !== root) return null;
  return { path, selection: caretOf(active) };
}

/** Give the keyboard back to the element now standing where the remembered one stood. */
export function restoreFocusMemento(root: Element, memento: FocusMemento | null): boolean {
  if (!memento) return false;
  let node: Element = root;
  for (const i of memento.path) {
    const next: Element | undefined = node.children[i];
    if (!next) return false; // the rebuilt tree is a different shape: leave focus where it fell
    node = next;
  }
  const el = node as Element & Partial<HTMLElement> & Partial<HTMLInputElement>;
  if (typeof el.focus !== 'function') return false;
  el.focus();
  const caret = memento.selection;
  if (caret && typeof el.setSelectionRange === 'function') {
    try {
      el.setSelectionRange(caret.start, caret.end);
    } catch {
      /* a field that no longer takes a caret keeps the focus anyway */
    }
  }
  return true;
}
