/**
 * Help copy for every filter category, plus the state and geometry behind the "?" affordance
 * beside each heading. One bubble is open at a time, opened by pointer hover, keyboard focus or
 * tap and dismissed by Escape, a second tap, a click or tap outside it, leaving the trigger, or
 * focus moving away (issue #26).
 */
import type { FacetKey } from '../state/filterIndex';

/**
 * Keyed by FacetKey, so a new facet cannot ship without an explanation. The copy says what the
 * category means and how a value was arrived at — measured, inferred, or merely never flagged.
 */
export const FACET_HELP: Record<FacetKey, string> = {
  engine:
    'The software synthesizer or emulator that rendered the MIDI file. Different engines can interpret the same MIDI events differently, even with similar instruments. Use this filter to compare rendering backends such as FluidSynth, libADLMIDI, Nuked-SC55 and Munt.',
  chip: 'The sound-generating chip, module architecture or bank format the variant represents: OPL2/OPL3 FM chips, the SC-55 PCM ROM, MT-32-style LA synthesis, SoundFont banks. Some engines can render more than one chip family.',
  type: 'The broad synthesis method used to make the sound. FM builds tones from interacting oscillators, sampled systems play recorded waveforms, and LA pairs a short sampled attack with a synthesized sustain. This is a coarse technical grouping, not a quality ranking.',
  completeness:
    'How much of the expected General MIDI instrument and percussion layout the source provides: full GM, melodic only, drums only, partial, or a single instrument. It is derived from bank structure or documented engine behaviour, and says nothing about how good those instruments sound.',
  bank_map:
    'The MIDI bank and program-number layout the variant expects. GM is the basic standard, while GS, XG and GM2 add or organise variation banks differently, and non-GM sources may use game- or device-specific mappings. A song written for a different map may select unexpected instruments or percussion.',
  size: 'The storage-size range of the source bank, sample set or ROM behind the variant. Larger banks often hold more or longer samples, but size alone implies neither better sound nor broader coverage. The bucket is catalog metadata, not the amount downloaded when you press play.',
  lineage:
    'The likely hardware, platform or sound-set family behind the variant, such as Roland, Yamaha XG, Creative, Gravis, console or FM bank. For SoundFonts it may be inferred from filenames and embedded metadata, so generic means no stronger match was found. Lineage describes provenance, not guaranteed authorship or exact hardware equivalence.',
  decade:
    'The best available decade for the sound source or the hardware it emulates, taken from embedded dates, filenames, documented product years or catalog provenance. A decade of unknown means the catalog could not make a reliable assignment.',
  quality:
    'Known compatibility caveats attached to a variant: non GM uses a different instrument map, MT32 expects an MT-32-style layout, miss ins lacks some instruments, and broken drums has a documented percussion problem. A quality of ok only means that none of these warnings was assigned — it is not a listening test, and not a guarantee that every song renders correctly.',
};

/**
 * Which help bubble is showing. Pointer and focus open one directly; the first activation after a
 * focus keeps it open (one tap on a touch screen fires focus *and* click, and must not toggle the
 * bubble shut again), later activations toggle. Escape reports whether it consumed the key, so the
 * app keymap only sees the presses that did not close a bubble.
 */
export class HelpTips {
  private shown: FacetKey | null = null;
  private focused: FacetKey | null = null;
  /** the focus above arrived with a pointer press, so it is not the keyboard holding a bubble open */
  private viaPointer = false;
  /** a press has landed on a trigger and the focus it may bring has not arrived yet */
  private pressing = false;
  private justFocused = false;

  constructor(private readonly onChange: (open: FacetKey | null) => void = () => {}) {}

  get open(): FacetKey | null {
    return this.shown;
  }

  /**
   * The bubble the keyboard is holding open, if any. A trigger left focused by a mouse click does
   * not hold one: the bubble covers the chips beside it, and the pointer moving away is the plainest
   * signal that the reader is done with it.
   */
  private get held(): FacetKey | null {
    return this.viaPointer ? null : this.focused;
  }

  pointerEnter(key: FacetKey): void {
    this.show(key);
  }

  /** the pointer left the trigger: a bubble the keyboard is holding open stays */
  pointerLeave(key: FacetKey): void {
    if (this.shown === key && this.held !== key) this.show(this.held);
  }

  /**
   * A pointer went down on a trigger. Any focus that follows belongs to this press, and a focus the
   * keyboard left behind is spent: without this, the first click on a trigger reached by Tab takes
   * the just-focused branch of activate() below and re-shows instead of toggling.
   */
  pointerDown(): void {
    this.pressing = true;
    this.justFocused = false;
  }

  focus(key: FacetKey): void {
    this.focused = key;
    this.viaPointer = this.pressing;
    this.pressing = false;
    this.justFocused = true;
    this.show(key);
  }

  blur(key: FacetKey): void {
    this.pressing = false;
    if (this.focused !== key) return;
    this.focused = null;
    this.viaPointer = false;
    this.justFocused = false;
    if (this.shown === key) this.show(null);
  }

  /** click or tap */
  activate(key: FacetKey): void {
    if (this.justFocused) {
      this.justFocused = false;
      this.show(key);
      return;
    }
    this.show(this.shown === key ? null : key);
  }

  /** true when a bubble was open and this Escape closed it */
  escape(): boolean {
    if (!this.shown) return false;
    this.close();
    return true;
  }

  /**
   * Dismissed: by Escape, by a tap outside, by the panel scrolling out from under the bubble, or by
   * the triggers being rebuilt. The remembered focus goes with it — Escape never blurs a button, and
   * a trigger still holding focus for a bubble the reader has dismissed would have it painted back
   * the next time the pointer visited another "?" and left again (issue #26).
   */
  close(): void {
    this.focused = null;
    this.viaPointer = false;
    this.pressing = false;
    this.justFocused = false;
    this.show(null);
  }

  private show(next: FacetKey | null): void {
    if (next === this.shown) return;
    this.shown = next;
    this.onChange(next);
  }
}

/** a rectangle as getBoundingClientRect gives it (only the edges the bubble is anchored to) */
export interface Anchor {
  readonly top: number;
  readonly bottom: number;
  readonly left: number;
}

/**
 * Where a bubble goes, in viewport (position: fixed) coordinates: under its trigger when it fits
 * there, above it when it does not, and always clamped inside the viewport — a bubble that hangs
 * off the bottom of a landscape phone loses the end of its copy, which for quality is the whole
 * point of the text (issue #26).
 *
 * The bubble sits flush against the trigger (rounded away from it, so no sub-pixel crack opens
 * between them): a pointer moving off the "?" to read the bubble must never cross ground that
 * belongs to neither, because that ends the hover and takes the bubble down with it (WCAG 1.4.13).
 */
export function tipPosition(
  anchor: Anchor,
  box: { readonly width: number; readonly height: number },
  view: { readonly width: number; readonly height: number },
  pad = 8,
): { left: number; top: number } {
  const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);
  const below = Math.floor(anchor.bottom);
  const above = Math.ceil(anchor.top - box.height);
  const lowest = view.height - pad - box.height; // the lowest top edge that still fits
  return {
    left: Math.round(clamp(anchor.left, pad, Math.max(pad, view.width - pad - box.width))),
    top: Math.floor(clamp(below > lowest && above >= pad ? above : below, pad, Math.max(pad, lowest))),
  };
}
