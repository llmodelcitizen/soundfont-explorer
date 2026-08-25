// The shared admin DOM helpers. el() and note() used to be copied into every view (el ×4,
// note ×3); these pin the one definition each view now imports.
import { afterEach, describe, expect, it } from 'vitest';
import { el, note, statusLine } from '../../src/dom';

afterEach(() => document.body.replaceChildren());

describe('el', () => {
  it('sets class through className and everything else through setAttribute', () => {
    const e = el('input', { class: 'a b', type: 'search', placeholder: 'filter…' });
    expect(e.tagName).toBe('INPUT');
    expect(e.className).toBe('a b');
    expect(e.getAttribute('type')).toBe('search');
    expect(e.getAttribute('placeholder')).toBe('filter…');
    expect(e.getAttribute('class')).toBe('a b');
  });

  it('appends nodes and strings as children, in order', () => {
    const e = el('div', {}, 'a', el('span', {}, 'b'), 'c');
    expect(e.childNodes.length).toBe(3);
    expect(e.textContent).toBe('abc');
    expect((e.childNodes[1] as HTMLElement).tagName).toBe('SPAN');
  });

  it('takes no attributes and no children', () => {
    const e = el('div');
    expect(e.attributes.length).toBe(0);
    expect(e.childNodes.length).toBe(0);
  });
});

describe('note', () => {
  it('replaces the text and toggles the error class both ways', () => {
    const s = statusLine();
    expect(s.className).toBe('statusline');
    note(s, 'working…');
    expect(s.textContent).toBe('working…');
    expect(s.classList.contains('error')).toBe(false);
    note(s, 'boom', true);
    expect(s.textContent).toBe('boom');
    expect(s.classList.contains('error')).toBe(true);
    note(s, 'done');
    expect(s.textContent).toBe('done');
    expect(s.classList.contains('error')).toBe(false);
  });
});
