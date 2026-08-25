/**
 * Help copy for every filter category, plus the state behind the "?" affordance beside each
 * heading. One bubble is open at a time, opened by pointer hover, keyboard focus or tap and
 * dismissed by Escape, a second tap, leaving the trigger, or focus moving away (issue #26).
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
  private justFocused = false;

  constructor(private readonly onChange: (open: FacetKey | null) => void = () => {}) {}

  get open(): FacetKey | null {
    return this.shown;
  }

  pointerEnter(key: FacetKey): void {
    this.show(key);
  }

  /** the pointer left the trigger: a bubble the keyboard is holding open stays */
  pointerLeave(key: FacetKey): void {
    if (this.shown === key && this.focused !== key) this.show(this.focused);
  }

  focus(key: FacetKey): void {
    this.focused = key;
    this.justFocused = true;
    this.show(key);
  }

  blur(key: FacetKey): void {
    if (this.focused !== key) return;
    this.focused = null;
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

  /** the panel scrolled out from under the bubble, or was rebuilt */
  close(): void {
    this.justFocused = false;
    this.show(null);
  }

  /** the triggers themselves are gone (the facet panel re-rendered) */
  reset(): void {
    this.focused = null;
    this.close();
  }

  private show(next: FacetKey | null): void {
    if (next === this.shown) return;
    this.shown = next;
    this.onChange(next);
  }
}
