import { describe, expect, it, vi } from 'vitest';
import { FACET_KEYS, FACET_LABELS } from '../../src/state/filterIndex';
import { FACET_HELP, HelpTips, tipPosition } from '../../src/ui/facetHelp';

/** sentences that actually end: a full stop followed by a space or the end of the copy */
const sentences = (text: string): number => (text.match(/[.!?](\s|$)/g) ?? []).length;

describe('facet help copy', () => {
  it('explains every filter category', () => {
    for (const key of FACET_KEYS) {
      const text = FACET_HELP[key];
      expect(text.trim(), `${FACET_LABELS[key]} has no help copy`).not.toBe('');
      // a concise multi-sentence explanation, not a restated heading
      expect(sentences(text), `${FACET_LABELS[key]} help is a single sentence`).toBeGreaterThan(1);
      expect(text.length).toBeGreaterThan(80);
    }
  });

  it('says that quality ok is only the absence of a warning', () => {
    expect(FACET_HELP.quality).toMatch(/ok only means that none of these warnings was assigned/);
    expect(FACET_HELP.quality).toMatch(/not a listening test/);
  });

  it('names the caveats quality can carry', () => {
    for (const caveat of ['non GM', 'MT32', 'miss ins', 'broken drums']) expect(FACET_HELP.quality).toContain(caveat);
  });
});

describe('help bubbles', () => {
  it('opens on hover and closes when the pointer leaves', () => {
    const seen: (string | null)[] = [];
    const tips = new HelpTips((open) => seen.push(open));
    tips.pointerEnter('engine');
    expect(tips.open).toBe('engine');
    tips.pointerLeave('engine');
    expect(tips.open).toBe(null);
    expect(seen).toEqual(['engine', null]);
  });

  it('shows one bubble at a time', () => {
    const tips = new HelpTips();
    tips.pointerEnter('engine');
    tips.pointerEnter('quality');
    expect(tips.open).toBe('quality');
  });

  it('keeps the bubble through the click that follows a tap focus, and toggles after that', () => {
    const tips = new HelpTips();
    // one tap on a touch screen: focus, then click. The enter/leave a tap also fires is hover
    // only on a hovering pointer, and filters.ts keeps the touch-derived pair out of here — the
    // browser's whole tap sequence is replayed against the DOM in filterHelp.test.ts.
    tips.focus('decade');
    tips.activate('decade');
    expect(tips.open).toBe('decade');
    // a second tap on the already-focused trigger closes it, a third opens it again
    tips.activate('decade');
    expect(tips.open).toBe(null);
    tips.activate('decade');
    expect(tips.open).toBe('decade');
    tips.activate('decade');
    expect(tips.open).toBe(null);
  });

  it('closes when focus moves away, even after a tap opened it', () => {
    const tips = new HelpTips();
    tips.focus('size');
    tips.activate('size');
    tips.blur('size');
    expect(tips.open).toBe(null);
  });

  it('leaves a keyboard-held bubble alone when the pointer wanders off', () => {
    const tips = new HelpTips();
    tips.focus('chip');
    tips.pointerEnter('chip');
    tips.pointerLeave('chip');
    expect(tips.open).toBe('chip');
    // a pointer visiting a different trigger falls back to the focused one
    tips.pointerEnter('lineage');
    tips.pointerLeave('lineage');
    expect(tips.open).toBe('chip');
  });

  it('dismisses with Escape and only claims the key when a bubble was open', () => {
    const tips = new HelpTips();
    expect(tips.escape()).toBe(false);
    tips.focus('bank_map');
    expect(tips.escape()).toBe(true);
    expect(tips.open).toBe(null);
    // the trigger still has focus: Enter re-opens the same bubble
    tips.activate('bank_map');
    expect(tips.open).toBe('bank_map');
  });

  it('does not bring a dismissed bubble back the next time the pointer visits another trigger', () => {
    const tips = new HelpTips();
    // Escape never blurs a button, so the trigger keeps focus after dismissing its bubble. If that
    // focus is still remembered, the next hover elsewhere falls back to it on the way out and paints
    // a bubble the reader dismissed, with the pointer nowhere near it (issue #26).
    tips.focus('engine');
    tips.escape();
    tips.pointerEnter('chip');
    tips.pointerLeave('chip');
    expect(tips.open).toBe(null);
  });

  it('closes when the panel scrolls or is rebuilt, remembered focus and all', () => {
    const tips = new HelpTips();
    tips.pointerEnter('type');
    tips.close();
    expect(tips.open).toBe(null);
    tips.focus('type');
    tips.close();
    expect(tips.open).toBe(null);
    // close drops the remembered focus too, so a stale trigger cannot hold a bubble open
    tips.pointerEnter('type');
    tips.pointerLeave('type');
    expect(tips.open).toBe(null);
  });

  it('toggles on the first click on a trigger the keyboard had already focused', () => {
    const tips = new HelpTips();
    // Tab to the trigger, then click it: the button already has focus, so no focus event follows the
    // press, and the click has to close the bubble rather than re-show it
    tips.focus('decade');
    tips.pointerDown();
    tips.activate('decade');
    expect(tips.open).toBe(null);
  });

  it('lets the pointer take away a bubble its own click left focused', () => {
    const tips = new HelpTips();
    // a mouse click focuses the trigger; that focus must not pin the bubble over the chips it
    // covers the way a keyboard focus does
    tips.pointerDown();
    tips.focus('size');
    tips.activate('size');
    expect(tips.open).toBe('size');
    tips.pointerLeave('size');
    expect(tips.open).toBe(null);
  });

  it('reports only real changes', () => {
    const onChange = vi.fn();
    const tips = new HelpTips(onChange);
    tips.pointerEnter('completeness');
    tips.pointerEnter('completeness');
    tips.focus('completeness');
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith('completeness');
  });
});

describe('bubble placement', () => {
  const view = { width: 1280, height: 800 };
  const box = { width: 320, height: 120 };

  it('hangs under the trigger, flush against it so the pointer can reach it', () => {
    expect(tipPosition({ top: 100, bottom: 116, left: 200 }, box, view)).toEqual({ left: 200, top: 116 });
    // a fractional anchor rounds towards the trigger: a sub-pixel crack between the two ends the
    // hover as the pointer crosses it
    expect(tipPosition({ top: 100.4, bottom: 116.6, left: 200 }, box, view).top).toBe(116);
    expect(tipPosition({ top: 700.4, bottom: 716.6, left: 200 }, box, view).top).toBe(581); // 700.4 - 120
  });

  it('flips above the trigger when the bubble would not fit below it', () => {
    expect(tipPosition({ top: 700, bottom: 716, left: 200 }, box, view)).toEqual({ left: 200, top: 580 });
  });

  it('keeps the bubble inside a narrow viewport', () => {
    // the right-most category on a phone: anchored at the trigger the bubble would run off-screen
    expect(tipPosition({ top: 40, bottom: 56, left: 360 }, box, { width: 390, height: 844 }).left).toBe(62);
    expect(tipPosition({ top: 40, bottom: 56, left: 2 }, box, { width: 390, height: 844 }).left).toBe(8);
  });

  it('clamps the bottom when neither placement fits, rather than cutting the copy off', () => {
    // a landscape phone: the facet panel is near the top, so there is no room above either
    const at = tipPosition({ top: 120, bottom: 136, left: 40 }, { width: 320, height: 260 }, { width: 844, height: 390 });
    expect(at.top).toBe(122); // 390 - 8 - 260
    expect(at.top + 260).toBeLessThanOrEqual(390 - 8);
  });

  it('pins a bubble taller than the viewport to the top (the stylesheet scrolls the rest)', () => {
    expect(tipPosition({ top: 120, bottom: 136, left: 40 }, { width: 320, height: 600 }, { width: 844, height: 390 }).top).toBe(8);
  });
});
