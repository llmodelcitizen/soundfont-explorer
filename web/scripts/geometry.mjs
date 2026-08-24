#!/usr/bin/env node
/** Compare modern, Windows 95, and Amiga geometry at desktop and phone sizes. */
import { readFileSync } from 'node:fs';
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

// period-correctness statics: the Win95 stylesheet may not use gradients or round any corner
const win95Css = readFileSync(new URL('../src/styles/theme-win95.css', import.meta.url), 'utf8');
assert(!/gradient\(/.test(win95Css), 'win95 stylesheet uses gradients');
assert(!/border-radius:(?!\s*0\s*[;}])/.test(win95Css), 'win95 stylesheet rounds a corner');

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
    const verticallyCenteredHeader = (selector) => {
      const header = document.querySelector(selector);
      const title = header?.querySelector('.np-title');
      const outer = rect(header);
      const inner = rect(title);
      if (!header || !outer || !inner) return null;
      const style = getComputedStyle(header);
      return { offset: inner.cy - outer.cy, paddingLeft: style.paddingLeft, paddingRight: style.paddingRight };
    };
    const opticallyPositionedGlyph = (selector) => {
      const cell = document.querySelector(selector);
      const label = cell?.querySelector('.hlabel');
      const outer = rect(cell);
      const glyph = rect(label);
      if (!cell || !label || !outer || !glyph) return null;
      return { offset: glyph.cy - outer.cy, fontSize: getComputedStyle(cell).fontSize, transform: getComputedStyle(label).transform };
    };
    const debugTitleBar = (() => {
      const debug = document.querySelector('.debug');
      const header = debug?.querySelector('.dbg-head');
      const caption = header?.querySelector('strong');
      if (!debug || !header || !caption) return null;
      const wasHidden = debug.classList.contains('hidden');
      debug.classList.remove('hidden');
      const outer = rect(header);
      const title = rect(caption);
      const buttons = allRects('.dbg-actions .btn');
      const style = getComputedStyle(header);
      const result = outer && title ? {
        height: outer.h,
        paddingLeft: style.paddingLeft,
        captionOffset: title.cy - outer.cy,
        buttonOffsets: buttons.map((button) => button.cy - outer.cy),
      } : null;
      if (wasHidden) debug.classList.add('hidden');
      return result;
    })();
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
    const probeBtn = document.createElement('button');
    probeBtn.className = 'btn';
    probeBtn.disabled = true;
    probeBtn.textContent = 'probe';
    probeBtn.style.position = 'fixed';
    probeBtn.style.visibility = 'hidden';
    const probeLink = document.createElement('a');
    probeLink.href = '#';
    probeLink.textContent = 'probe';
    probeLink.style.position = 'fixed';
    probeLink.style.visibility = 'hidden';
    document.body.append(probeBtn, probeLink);
    const states = {
      disabledColor: getComputedStyle(probeBtn).color,
      linkColor: getComputedStyle(probeLink).color,
    };
    probeBtn.remove();
    probeLink.remove();
    const headingProbe = document.createElement('h2');
    const creditsProbe = document.createElement('div');
    creditsProbe.className = 'credits';
    creditsProbe.style.position = 'fixed';
    creditsProbe.style.visibility = 'hidden';
    creditsProbe.append(headingProbe);
    document.body.append(creditsProbe);
    const titleStyle = getComputedStyle(document.querySelector('.top .title'));
    const headingStyle = getComputedStyle(headingProbe);
    const titleTypography = {
      fontFamily: titleStyle.fontFamily,
      fontSize: titleStyle.fontSize,
      fontWeight: titleStyle.fontWeight,
      lineHeight: titleStyle.lineHeight,
      headingFamily: headingStyle.fontFamily,
      headingSize: headingStyle.fontSize,
      headingWeight: headingStyle.fontWeight,
    };
    creditsProbe.remove();
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
      states,
      titleTypography,
      debugTitleBar,
      amigaVerticalAlignment: {
        tracks: verticallyCenteredHeader('.tracks .np-head'),
        nowPlaying: verticallyCenteredHeader('.nowplaying .np-head'),
        favoriteHeader: opticallyPositionedGlyph('.row.head .hcell.col-fav'),
        listenedHeader: opticallyPositionedGlyph('.row.head .hcell.col-dot'),
        favoriteContent: (() => {
          const cell = document.querySelector('.row:not(.head) .cell.fav');
          const row = cell?.closest('.row');
          if (!cell || !row) return null;
          const wasFavorite = row.classList.contains('favorite');
          row.classList.add('favorite');
          const style = getComputedStyle(cell);
          const result = { fontSize: style.fontSize, height: style.height, background: style.backgroundColor, color: style.color };
          if (!wasFavorite) row.classList.remove('favorite');
          return result;
        })(),
        listenedContentSize: (() => {
          const dot = rect(document.querySelector('.row:not(.head) .cell.dot'));
          return dot ? `${dot.w}x${dot.h}` : null;
        })(),
      },
      folderEdge: (() => {
        const folder = document.createElement('div');
        folder.className = 'track-folder';
        folder.style.position = 'fixed';
        folder.style.visibility = 'hidden';
        document.body.append(folder);
        const edge = getComputedStyle(folder, '::before');
        const result = { content: edge.content, width: edge.width, background: edge.backgroundColor, shadow: edge.boxShadow };
        folder.remove();
        return result;
      })(),
      fonts: {
        msSans: document.fonts.check('11px "Pixelated MS Sans Serif"'),
        fixedsys: document.fonts.check('16px "Fixedsys Excelsior"'),
      },
      clockPositions: (() => {
        const current = document.querySelector('.clock-current');
        const separator = document.querySelector('.clock-separator');
        const seek = document.querySelector('.transport .seek');
        if (!current || !separator || !seek) return [];
        const chars = Array.from(current.querySelectorAll('.clock-char'));
        return ['0:00.000', '0:11.111', '0:28.888', '0:59.999'].map((value) => {
          const padded = value.padStart(chars.length);
          chars.forEach((char, index) => (char.textContent = padded[index] === ' ' ? '' : padded[index]));
          return { separator: rect(separator)?.x, seek: rect(seek)?.x, chars: chars.map((char) => rect(char)?.x) };
        });
      })(),
      theme: document.documentElement.dataset.theme,
      dropdown: document.querySelector('.themepick').value,
    };
  });
  const hoverRow = page.locator('.rows .row:not(.head):not(.sticky):not(.sel)').nth(1);
  await hoverRow.hover();
  result.hover = await hoverRow.evaluate((el) => {
    const style = getComputedStyle(el);
    const label = el.querySelector('.label');
    const meta = el.querySelector('.meta');
    return {
      background: style.backgroundColor,
      labelColor: label ? getComputedStyle(label).color : null,
      metaColor: meta ? getComputedStyle(meta).color : null,
    };
  });
  // Tab is an app shortcut (A/B), so probe keyboard focus by focusing the theme select directly.
  result.focusOutline = await page.evaluate(() => {
    const select = document.querySelector('.themepick');
    select.focus();
    const style = getComputedStyle(select).outlineStyle;
    select.blur();
    return style;
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
  assert(win95.titleTypography.fontFamily === win95.titleTypography.headingFamily && win95.titleTypography.fontSize === win95.titleTypography.headingSize && win95.titleTypography.fontWeight === win95.titleTypography.headingWeight, `${label}/win95: title typography does not match About headings: ${JSON.stringify(win95.titleTypography)}`);
  assert(win95.titleTypography.lineHeight === '29px', `${label}/win95: title line height is ${win95.titleTypography.lineHeight}, not 29px`);
  assert(win95.folderEdge?.content === '\"\"' && win95.folderEdge.width === '1px' && win95.folderEdge.background === 'rgb(128, 128, 128)' && win95.folderEdge.shadow.includes('rgb(0, 0, 0)'), `${label}/win95: folder header does not preserve the Tracks well edge: ${JSON.stringify(win95.folderEdge)}`);
  assert(new Set(win95.clockPositions.map((position) => JSON.stringify(position.chars))).size === 1, `${label}/win95: clock character slots shift as the current time changes: ${JSON.stringify(win95.clockPositions)}`);
  assert(new Set(win95.clockPositions.map((position) => position.separator)).size === 1, `${label}/win95: clock separator shifts as the current time changes: ${JSON.stringify(win95.clockPositions)}`);
  assert(new Set(win95.clockPositions.map((position) => position.seek)).size === 1, `${label}/win95: seek bar shifts as the current time changes: ${JSON.stringify(win95.clockPositions)}`);
  assert(win95.highlight.rowBackground === 'rgb(0, 0, 128)', `${label}/win95: selected row background is ${win95.highlight.rowBackground}`);
  for (const [part, color] of Object.entries(win95.highlight)) {
    if (color !== null && part !== 'rowBackground' && part !== 'optionBackground') assert(color === 'rgb(255, 255, 255)', `${label}/win95: highlighted ${part} is ${color}`);
  }
  assert(win95.highlight.optionBackground === 'rgb(0, 0, 128)', `${label}/win95: selected option background is ${win95.highlight.optionBackground}`);
  assert(win95.hover.background === 'rgb(0, 0, 128)', `${label}/win95: hovered row background is ${win95.hover.background}`);
  for (const [part, color] of Object.entries(win95.hover)) {
    if (color !== null && part !== 'background') assert(color === 'rgb(255, 255, 255)', `${label}/win95: hovered ${part} is ${color}`);
  }
  assert(win95.states.disabledColor === 'rgb(128, 128, 128)', `${label}/win95: disabled button text is ${win95.states.disabledColor}`);
  assert(win95.states.linkColor === 'rgb(0, 0, 255)', `${label}/win95: link color is ${win95.states.linkColor}`);
  assert(win95.focusOutline === 'dotted', `${label}/win95: keyboard focus outline is ${win95.focusOutline}, not dotted`);
  for (const themed of [win95, amiga]) {
    const titleBar = themed.debugTitleBar;
    assert(titleBar?.height === 36 && titleBar.paddingLeft === '8px', `${label}/${themed.theme}: debug title bar does not have the themed dimensions: ${JSON.stringify(titleBar)}`);
    assert(titleBar && close(titleBar.captionOffset, 0, 0.5), `${label}/${themed.theme}: debug caption is not vertically centered: ${JSON.stringify(titleBar)}`);
    assert(titleBar?.buttonOffsets.every((offset) => close(offset, 0, 0.5)), `${label}/${themed.theme}: debug title-bar buttons are not vertically centered: ${JSON.stringify(titleBar)}`);
  }
  for (const [name, alignment] of Object.entries(amiga.amigaVerticalAlignment)) {
    if (name === 'favoriteContent' || name === 'listenedContentSize') continue;
    if (name === 'tracks' && label === 'phone') continue; // the phone layout replaces Tracks with the song dropdown
    const expectedOffset = name === 'favoriteHeader' ? -3 : name === 'listenedHeader' ? -4 : 0;
    assert(alignment && close(alignment.offset, expectedOffset, 0.75), `${label}/amiga: ${name} lacks its expected vertical alignment: ${JSON.stringify(alignment)}`);
    if (name === 'tracks' || name === 'nowPlaying') assert(alignment?.paddingLeft === '12px' && alignment?.paddingRight === '12px', `${label}/amiga: ${name} title padding is not 12px: ${JSON.stringify(alignment)}`);
    else {
      assert(alignment?.fontSize === '16px', `${label}/amiga: ${name} is not 16px: ${JSON.stringify(alignment)}`);
      const expectedTransform = name === 'favoriteHeader' ? 'matrix(1, 0, 0, 1, 0, -3)' : 'matrix(1, 0, 0, 1, 0, -4)';
      assert(alignment?.transform === expectedTransform, `${label}/amiga: ${name} lacks its optical correction: ${JSON.stringify(alignment)}`);
    }
  }
  const favoriteContent = amiga.amigaVerticalAlignment.favoriteContent;
  assert(favoriteContent?.fontSize === '16px' && favoriteContent.height === '16px' && favoriteContent.background === 'rgba(0, 0, 0, 0)' && favoriteContent.color === 'rgb(0, 0, 0)', `${label}/amiga: row heart is not a transparent 16px black glyph: ${JSON.stringify(favoriteContent)}`);
  assert(amiga.amigaVerticalAlignment.listenedContentSize === '10x10', `${label}/amiga: row listened dot size changed: ${amiga.amigaVerticalAlignment.listenedContentSize}`);

  // Both replacement themes are paint-only: geometry must match modern exactly.
  for (const themed of [win95, amiga]) {
    assert(JSON.stringify(themed.order) === JSON.stringify(modern.order), `${label}/${themed.theme}: control order changed`);
    assert(JSON.stringify(themed.gaps) === JSON.stringify(modern.gaps), `${label}/${themed.theme}: shared gaps changed`);
    assert(themed.rowHeights.every((height, index) => close(height, modern.rowHeights[index])), `${label}/${themed.theme}: row heights changed`);
    for (const selector of ['#app', '.top', '.main', '.left', '.right', '.filterbar', '.transport', '.row.head']) {
      const actual = themed.namedRects[selector];
      const expected = modern.namedRects[selector];
      if (actual && expected) assert(close(actual.w, expected.w) && close(actual.h, expected.h), `${label}/${themed.theme}: ${selector} changed from ${expected.w}×${expected.h} to ${actual.w}×${actual.h}`);
    }
    for (const key of ['mainButtons', 'stepButtons']) {
      themed[key].forEach((button, index) => {
        const expected = modern[key][index];
        if (expected) assert(close(button.outer.w, expected.outer.w) && close(button.outer.h, expected.outer.h), `${label}/${themed.theme}: ${button.label} changed from ${expected.outer.w}×${expected.outer.h} to ${button.outer.w}×${button.outer.h}`);
      });
    }
  }
}

await browser.close();
if (errors.length) failures.push(...errors);
console.log(JSON.stringify({ ok: failures.length === 0, failures }, null, 2));
process.exit(failures.length ? 1 : 0);
