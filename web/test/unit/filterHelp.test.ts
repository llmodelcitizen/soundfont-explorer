// @vitest-environment jsdom
/**
 * The facet help affordance as the browser drives it: the "?" beside every heading, the events a
 * real tap emits around its click, dismissal by Escape and by an outside tap, and the Tab that has
 * to walk the panel for the other eight triggers to be reachable at all (issue #26).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CatalogDoc, Variant } from '../../src/contracts/catalog';
import { FACET_KEYS, FACET_LABELS, FilterIndex } from '../../src/state/filterIndex';
import { installKeyboard, type KeyActions } from '../../src/input/keyboard';
import { FACET_HELP } from '../../src/ui/facetHelp';
import { FilterBar } from '../../src/ui/filters';

const variant = (id: string, n: number): Variant => ({
  id,
  slug: id,
  label: id,
  engine: `engine${n}`,
  chip: `chip${n}`,
  type: `type${n}`,
  // every facet holds a different value per variant, so all nine categories render
  facets: { completeness: `cov${n}`, bank_map: `map${n}`, size: `size${n}`, lineage: `lin${n}`, decade: `dec${n}`, quality: [`q${n}`] },
  bank: null,
  source: null,
  render: null,
  legal_note: null,
  requires_rom: false,
  aliases: [],
});

const bars: FilterBar[] = [];

function makeBar(): { bar: FilterBar; onChange: ReturnType<typeof vi.fn> } {
  const variants = [variant('a', 1), variant('b', 2)];
  const catalog: CatalogDoc = { schema: 1, engines: [], facets: {}, variants, byId: new Map(variants.map((v) => [v.id, v])) };
  const index = new FilterIndex(['a', 'b'], catalog);
  const onChange = vi.fn();
  const bar = new FilterBar(index, {}, '', { onChange, onSearchEnter: vi.fn(), onFavoritesOnly: vi.fn(), onOpenChange: vi.fn() });
  document.body.appendChild(bar.el);
  bar.toggle(true);
  bars.push(bar);
  return { bar, onChange };
}

const trigger = (key: string) => document.querySelector<HTMLButtonElement>(`[aria-describedby="facet-help-${key}"]`)!;
const tip = (key: string) => document.getElementById(`facet-help-${key}`)!;
const wrapper = (key: string) => trigger(key).closest('.facet-help')!;
const shown = () => FACET_KEYS.filter((key) => !tip(key).classList.contains('hidden'));

const pointer = (el: Element, type: 'pointerenter' | 'pointerleave' | 'pointerdown', pointerType: string) =>
  el.dispatchEvent(new PointerEvent(type, { pointerType, bubbles: type === 'pointerdown' }));

/** what a browser emits around one tap on a touch screen (focus only on the first) */
function tap(key: string, first: boolean): void {
  const btn = trigger(key);
  pointer(wrapper(key), 'pointerenter', 'touch');
  pointer(btn, 'pointerdown', 'touch');
  if (first) btn.focus();
  btn.click();
  pointer(wrapper(key), 'pointerleave', 'touch');
}

const escape = () => document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

beforeEach(() => {
  document.body.innerHTML = '';
});
afterEach(() => {
  // every bar puts listeners on the document: a leftover one would answer for the next test
  for (const bar of bars.splice(0)) bar.dispose();
  document.body.innerHTML = '';
});

describe('facet help affordances', () => {
  it('gives every category a "?" wired to its own description', () => {
    makeBar();
    expect(document.querySelectorAll('.facet').length).toBe(FACET_KEYS.length);
    for (const key of FACET_KEYS) {
      const btn = trigger(key);
      expect(btn, `${key} has no help affordance`).toBeTruthy();
      expect(btn.closest('.facet-name'), `${key}'s "?" is not beside its heading`).toBeTruthy();
      expect(btn.getAttribute('aria-label')).toBe(`about the ${FACET_LABELS[key]} filter`);
      const described = tip(key);
      expect(described.getAttribute('role')).toBe('tooltip');
      expect(described.textContent).toBe(FACET_HELP[key]);
      // a tooltip is not a disclosure: state is a style hook, not an ARIA expanded/collapsed claim
      expect(btn.hasAttribute('aria-expanded')).toBe(false);
      expect(btn.dataset.open).toBe('false');
    }
  });

  it('re-opens on every tap, not just the first', () => {
    makeBar();
    // the synthetic mouse-compat enter that a tap fires had been taken for hover, so from the
    // third tap on the bubble was already open when the click arrived and toggled it shut again
    tap('engine', true);
    expect(shown()).toEqual(['engine']);
    tap('engine', false);
    expect(shown()).toEqual([]);
    tap('engine', false);
    expect(shown()).toEqual(['engine']);
    tap('engine', false);
    expect(shown()).toEqual([]);
    tap('engine', false);
    expect(shown()).toEqual(['engine']);
    expect(trigger('engine').dataset.open).toBe('true');
  });

  it('opens on a tap in a browser that never focuses the button', () => {
    makeBar();
    // WebKit does not focus a <button> on click, so the whole gesture is enter, click, leave —
    // and the leave that ends a tap must not take the bubble down with it
    for (const n of [1, 2, 3]) {
      tap('quality', false);
      expect(shown(), `tap ${n}`).toEqual(n % 2 ? ['quality'] : []);
    }
    expect(document.activeElement).toBe(document.body);
  });

  it('opens on a real hover and closes when the pointer leaves', () => {
    vi.useFakeTimers();
    try {
      makeBar();
      pointer(wrapper('quality'), 'pointerenter', 'mouse');
      expect(shown()).toEqual(['quality']);
      pointer(wrapper('quality'), 'pointerleave', 'mouse');
      vi.runAllTimers();
      expect(shown()).toEqual([]);
    } finally {
      vi.useRealTimers();
    }
  });

  it('survives the pointer clipping the heading on its way into the bubble', () => {
    vi.useFakeTimers();
    try {
      makeBar();
      pointer(wrapper('quality'), 'pointerenter', 'mouse');
      // a pointer aimed at the middle of the copy leaves the trigger sideways first: the bubble
      // has to still be there when it arrives, or it can never be read (WCAG 1.4.13 Hoverable)
      pointer(wrapper('quality'), 'pointerleave', 'mouse');
      vi.advanceTimersByTime(80);
      expect(shown()).toEqual(['quality']);
      pointer(wrapper('quality'), 'pointerenter', 'mouse');
      vi.runAllTimers();
      expect(shown()).toEqual(['quality']);
    } finally {
      vi.useRealTimers();
    }
  });

  it('leaves the filters, the selection and the panel alone', () => {
    const { bar, onChange } = makeBar();
    onChange.mockClear();
    tap('lineage', true);
    expect(shown()).toEqual(['lineage']);
    expect(onChange).not.toHaveBeenCalled();
    expect(bar.sel).toEqual({});
    expect(document.querySelector('.facets')!.classList.contains('hidden')).toBe(false);
    expect(document.querySelectorAll('.opt.on').length).toBe(0);
  });

  it('dismisses a hover-opened bubble with Escape without letting the key reach the app', () => {
    makeBar();
    const keymap = vi.fn();
    window.addEventListener('keydown', keymap);
    pointer(wrapper('decade'), 'pointerenter', 'mouse');
    escape();
    expect(shown()).toEqual([]);
    // the app's Escape closes the whole filter panel: it must not see the one that closed a bubble
    expect(keymap).not.toHaveBeenCalled();
    escape();
    expect(keymap).toHaveBeenCalledTimes(1);
    window.removeEventListener('keydown', keymap);
  });

  it('dismisses on a tap outside it', () => {
    const { bar } = makeBar();
    tap('size', true);
    pointer(trigger('size'), 'pointerdown', 'touch');
    expect(shown()).toEqual(['size']);
    // a tap on the bubble is aimed at the chips it covers: down it goes — on the click, so that
    // the tap is spent on the bubble rather than toggling a chip the reader could not see
    pointer(tip('size'), 'pointerdown', 'touch');
    expect(shown()).toEqual(['size']);
    tip('size').dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(shown()).toEqual([]);
    tap('size', false);
    expect(shown()).toEqual(['size']);
    pointer(bar.el.querySelector('.filterrow')!, 'pointerdown', 'touch');
    expect(shown()).toEqual([]);
  });

  it('keeps the bubble up while the mouse selects its copy', () => {
    makeBar();
    pointer(wrapper('decade'), 'pointerenter', 'mouse');
    pointer(tip('decade'), 'pointerdown', 'mouse');
    tip('decade').dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(shown()).toEqual(['decade']);
  });

  it('keeps Space and Enter off the global keymap but lets Tab through', () => {
    makeBar();
    const keymap = vi.fn();
    window.addEventListener('keydown', keymap);
    for (const key of [' ', 'Enter']) trigger('chip').dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
    expect(keymap).not.toHaveBeenCalled();
    trigger('chip').dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }));
    expect(keymap).toHaveBeenCalledTimes(1);
    window.removeEventListener('keydown', keymap);
  });

  it('lets go of its document listeners once the bar is thrown away', () => {
    // the whole bar is rebuilt for every song, and its Escape and outside-tap listeners live on
    // the document rather than on the DOM that goes with it
    const { bar } = makeBar();
    const keymap = vi.fn();
    window.addEventListener('keydown', keymap);
    pointer(wrapper('engine'), 'pointerenter', 'mouse');
    bar.dispose();
    escape();
    expect(keymap).toHaveBeenCalledTimes(1);
    window.removeEventListener('keydown', keymap);
  });

  it('forgets a trigger that the panel closed under', () => {
    vi.useFakeTimers();
    try {
      const { bar } = makeBar();
      // the panel hides with display:none, and the blur that normally clears the focused trigger
      // is the browser's doing — where it does not come, a stale trigger must not pop its bubble
      // back open the next time the pointer visits some other "?" and leaves again
      trigger('type').focus();
      expect(shown()).toEqual(['type']);
      bar.toggle(false);
      bar.toggle(true);
      expect(shown()).toEqual([]);
      pointer(wrapper('bank_map'), 'pointerenter', 'mouse');
      pointer(wrapper('bank_map'), 'pointerleave', 'mouse');
      vi.runAllTimers();
      expect(shown()).toEqual([]);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('an open bubble follows the viewport', () => {
  const rect = (el: Element, x: number, y: number, width: number, height: number) => {
    el.getBoundingClientRect = () => ({ x, y, left: x, top: y, width, height, right: x + width, bottom: y + height, toJSON: () => ({}) }) as DOMRect;
  };
  const viewport = (width: number, height: number) => {
    Object.defineProperty(window, 'innerWidth', { value: width, configurable: true });
    Object.defineProperty(window, 'innerHeight', { value: height, configurable: true });
  };

  it('re-anchors when the window resizes, instead of being stranded off-screen', () => {
    const { bar } = makeBar();
    viewport(1280, 800);
    rect(bar.el.querySelector('.facets')!, 0, 40, 1280, 300);
    rect(trigger('engine'), 1100, 60, 16, 16);
    rect(tip('engine'), 0, 0, 320, 200);
    pointer(wrapper('engine'), 'pointerenter', 'mouse');
    expect(tip('engine').style.left).toBe('952px'); // 1280 - 8 - 320
    viewport(420, 800);
    window.dispatchEvent(new Event('resize'));
    // a phone rotation used to leave the bubble at its old coordinates, entirely off the screen
    expect(tip('engine').style.left).toBe('92px'); // 420 - 8 - 320
    expect(shown()).toEqual(['engine']);
  });
});

describe('keyboard reach', () => {
  function keys(): KeyActions {
    return {
      step: vi.fn(),
      stepEnd: vi.fn(),
      page: vi.fn(),
      home: vi.fn(),
      end: vi.fn(),
      toggle: vi.fn(),
      stop: vi.fn(),
      skip: vi.fn(),
      loop: vi.fn(),
      mute: vi.fn(),
      favorite: vi.fn(),
      focusSearch: vi.fn(),
      escape: vi.fn(),
      song: vi.fn(),
      pinA: vi.fn(),
      ab: vi.fn(),
      filters: vi.fn(),
      theme: vi.fn(),
      debug: vi.fn(),
      keymap: vi.fn(),
      settings: vi.fn(),
    };
  }

  it('lets Tab walk the filter panel rather than firing A/B', () => {
    makeBar();
    const a = keys();
    const uninstall = installKeyboard(window, a);
    for (const key of FACET_KEYS) {
      const e = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
      trigger(key).dispatchEvent(e);
      expect(e.defaultPrevented, `Tab is swallowed on the ${key} help trigger`).toBe(false);
    }
    const onOpt = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    document.querySelector('.opt')!.dispatchEvent(onOpt);
    expect(onOpt.defaultPrevented).toBe(false);
    expect(a.ab).not.toHaveBeenCalled();
    // outside the panel Tab is still the A/B key
    const outside = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    document.body.dispatchEvent(outside);
    expect(outside.defaultPrevented).toBe(true);
    expect(a.ab).toHaveBeenCalledTimes(1);
    uninstall();
  });
});
