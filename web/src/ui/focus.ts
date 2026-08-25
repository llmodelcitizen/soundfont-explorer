/**
 * Focus management for the overlay dialogs. A box that claims `aria-modal="true"` has to mean it:
 * Tab must not walk out of it, and closing must hand focus to something real.
 *
 * Both halves matter because of one window binding: the keymap gives Tab to A/B
 * (`input/keyboard.ts`), so a Tab that reaches the window is preventDefault-ed away. Focus that
 * escapes onto `<body>` therefore cannot walk back in, and every plain letter key is a global
 * shortcut again — the theme flips, another song loads, all behind an open dialog (#30, #33).
 */

/** what Tab can reach, in document order — the same shape every dialog here is built from */
const FOCUSABLE = 'a[href], button, input, select, textarea, [tabindex]';

/** the controls inside `root` that Tab can reach, in tab order */
export function focusables(root: ParentNode): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => {
    if ((el as HTMLInputElement).disabled) return false; // a disabled control is skipped by Tab
    if (el.getAttribute('tabindex') === '-1') return false;
    return !el.hidden;
  });
}

/**
 * Keep Tab inside `box`, wrapping at both ends. Returns true when the event was a Tab, so the
 * caller knows it is dealt with. Propagation stops either way: Tab = A/B must never fire behind
 * an open dialog.
 */
export function trapTab(box: HTMLElement, e: KeyboardEvent): boolean {
  if (e.key !== 'Tab') return false;
  e.stopPropagation();
  const items = focusables(box);
  const first = items[0];
  const last = items[items.length - 1];
  if (!first || !last) {
    e.preventDefault(); // nothing to move to: staying put beats landing on <body>
    return true;
  }
  const active = box.ownerDocument.activeElement;
  const edge = e.shiftKey ? first : last;
  if (active === edge || !box.contains(active)) {
    e.preventDefault();
    (e.shiftKey ? last : first).focus({ preventScroll: true });
  }
  return true;
}

/**
 * Hand focus back to whatever opened the dialog, now that the control it was on is hidden.
 * `<body>` is not an answer — that is the stranded state above — so the fallback runs instead.
 */
export function restoreFocus(opener: HTMLElement | null, fallback?: () => void): void {
  const doc = opener?.ownerDocument;
  if (opener && doc && opener.isConnected && opener !== doc.body && typeof opener.focus === 'function') opener.focus({ preventScroll: true });
  else fallback?.();
}

/** the control focus is on right now: what a dialog gives it back to when it closes */
export function activeElement(): HTMLElement | null {
  return typeof document === 'undefined' ? null : (document.activeElement as HTMLElement | null);
}
