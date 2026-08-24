#!/usr/bin/env node
/** Compare modern, Windows 95, and Amiga geometry at desktop and phone sizes. */
import { chromium } from 'playwright';

const args = Object.fromEntries(process.argv.slice(2).map((arg) => arg.replace(/^--/, '').split('=')));
const url = args.url ?? 'http://127.0.0.1:5173/';
const viewports = {
  desktop: { width: 1280, height: 800 },
  phone: { width: 390, height: 844 },
};
const errors = [];
const failures = [];
const browser = await chromium.launch();

const close = (a, b, tolerance = 1) => Math.abs(a - b) <= tolerance;
const assert = (condition, message) => {
  if (!condition) failures.push(message);
};

async function measure(theme, viewport, label) {
  const page = await browser.newPage({ viewport, hasTouch: label === 'phone' });
  page.on('pageerror', (error) => errors.push(`${label}/${theme}: ${String(error)}`));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(`${label}/${theme}: ${message.text()}`);
  });
  const target = new URL(url);
  target.searchParams.set('theme', theme);
  await page.goto(target.href, { waitUntil: 'networkidle' });
  await page.waitForSelector('.rows .row', { timeout: 20000 });
  await page.evaluate(async () => {
    await Promise.all([
      document.fonts.load('11px "Pixelated MS Sans Serif"'),
      document.fonts.load('16px "Fixedsys Excelsior"'),
    ]);
    await document.fonts.ready;
  });
  const result = await page.evaluate(() => {
    const rect = (element) => {
      if (!element) return null;
      const box = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      if (style.display === 'none' || style.visibility === 'hidden' || box.width === 0 || box.height === 0) return null;
      return { x: box.x, y: box.y, w: box.width, h: box.height, right: box.right, bottom: box.bottom, cx: box.x + box.width / 2, cy: box.y + box.height / 2 };
    };
    const allRects = (selector) => Array.from(document.querySelectorAll(selector)).map(rect).filter(Boolean);
    const overlaps = (rects) => {
      const found = [];
      for (let i = 0; i < rects.length; i++) {
        for (let j = i + 1; j < rects.length; j++) {
          const a = rects[i];
          const b = rects[j];
          if (Math.min(a.right, b.right) - Math.max(a.x, b.x) > 0.5 && Math.min(a.bottom, b.bottom) - Math.max(a.y, b.y) > 0.5) found.push([i, j]);
        }
      }
      return found;
    };
    const buttonInfo = (selector) => Array.from(document.querySelectorAll(selector)).map((button) => {
      const outer = rect(button);
      if (!outer) return null;
      const svg = rect(button.querySelector('svg'));
      return { outer, svg, label: button.getAttribute('aria-label') ?? button.textContent?.trim() ?? '' };
    }).filter(Boolean);
    const gap = (selector) => {
      const element = document.querySelector(selector);
      if (!element) return null;
      const style = getComputedStyle(element);
      return { column: style.columnGap, row: style.rowGap };
    };
    const namedRects = {};
    for (const selector of ['#app', '.top', '.main', '.left', '.right', '.filterbar', '.transport', '.row.head']) namedRects[selector] = rect(document.querySelector(selector));
    const headerControls = allRects('.top > .themepick, .top > .btn, .top > .vol-top');
    const mainButtons = buttonInfo('.transport > .btn');
    const stepButtons = buttonInfo('.transport .steps > .btn');
    const selected = document.querySelector('.rows .row.sel');
    const selectedStyle = selected ? getComputedStyle(selected) : null;
    const selectedText = (selector) => {
      const element = selected?.querySelector(selector);
      return element ? getComputedStyle(element).color : null;
    };
    const firstOpt = document.createElement('button');
    firstOpt.className = 'opt on';
    firstOpt.style.position = 'fixed';
    firstOpt.style.visibility = 'hidden';
    firstOpt.append('engine', Object.assign(document.createElement('span'), { className: 'cnt', textContent: '1' }));
    document.body.append(firstOpt);
    const optStyle = getComputedStyle(firstOpt);
    const highlight = {
      rowBackground: selectedStyle?.backgroundColor ?? null,
      rowColor: selectedStyle?.color ?? null,
      labelColor: selectedText('.label'),
      metaColor: selectedText('.meta'),
      indexColor: selectedText('.idx'),
      optionBackground: optStyle?.backgroundColor ?? null,
      optionColor: optStyle?.color ?? null,
      optionCountColor: getComputedStyle(firstOpt.querySelector('.cnt')).color,
    };
    firstOpt.remove();
    return {
      viewport: { width: innerWidth, height: innerHeight },
      overflow: {
        document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        app: document.querySelector('#app').scrollWidth - document.querySelector('#app').clientWidth,
      },
      namedRects,
      rowHeights: allRects('.rows .row').slice(0, 8).map((item) => item.h),
      gaps: { top: gap('.top'), filters: gap('.filterrow'), transport: gap('.transport'), rows: gap('.row') },
      order: {
        header: Array.from(document.querySelector('.top').children).map((element) => element.className),
        transport: Array.from(document.querySelector('.transport').children).map((element) => element.className),
      },
      headerCenters: headerControls.map((item) => item.cy),
      headerOverlaps: overlaps(headerControls),
      paneOverlaps: overlaps(allRects('.main > .left, .main > .right')),
      mainButtons,
      stepButtons,
      highlight,
      fonts: {
        msSans: document.fonts.check('11px "Pixelated MS Sans Serif"'),
        fixedsys: document.fonts.check('16px "Fixedsys Excelsior"'),
      },
      theme: document.documentElement.dataset.theme,
      dropdown: document.querySelector('.themepick').value,
    };
  });
  await page.close();
  return result;
}

for (const [label, viewport] of Object.entries(viewports)) {
  const modern = await measure('modern', viewport, label);
  const win95 = await measure('win95', viewport, label);
  const amiga = await measure('amiga', viewport, label);
  for (const snapshot of [modern, win95, amiga]) {
    assert(snapshot.overflow.document <= 0.5, `${label}/${snapshot.theme}: document overflows horizontally by ${snapshot.overflow.document}px`);
    assert(snapshot.overflow.app <= 0.5, `${label}/${snapshot.theme}: app overflows horizontally by ${snapshot.overflow.app}px`);
    assert(snapshot.headerOverlaps.length === 0, `${label}/${snapshot.theme}: overlapping header controls ${JSON.stringify(snapshot.headerOverlaps)}`);
    assert(snapshot.paneOverlaps.length === 0, `${label}/${snapshot.theme}: overlapping main panes`);
    assert(snapshot.theme === snapshot.dropdown, `${label}/${snapshot.theme}: dropdown is ${snapshot.dropdown}`);
    assert(new Set(snapshot.rowHeights.map((height) => Math.round(height * 10))).size === 1, `${label}/${snapshot.theme}: unequal row heights ${snapshot.rowHeights}`);
    for (const group of [snapshot.mainButtons, snapshot.stepButtons]) {
      if (group.length > 1) {
        const firstWidth = group[0].outer.w;
        assert(group.every((button) => close(button.outer.w, firstWidth)), `${label}/${snapshot.theme}: unequal transport widths ${group.map((button) => button.outer.w)}`);
      }
      for (const button of group) {
        if (button.svg) assert(close(button.outer.cx, button.svg.cx) && close(button.outer.cy, button.svg.cy), `${label}/${snapshot.theme}: off-center ${button.label} icon`);
      }
    }
    if (snapshot.headerCenters.length > 1) assert(Math.max(...snapshot.headerCenters) - Math.min(...snapshot.headerCenters) <= 1, `${label}/${snapshot.theme}: header controls are not vertically centered`);
  }

  assert(win95.fonts.msSans && win95.fonts.fixedsys, `${label}/win95: bundled fonts did not load`);
  assert(win95.highlight.rowBackground === 'rgb(0, 0, 128)', `${label}/win95: selected row background is ${win95.highlight.rowBackground}`);
  for (const [part, color] of Object.entries(win95.highlight)) {
    if (color !== null && part !== 'rowBackground' && part !== 'optionBackground') assert(color === 'rgb(255, 255, 255)', `${label}/win95: highlighted ${part} is ${color}`);
  }
  assert(win95.highlight.optionBackground === 'rgb(0, 0, 128)', `${label}/win95: selected option background is ${win95.highlight.optionBackground}`);

  for (const themed of [win95, amiga]) assert(JSON.stringify(themed.order) === JSON.stringify(modern.order), `${label}/${themed.theme}: control order changed`);
  assert(win95.rowHeights.every((height) => close(height, 20)), `${label}/win95: rows are not the native-density 20px target`);

  assert(JSON.stringify(amiga.gaps) === JSON.stringify(modern.gaps), `${label}/amiga: shared gaps changed`);
  assert(amiga.rowHeights.every((height, index) => close(height, modern.rowHeights[index])), `${label}/amiga: row heights changed`);
  for (const selector of ['#app', '.top', '.main', '.left', '.right', '.filterbar', '.transport', '.row.head']) {
      const actual = amiga.namedRects[selector];
      const expected = modern.namedRects[selector];
      if (actual && expected) assert(close(actual.w, expected.w) && close(actual.h, expected.h), `${label}/amiga: ${selector} changed from ${expected.w}×${expected.h} to ${actual.w}×${actual.h}`);
  }
  for (const key of ['mainButtons', 'stepButtons']) {
      amiga[key].forEach((button, index) => {
        const expected = modern[key][index];
        if (expected) assert(close(button.outer.w, expected.outer.w) && close(button.outer.h, expected.outer.h), `${label}/amiga: ${button.label} changed from ${expected.outer.w}×${expected.outer.h} to ${button.outer.w}×${button.outer.h}`);
      });
  }
}

await browser.close();
if (errors.length) failures.push(...errors);
console.log(JSON.stringify({ ok: failures.length === 0, failures }, null, 2));
process.exit(failures.length ? 1 : 0);
