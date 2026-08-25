/**
 * The parts of the help affordance that only a stylesheet can state: the tap-target size the issue's
 * accessibility label asks for, in every theme, and the viewport unit the bubble is capped with.
 * Neither can be observed in jsdom (it evaluates no media queries and knows no dvh), so the cascade
 * is read here instead — which is also where the amiga regression lived: a media query adds no
 * specificity, so a themed width silently outranks the coarse-pointer rule (issue #26).
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

interface Rule {
  selector: string;
  body: string;
  media: string;
}

/** every style rule of a stylesheet, flattened, each tagged with the @media it sits in (if any) */
function rules(source: string, media = '', out: Rule[] = []): Rule[] {
  const src = media ? source : source.replace(/\/\*[\s\S]*?\*\//g, '');
  let i = 0;
  let from = 0;
  while (i < src.length) {
    if (src[i] !== '{') {
      i += 1;
      continue;
    }
    const selector = src.slice(from, i).trim();
    let depth = 1;
    let j = i + 1;
    while (j < src.length && depth > 0) {
      if (src[j] === '{') depth += 1;
      else if (src[j] === '}') depth -= 1;
      j += 1;
    }
    const body = src.slice(i + 1, j - 1);
    if (selector.startsWith('@media')) rules(body, selector, out);
    else if (!selector.startsWith('@')) for (const one of selector.split(',')) out.push({ selector: one.trim(), body, media });
    i = j;
    from = j;
  }
  return out;
}

const sheet = (name: string) => rules(readFileSync(new URL(`../../src/styles/${name}`, import.meta.url), 'utf8'));
const base = sheet('base.css');

const coarse = (r: Rule) => r.media.includes('pointer: coarse');
const find = (all: Rule[], selector: string, touch: boolean) => all.filter((r) => r.selector === selector && (touch ? coarse(r) : r.media === ''));

const px = (bodies: Rule[], prop: string): number | null => {
  for (const { body } of bodies) {
    const m = body.match(new RegExp(`(?:^|[;{\\s])${prop}\\s*:\\s*(-?\\d+)px`));
    if (m?.[1]) return Number(m[1]);
  }
  return null;
};

/**
 * The size a touch screen ends up giving the "?" of a theme, once the cascade is settled. A theme's
 * [data-theme=…] .facet-help-btn is 0,2,0 and beats base.css's bare 0,1,0 wherever it declares the
 * property — media queries add no specificity, so a themed desktop width outranks base.css's
 * coarse-pointer rule, and only a coarse rule of the theme's own can raise it.
 */
function touchSize(theme: 'win95' | 'amiga'): { width: number | null; height: number | null } {
  const themeSheet = sheet(`theme-${theme}.css`);
  const selector = `[data-theme='${theme}'] .facet-help-btn`;
  const order = (prop: 'width' | 'height') =>
    px(find(themeSheet, selector, true), prop) ?? px(find(themeSheet, selector, false), prop) ?? px(find(base, '.facet-help-btn', true), prop) ?? px(find(base, '.facet-help-btn', false), prop);
  return { width: order('width'), height: order('height') };
}

describe('the help trigger as a pointer target', () => {
  it('meets the 24x24 minimum on a touch screen in the default theme', () => {
    const touch = find(base, '.facet-help-btn', true);
    expect(touch.length).toBeGreaterThan(0);
    expect(px(touch, 'width')).toBeGreaterThanOrEqual(24);
    expect(px(touch, 'height')).toBeGreaterThanOrEqual(24);
  });

  it.each(['win95', 'amiga'] as const)('meets it in the %s theme too, whatever size that theme draws', (theme) => {
    const { width, height } = touchSize(theme);
    expect(width, `${theme} keeps a small tap target on a touch screen`).toBeGreaterThanOrEqual(24);
    expect(height, `${theme} keeps a small tap target on a touch screen`).toBeGreaterThanOrEqual(24);
  });

  it('pads the small desktop gadget out to the same 24x24 target', () => {
    const btn = find(base, '.facet-help-btn', false);
    const size = px(btn, 'width') ?? 0;
    // everything is border-box (base.css:5), and an absolutely positioned pad is inset from the
    // padding box: the border it sits inside comes off the target twice
    const border = Number((btn[0]?.body ?? '').match(/border\s*:\s*(\d+)px/)?.[1] ?? 0);
    const inset = -(px(find(base, '.facet-help-btn::after', false), 'inset') ?? 0);
    expect(size - 2 * border + 2 * inset).toBeGreaterThanOrEqual(24);
    // and the heading's own margin keeps that pad off the first chip of the row below
    const heading = find(base, '.facet-name', false)[0]?.body ?? '';
    const margin = heading.match(/margin\s*:\s*(\d+)px\s+0(?:\s+(\d+)px)?/);
    expect(Number(margin?.[2] ?? margin?.[1] ?? 0)).toBeGreaterThanOrEqual(inset - border);
  });
});

describe('the bubble on a phone', () => {
  it('is capped against the visible viewport, not the one the URL bar hides part of', () => {
    const body = find(base, '.facet-tip', false)[0]?.body ?? '';
    expect(body).toContain('max-height: calc(100vh - 16px)');
    expect(body).toContain('max-height: calc(100dvh - 16px)');
    expect(body.indexOf('100dvh')).toBeGreaterThan(body.indexOf('100vh'));
  });
});
