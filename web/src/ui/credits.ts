/** #/credits — generated from songs.json + catalog engines; licenses verbatim; DMCA contact (phase 1). */
import { engineVersion, type CatalogDoc, type Source } from '../contracts/catalog';
import type { SongsDoc } from '../contracts/songs';
import { h } from './dom';

/** Takedown contact for the About page: VITE_CONTACT_EMAIL from web/.env.local (private overlay). */
export const CONTACT: string = (import.meta.env.VITE_CONTACT_EMAIL as string | undefined) ?? '';

/** One paragraph per source collection found in the catalog (attribution is per file, never assumed). */
function soundfontSources(catalog: CatalogDoc): HTMLElement[] {
  const byColl = new Map<string, NonNullable<Source['collection']>>();
  let unknown = 0;
  for (const v of catalog.variants) {
    if (v.engine !== 'fluidsynth') continue;
    const c = v.source?.collection;
    if (!c) unknown++;
    else if (!byColl.has(c.id)) byColl.set(c.id, c);
  }
  const out: HTMLElement[] = [];
  for (const c of byColl.values()) {
    out.push(
      h(
        'p',
        null,
        'Most SoundFonts showcased here come from ',
        h('a', { href: c.url, target: '_blank', rel: 'noopener' }, c.url),
        '. Huge thanks to the Archive and to the collector(s) who assembled it! ',
        c.torrent ? ['(', h('a', { href: c.torrent, target: '_blank', rel: 'noopener' }, 'torrent'), ')'] : '',
        ' Those variants say so in the now-playing panel ("from"), next to the authorship and copyright notices embedded in the file.',
      ),
    );
  }
  if (unknown > 0) out.push(h('p', null, `${unknown} SoundFont${unknown === 1 ? '' : 's'} ${unknown === 1 ? 'has' : 'have'} no recorded source collection; only the notices inside the file are shown.`));
  if (!out.length) out.push(h('p', null, 'SoundFont provenance is shown per variant in the now-playing panel.'));
  return out;
}

export function renderCredits(songs: SongsDoc, catalog: CatalogDoc): HTMLElement {
  const engineRows = catalog.engines.map((e) => {
    const ver = engineVersion(e);
    return h('li', null, h('strong', null, e.label), ver && ` ${ver}`, e.url ? [' · ', h('a', { href: e.url, target: '_blank', rel: 'noopener' }, e.url)] : '', e.license ? h('div', { class: 'muted' }, e.license) : '');
  });
  return h(
    'article',
    { class: 'credits' },
    h('p', null, h('a', { href: '#', class: 'btn link' }, '← back to the player')),
    h('h1', null, 'About'),
    h('p', { class: 'tagline' }, 'The voices of your PC through the decades <3'),
    h(
      'p',
      null,
      'Every variant you hear is the same MIDI file rendered offline through a different SoundFont or synthesizer emulation, ',
      'measured with EBU R128 and brought to −16 LUFS integrated / −1.5 dBTP with a pure linear gain (no limiter, no dynamics). ',
      'Reverb and chorus are switched off on every engine so the comparison is fair. Audio is 48 kHz Opus.',
    ),
    h('h2', null, 'Engines'),
    h('ul', { class: 'engines' }, engineRows),
    h('h2', null, 'SoundFonts and banks'),
    ...soundfontSources(catalog),
    h(
      'p',
      null,
      'FM banks are the ones embedded in libADLMIDI / libOPNMIDI, several of which were extracted from period games. Variants derived from Roland ROMs or Roland-copyright sample sets are labelled.',
    ),
    h('h2', null, 'Typography'),
    h(
      'p',
      null,
      'The Amiga theme uses the Topaz Kickstart 1.x typeface remade by dMG of TrueSchool/Divine Stylers, from ',
      h('a', { href: 'https://github.com/rewtnull/amigafonts', target: '_blank', rel: 'noopener' }, 'rewtnull/amigafonts'),
      ' under GPL with the font exception.',
    ),
    h(
      'p',
      null,
      'The Windows 95 theme uses lou’s CC BY-SA 3.0 FontStruction recreations of ',
      h('a', { href: 'https://fontstruct.com/fontstructions/show/1384746', target: '_blank', rel: 'noopener' }, 'MS Sans Serif'),
      ' and ',
      h('a', { href: 'https://fontstruct.com/fontstructions/show/1384862', target: '_blank', rel: 'noopener' }, 'MS Sans Serif Bold'),
      ', distributed by ',
      h('a', { href: 'https://github.com/jdan/98.css', target: '_blank', rel: 'noopener' }, '98.css'),
      '. Monospace text uses the public-domain/CC0 ',
      h('a', { href: 'https://github.com/kika/fixedsys', target: '_blank', rel: 'noopener' }, 'Fixedsys Excelsior'),
      ' recreation.',
    ),
    h('h2', null, 'Takedown / DMCA'),
    h('p', null, `If you hold rights to something here and want it removed, ${CONTACT ? `email ${CONTACT}` : 'contact the site operator'} with the variant id (shown in the now-playing panel) and the work concerned; it will be taken down promptly.`),
    h('h2', null, 'Privacy'),
    h('p', null, 'No analytics, no tracking, no selling your information.'),
    h(
      'p',
      { class: 'muted' },
      'Your settings, favorites and listened marks live only in your browser (localStorage) — nothing is sent anywhere; the settings dialog can wipe them. ',
      'There are no cookies and no accounts today; if accounts arrive, this section will say exactly what is stored and why.',
    ),
    h('p', { class: 'muted' }, `songs.json generated ${songs.generated_at}`),
  );
}
