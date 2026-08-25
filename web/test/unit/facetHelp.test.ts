import { describe, expect, it, vi } from 'vitest';
import { FACET_KEYS, FACET_LABELS } from '../../src/state/filterIndex';
import { FACET_HELP, HelpTips } from '../../src/ui/facetHelp';

describe('facet help copy', () => {
  it('explains every filter category and nothing else', () => {
    expect(Object.keys(FACET_HELP).sort()).toEqual([...FACET_KEYS].sort());
    for (const key of FACET_KEYS) {
      const text = FACET_HELP[key];
      expect(text.trim(), `${FACET_LABELS[key]} has no help copy`).not.toBe('');
      // a concise multi-sentence explanation, not a restated heading
      expect(text.split('. ').length, `${FACET_LABELS[key]} help is a single sentence`).toBeGreaterThan(1);
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
    // one tap on a touch screen: focus, then click
    tips.focus('decade');
    tips.activate('decade');
    expect(tips.open).toBe('decade');
    // a second tap on the already-focused trigger closes it
    tips.activate('decade');
    expect(tips.open).toBe(null);
    tips.activate('decade');
    expect(tips.open).toBe('decade');
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

  it('closes when the panel scrolls or is rebuilt', () => {
    const tips = new HelpTips();
    tips.pointerEnter('type');
    tips.close();
    expect(tips.open).toBe(null);
    tips.focus('type');
    tips.reset();
    expect(tips.open).toBe(null);
    // reset drops the remembered focus too, so a stale trigger cannot hold a bubble open
    tips.pointerEnter('type');
    tips.pointerLeave('type');
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
