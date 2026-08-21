/** #/credits — generated from songs.json + catalog engines; licenses verbatim; DMCA contact (phase 1). */
import type { CatalogDoc } from '../contracts/catalog';
import type { SongsDoc } from '../contracts/songs';
import { h } from './dom';

export const CONTACT = 'soundfonts@ericq.com';

export function renderCredits(songs: SongsDoc, catalog: CatalogDoc): HTMLElement {
  const songRows = songs.songs.map((s) =>
    h(
      'li',
      null,
      h('strong', null, s.title),
      s.composer ? ` — ${s.composer}` : '',
      s.sequencer ? ` (sequenced/typeset by ${s.sequencer})` : '',
      ' · ',
      s.source_url ? h('a', { href: s.source_url, target: '_blank', rel: 'noopener' }, 'source') : '',
      ' · ',
      s.license.url ? h('a', { href: s.license.url, target: '_blank', rel: 'noopener' }, s.license.id) : s.license.id,
      s.modifications && s.modifications !== 'none' ? h('div', { class: 'muted' }, `modifications: ${s.modifications}`) : '',
      s.license.notice_text ? h('details', null, h('summary', null, 'license notice'), h('pre', { class: 'notice' }, s.license.notice_text)) : '',
    ),
  );
  const engineRows = catalog.engines.map((e) =>
    h('li', null, h('strong', null, e.label), e.version ? ` ${e.version}` : '', e.commit ? ` (${e.commit})` : '', e.url ? [' · ', h('a', { href: e.url, target: '_blank', rel: 'noopener' }, e.url)] : '', e.license ? h('div', { class: 'muted' }, e.license) : ''),
  );
  return h(
    'article',
    { class: 'credits' },
    h('p', null, h('a', { href: '/' }, '← back to the player')),
    h('h1', null, 'About'),
    h(
      'p',
      null,
      'Every variant you hear is the same MIDI file rendered offline through a different SoundFont or synthesizer emulation, ',
      'measured with EBU R128 and brought to −16 LUFS integrated / −1.5 dBTP with a pure linear gain (no limiter, no dynamics). ',
      'Reverb and chorus are switched off on every engine so the comparison is fair. Audio is 48 kHz Opus.',
    ),
    h('h2', null, 'Songs'),
    h('ul', { class: 'songs' }, songRows),
    h('h2', null, 'Engines'),
    h('ul', { class: 'engines' }, engineRows),
    h('h2', null, 'SoundFonts and banks'),
    h(
      'p',
      null,
      'SoundFonts come from public collections on the Internet Archive; their authorship and copyright notices (when present in the file) are shown in the now-playing panel. ',
      'FM banks are the ones embedded in libADLMIDI / libOPNMIDI, several of which were extracted from period games. Variants derived from Roland ROMs or Roland-copyright sample sets are labelled.',
    ),
    h('h2', null, 'Takedown / DMCA'),
    h('p', null, `If you hold rights to something here and want it removed, email ${CONTACT} with the variant id (shown in the now-playing panel) and the work concerned; it will be taken down promptly.`),
    h('h2', null, 'Privacy'),
    h('p', null, 'No analytics, no cookies, no accounts. The debug panel (D) only shows numbers in your browser.'),
    h('p', { class: 'muted' }, `songs.json generated ${songs.generated_at}`),
  );
}
