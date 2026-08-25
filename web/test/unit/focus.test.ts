import { describe, expect, it } from 'vitest';
import { captureFocus, restoreFocus } from '../../src/ui/focus';

/**
 * The unit suite runs without a DOM, so these are the parts of Element the module touches:
 * a child list, a parent link, focus() and an optional caret. `tree()` builds a shape close to
 * the player's own root — header > filters > search box — and `tree()` twice is a rebuild.
 */
class FakeEl {
  children: FakeEl[] = [];
  parentElement: FakeEl | null = null;
  focused = false;
  selectionStart: number | null = null;
  selectionEnd: number | null = null;

  constructor(readonly name: string, kids: FakeEl[] = []) {
    for (const kid of kids) {
      kid.parentElement = this;
      this.children.push(kid);
    }
  }

  focus(): void {
    this.focused = true;
  }

  setSelectionRange(start: number, end: number): void {
    this.selectionStart = start;
    this.selectionEnd = end;
  }

  find(name: string): FakeEl {
    const hit = this.search(name);
    if (!hit) throw new Error(`no ${name} in this tree`);
    return hit;
  }

  private search(name: string): FakeEl | null {
    if (this.name === name) return this;
    for (const kid of this.children) {
      const hit = kid.search(name);
      if (hit) return hit;
    }
    return null;
  }
}

const el = (name: string, ...kids: FakeEl[]) => new FakeEl(name, kids);
const tree = () => el('root', el('header', el('title'), el('picker')), el('main', el('filters', el('search'), el('facets')), el('list')), el('transport'));
const asElement = (node: FakeEl) => node as unknown as Element;

describe('keeping the keyboard across a rebuild', () => {
  it('gives focus to the control standing where the remembered one stood', () => {
    const before = tree();
    const search = before.find('search');
    const memento = captureFocus(asElement(before), asElement(search));

    const after = tree(); // buildUi() cleared the root and made every control again
    expect(restoreFocus(asElement(after), memento)).toBe(true);
    expect(after.find('search').focused).toBe(true);
    expect(after.find('list').focused).toBe(false);
  });

  it('restores the caret, so a half-typed query can be finished', () => {
    const before = tree();
    const search = before.find('search');
    search.selectionStart = 3;
    search.selectionEnd = 3;
    const memento = captureFocus(asElement(before), asElement(search));
    expect(memento?.selection).toEqual({ start: 3, end: 3 });

    const after = tree();
    restoreFocus(asElement(after), memento);
    expect(after.find('search').selectionStart).toBe(3);
  });

  it('has nothing to keep when focus is on the root, outside it, or nowhere', () => {
    const root = tree();
    expect(captureFocus(asElement(root), null)).toBeNull();
    expect(captureFocus(asElement(root), asElement(root))).toBeNull();
    expect(captureFocus(asElement(root), asElement(el('body', el('stray')).find('stray')))).toBeNull();
    expect(restoreFocus(asElement(root), null)).toBe(false);
  });

  it('leaves focus alone when the rebuilt tree is a different shape', () => {
    const before = tree();
    const memento = captureFocus(asElement(before), asElement(before.find('facets')));
    const after = el('root', el('header'), el('main', el('filters', el('search'))));
    expect(restoreFocus(asElement(after), memento)).toBe(false);
    expect(after.find('search').focused).toBe(false);
  });
});
