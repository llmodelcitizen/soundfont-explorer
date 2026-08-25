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
      document.fonts.load('14px "SFP IBM Plex Sans"', 'Soundfont Explorer'),
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
      // the listened LED through the states the row classes produce, painted on a real row so
      // that the whole cascade (base.css and the theme) decides the colour, as it does live
      listenedLed: (() => {
        const row = document.querySelector('.rows .row');
        const dot = row?.querySelector('.cell.dot');
        if (!row || !dot) return null;
        const was = row.className;
        const paint = (classes) => {
          row.className = classes;
          return getComputedStyle(dot).backgroundColor;
        };
        const led = {
          listened: paint('row cached'),
          playing: paint('row audible'),
          selectedListened: paint('row sel cached'),
          selectedPlaying: paint('row sel cached audible'),
          selectedBuffering: paint('row sel cached audible loading'),
        };
        row.className = was;
        return led;
      })(),
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
    const dot = el.querySelector('.cell.dot');
    const was = el.className;
    const paint = (classes) => {
      el.className = classes;
      return dot ? getComputedStyle(dot).backgroundColor : null;
    };
    const listenedDot = paint(`${was} cached`);
    const playingDot = paint(`${was} cached audible`);
    el.className = was;
    return {
      background: style.backgroundColor,
      labelColor: label ? getComputedStyle(label).color : null,
      metaColor: meta ? getComputedStyle(meta).color : null,
      listenedDot,
      playingDot,
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
  result.fontCycle = theme === 'modern' ? await page.evaluate(async () => {
    const title = document.querySelector('.top .title');
    const root = document.documentElement;
    if (!(title instanceof HTMLElement)) return null;
    const states = [];
    for (let index = 0; index < 14; index++) {
      const family = getComputedStyle(document.body).fontFamily;
      const primary = family.split(',')[0];
      const loaded = (await document.fonts.load(`14px ${primary}`, 'Soundfont Explorer 1990s')).length > 0;
      await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));
      const controls = Array.from(document.querySelectorAll('.top > .themepick, .top > .btn, .top > .vol-top')).map((element) => element.getBoundingClientRect());
      const overlaps = controls.some((a, i) => controls.slice(i + 1).some((b) => Math.min(a.right, b.right) - Math.max(a.left, b.left) > 0.5 && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 0.5));
      states.push({
        id: root.dataset.modernFont ?? null,
        family,
        stored: localStorage.getItem('sfp.modern-font.v1'),
        loaded,
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        headerOverlap: overlaps,
        filterHeight: document.querySelector('.filterbar')?.getBoundingClientRect().height ?? null,
        rowHeights: [...new Set(Array.from(document.querySelectorAll('.rows .row')).slice(0, 8).map((row) => row.getBoundingClientRect().height))],
      });
      title.click();
    }
    return {
      states,
      wrapped: { id: root.dataset.modernFont ?? null, stored: localStorage.getItem('sfp.modern-font.v1') },
      role: title.getAttribute('role'),
      tabIndex: title.tabIndex,
    };
  }) : await page.evaluate(() => {
    const title = document.querySelector('.top .title');
    const root = document.documentElement;
    const before = { id: root.dataset.modernFont ?? null, stored: localStorage.getItem('sfp.modern-font.v1') };
    title?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    return {
      before,
      after: { id: root.dataset.modernFont ?? null, stored: localStorage.getItem('sfp.modern-font.v1') },
      role: title?.getAttribute('role') ?? null,
      tabIndex: title instanceof HTMLElement ? title.tabIndex : null,
    };
  });
  result.settingsFontReset = await page.evaluate(() => {
    const root = document.documentElement;
    if (root.dataset.theme === 'modern') document.querySelector('.top .title')?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    document.querySelector('.top [aria-label="settings"]')?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    const buttons = Array.from(document.querySelectorAll('.settings .colaction'));
    const defaults = buttons.find((button) => button.textContent?.trim() === 'defaults');
    const reset = buttons.find((button) => button.textContent?.trim() === 'reset font');
    const guidance = document.querySelector('.settings .fontfoot span')?.textContent?.trim() ?? null;
    const box = document.querySelector('.settings')?.getBoundingClientRect();
    const defaultsBox = defaults?.getBoundingClientRect();
    const resetBox = reset?.getBoundingClientRect();
    reset?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    return {
      guidance,
      equalButtons: !!defaultsBox && !!resetBox && Math.abs(defaultsBox.width - resetBox.width) <= 0.5 && Math.abs(defaultsBox.height - resetBox.height) <= 0.5,
      resetBelow: !!defaultsBox && !!resetBox && resetBox.top >= defaultsBox.bottom,
      fitsViewport: !!box && box.top >= 0 && box.bottom <= innerHeight,
      font: { id: root.dataset.modernFont ?? null, stored: localStorage.getItem('sfp.modern-font.v1') },
    };
  });
  await page.close();
  return result;
}

/**
 * Phone UI state that must not survive a song switch: the open filter panel hides Now Playing
 * (`.filters-open .right { display: none }`) and its scrim covers everything below the bar, so
 * the panel is rebuilt closed for the new song — the class and the scrim have to go with it.
 * The App has no unit test (there is no DOM in the vitest toolchain), so it is checked here.
 */
async function filterPanelResetsOnSongSwitch() {
  const label = 'phone';
  const page = await browser.newPage({ viewport: viewports.phone, hasTouch: true });
  page.on('pageerror', (error) => errors.push(`${label}/modern: ${String(error)}`));
  const target = new URL(url);
  target.searchParams.set('theme', 'modern');
  await page.goto(target.href, { waitUntil: 'networkidle' });
  await page.waitForSelector('.rows .row', { timeout: 20000 });
  const opened = await page.evaluate(() => {
    const toggle = Array.from(document.querySelectorAll('.filterrow .btn')).find((button) => (button.title ?? '').startsWith('filters'));
    toggle?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    const right = document.querySelector('.right');
    document.querySelector('.row.head')?.setAttribute('data-geometry-probe', 'before'); // survives only if the UI is not rebuilt
    return {
      open: document.querySelector('#app')?.classList.contains('filters-open') ?? false,
      scrim: !!document.querySelector('.filter-scrim'),
      nowPlayingHidden: !!right && getComputedStyle(right).display === 'none',
      songs: Array.from(document.querySelector('.songpicker')?.options ?? []).map((option) => option.value),
      song: document.querySelector('.songpicker')?.value ?? null,
    };
  });
  assert(opened.open && opened.scrim && opened.nowPlayingHidden, `${label}/modern: the filter panel did not open over Now Playing: ${JSON.stringify(opened)}`);
  const next = opened.songs.find((song) => song !== opened.song);
  // a catalogue with a single song cannot exercise a switch: skip rather than fail, so the
  // check depends on what the fix does and not on how many songs the server happens to serve
  if (!next) process.stderr.write(`${label}/modern: only one song served — skipping the filter-panel song-switch check\n`);
  if (next) {
    await page.evaluate((song) => {
      const picker = document.querySelector('.songpicker');
      picker.value = song;
      picker.dispatchEvent(new Event('change', { bubbles: true }));
    }, next);
    try {
      await page.waitForFunction(() => {
        const head = document.querySelector('.row.head');
        return !!head && !head.hasAttribute('data-geometry-probe');
      }, undefined, { timeout: 20000 });
      const after = await page.evaluate(() => {
        const right = document.querySelector('.right');
        return {
          song: document.querySelector('.songpicker')?.value ?? null,
          open: document.querySelector('#app')?.classList.contains('filters-open') ?? false,
          scrim: !!document.querySelector('.filter-scrim'),
          nowPlayingVisible: !!right && getComputedStyle(right).display !== 'none' && right.getBoundingClientRect().height > 0,
        };
      });
      assert(after.song === next && !after.open && !after.scrim && after.nowPlayingVisible, `${label}/modern: the filter panel survived the song switch: ${JSON.stringify(after)}`);
    } catch (error) {
      failures.push(`${label}/modern: the list was never rebuilt for the new song: ${String(error)}`);
    }
  }
  await page.close();
}

await filterPanelResetsOnSongSwitch();

/**
 * Phone portrait only: the open filter panel takes the whole grid, so the handle that resizes
 * Now Playing must go with the pane it resizes. It is positioned, so left in its grid row it
 * paints its bar across the filter buttons of a panel tall enough to reach that row. CSS in a
 * breakpoint has no unit test (web/test/unit runs in the node environment, with no cascade), so
 * it is measured here, in all three themes: no filter button may be under the handle.
 */
async function filterPanelClearsTheResizeHandle() {
  for (const theme of ['modern', 'win95', 'amiga']) {
    const label = `phone/${theme}`;
    const page = await browser.newPage({ viewport: viewports.phone, hasTouch: true });
    page.on('pageerror', (error) => errors.push(`${label}: ${String(error)}`));
    const target = new URL(url);
    target.searchParams.set('theme', theme);
    await page.goto(target.href, { waitUntil: 'networkidle' });
    await page.waitForSelector('.rows .row', { timeout: 20000 });
    const result = await page.evaluate(() => {
      const handle = document.querySelector('.hsplit');
      const before = handle ? getComputedStyle(handle).display : null;
      const toggle = Array.from(document.querySelectorAll('.filterrow .btn')).find((button) => (button.title ?? '').startsWith('filters'));
      toggle?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      const box = handle.getBoundingClientRect();
      const opts = Array.from(document.querySelectorAll('.facets .opt'));
      const covered = opts.filter((opt) => {
        const rect = opt.getBoundingClientRect();
        return Math.min(rect.right, box.right) - Math.max(rect.left, box.left) > 0.5 && Math.min(rect.bottom, box.bottom) - Math.max(rect.top, box.top) > 0.5;
      }).length;
      return { before, display: getComputedStyle(handle).display, covered, opts: opts.length, open: document.querySelector('#app')?.classList.contains('filters-open') ?? false };
    });
    assert(result.before === 'block', `${label}: the phone layout has no Now Playing handle to get out of the way: ${JSON.stringify(result)}`);
    assert(result.open && result.opts > 0, `${label}: the filter panel did not open: ${JSON.stringify(result)}`);
    assert(result.display === 'none' && result.covered === 0, `${label}: the Now Playing handle is drawn over the filter panel: ${JSON.stringify(result)}`);
    await page.close();
  }
}

await filterPanelClearsTheResizeHandle();

/**
 * The listened LED of the row you are hearing, in the state the app really produces: a variant
 * played until its row is the selection, audible and listened at once. The stylesheet is pinned
 * by the probes in measure(); this checks that the class combination they paint is the one the
 * app reaches, and that the LED is green (win95 and modern) rather than turning white.
 */
async function listenedLedOfThePlayingRow() {
  for (const [theme, expected] of [['win95', 'rgb(0, 255, 0)'], ['modern', 'rgb(0, 255, 65)'], ['amiga', 'rgb(0, 0, 0)']]) {
    const label = `desktop/${theme}`;
    const page = await browser.newPage({ viewport: viewports.desktop });
    page.on('pageerror', (error) => errors.push(`${label}: ${String(error)}`));
    const target = new URL(url);
    target.searchParams.set('theme', theme);
    await page.goto(target.href, { waitUntil: 'networkidle' });
    await page.waitForSelector('.rows .row', { timeout: 20000 });
    await page.click('.rows .row:nth-child(3)');
    try {
      await page.waitForFunction(() => !!document.querySelector('.rows .row.sel.audible.cached'), undefined, { timeout: 60000 });
    } catch (error) {
      failures.push(`${label}: no variant played long enough to light its listened LED: ${String(error).split('\n')[0]}`);
      await page.close();
      continue;
    }
    const led = await page.evaluate(() => {
      const row = document.querySelector('.rows .row.sel.audible.cached');
      return { classes: row.className, dot: getComputedStyle(row.querySelector('.cell.dot')).backgroundColor };
    });
    assert(led.dot === expected, `${label}: the listened LED of the playing selection is ${led.dot}, not ${expected} (${led.classes})`);
    await page.close();
  }
}

await listenedLedOfThePlayingRow();

/**
 * Space on the Modern font title cycles the font and must not also reach the window keymap,
 * where Space is play/pause: the handler stops propagation. Checked here for the same reason as
 * the filter panel above — App is only exercisable in a real document.
 */
async function titleSpaceStaysOnTheTitle() {
  const page = await browser.newPage({ viewport: viewports.desktop });
  page.on('pageerror', (error) => errors.push(`desktop/modern: ${String(error)}`));
  const target = new URL(url);
  target.searchParams.set('theme', 'modern');
  await page.goto(target.href, { waitUntil: 'networkidle' });
  await page.waitForSelector('.rows .row', { timeout: 20000 });
  const result = await page.evaluate(() => {
    const title = document.querySelector('.top .title');
    let reachedWindow = 0;
    const spy = () => reachedWindow++;
    window.addEventListener('keydown', spy); // stands in for installKeyboard(window, ...)
    const before = document.documentElement.dataset.modernFont ?? null;
    title?.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true }));
    window.removeEventListener('keydown', spy);
    return { reachedWindow, before, after: document.documentElement.dataset.modernFont ?? null };
  });
  assert(result.reachedWindow === 0, `desktop/modern: Space on the title also reached the window keymap (play/pause): ${JSON.stringify(result)}`);
  assert(result.after !== result.before, `desktop/modern: Space on the title did not cycle the font: ${JSON.stringify(result)}`);
  await page.close();
}

await titleSpaceStaysOnTheTitle();

/**
 * Sorting from the header row: every other column cycles ascending → descending → catalog order,
 * while '#' *is* the catalog order and restores it in one click, then stops offering anything.
 * nextSort() is unit-tested; the wiring (App.toggleSort), the rebuilt header and the focus it
 * must keep only exist in a real document, so they are checked here like the filter panel above.
 */
async function headerSorting() {
  const label = 'desktop/modern';
  const page = await browser.newPage({ viewport: viewports.desktop });
  page.on('pageerror', (error) => errors.push(`${label}: ${String(error)}`));
  const target = new URL(url);
  target.searchParams.set('theme', 'modern');
  await page.goto(target.href, { waitUntil: 'networkidle' });
  await page.waitForSelector('.rows .row', { timeout: 20000 });
  const result = await page.evaluate(() => {
    const header = (key) => document.querySelector(`.row.head .hcell.col-${key}`);
    const ids = () => Array.from(document.querySelectorAll('.rows .row')).map((row) => row.dataset.id).join(' ');
    const state = (key) => ({ sort: header(key).getAttribute('aria-sort'), disabled: header(key).getAttribute('aria-disabled'), title: header(key).getAttribute('title') });
    const click = (key) => header(key).dispatchEvent(new MouseEvent('click', { bubbles: true }));
    const catalog = ids();
    const idle = state('idx');
    click('engine');
    const ascending = { order: ids(), engine: state('engine'), idx: state('idx') };
    click('engine');
    const descending = { order: ids(), engine: state('engine') };
    header('engine').focus();
    click('engine');
    const cycled = { order: ids(), engine: state('engine'), focus: document.activeElement?.dataset.col ?? null };
    click('engine');
    click('engine');
    click('idx'); // one click, from a descending sort straight back to the catalog order
    const restored = { order: ids(), engine: state('engine'), idx: state('idx') };
    // and again in the catalog order: nothing to do, so nothing may happen — re-applying the
    // order would rebuild the head, scroll the cursor back into view and jump the prefetcher
    const list = document.querySelector('.list');
    list.scrollTop = 300;
    header('idx').dataset.geometryProbe = 'before'; // survives only if the head is not rebuilt
    click('idx');
    const spent = { order: ids(), scrollTop: list.scrollTop, scrollable: list.scrollHeight - list.clientHeight, rebuilt: !header('idx').dataset.geometryProbe };
    return { catalog, idle, ascending, descending, cycled, restored, spent };
  });
  assert(result.ascending.engine.sort === 'ascending' && result.descending.engine.sort === 'descending' && result.cycled.engine.sort === 'none', `${label}: the engine header does not cycle ascending, descending, catalog order: ${JSON.stringify([result.ascending.engine, result.descending.engine, result.cycled.engine])}`);
  assert(result.cycled.order === result.catalog, `${label}: the third engine click did not restore the catalog order`);
  assert(result.cycled.focus === 'engine', `${label}: sorting moved the focus off the header that was activated: ${result.cycled.focus}`);
  // a catalogue whose engine order happens to be the catalog order cannot exercise the restore
  if (result.descending.order === result.catalog) process.stderr.write(`${label}: the served catalogue sorts by engine into its own order — skipping the '#' restore check\n`);
  else assert(result.restored.order === result.catalog && result.restored.engine.sort === 'none', `${label}: one '#' click did not restore the catalog order: ${JSON.stringify(result.restored.engine)}`);
  assert(result.idle.disabled === 'true' && !result.idle.title.includes('click'), `${label}: '#' offers an order the list is already in: ${JSON.stringify(result.idle)}`);
  assert(result.ascending.idx.disabled === null && result.ascending.idx.title.endsWith('click for the catalog order'), `${label}: '#' does not offer the catalog order while a sort is active: ${JSON.stringify(result.ascending.idx)}`);
  assert(result.restored.idx.disabled === 'true', `${label}: '#' still claims to be actionable after restoring the catalog order: ${JSON.stringify(result.restored.idx)}`);
  assert(!result.spent.rebuilt && result.spent.order === result.catalog && (result.spent.scrollable < 300 || result.spent.scrollTop === 300), `${label}: clicking '#' in the catalog order re-applied the order anyway: ${JSON.stringify({ ...result.spent, order: result.spent.order === result.catalog })}`);
  await page.close();
}

await headerSorting();

/**
 * The Now Playing tier pill: its tooltip is written once, in the NowPlaying constructor, and its
 * label by setTier() as the engine promotes the audio. tierLabel()/tierTitle() are unit-tested;
 * the strings only reach a user through a real document, so a real playback is checked here.
 */
async function nowPlayingTierPill() {
  const label = 'desktop/modern';
  const page = await browser.newPage({ viewport: viewports.desktop });
  page.on('pageerror', (error) => errors.push(`${label}: ${String(error)}`));
  const target = new URL(url);
  target.searchParams.set('theme', 'modern');
  await page.goto(target.href, { waitUntil: 'networkidle' });
  await page.waitForSelector('.rows .row', { timeout: 20000 });
  const tooltip = await page.evaluate(() => document.querySelector('.tier')?.getAttribute('title') ?? null);
  assert(/^audio tier: scrubbing \(\d+ kbps\) or listening \(\d+ kbps\)$/.test(tooltip ?? ''), `desktop/modern: the tier tooltip does not name the tiers as the pill does: ${tooltip}`);
  const pill = { first: null, listening: null };
  await page.click('.rows .row:nth-child(3)');
  try {
    await page.waitForFunction(() => (document.querySelector('.tier')?.textContent ?? '').length > 0, undefined, { timeout: 30000 });
    pill.first = await page.evaluate(() => document.querySelector('.tier').textContent);
    await page.waitForFunction(() => (document.querySelector('.tier')?.textContent ?? '').startsWith('listening'), undefined, { timeout: 30000 });
    pill.listening = await page.evaluate(() => document.querySelector('.tier').textContent);
  } catch (error) {
    failures.push(`${label}: the tier pill never named the tier being played: ${JSON.stringify(pill)} (${String(error).split('\n')[0]})`);
  }
  if (pill.first) assert(/^(scrubbing|listening) · \d+k$/.test(pill.first), `${label}: the tier pill reads ${pill.first}`);
  if (pill.listening) assert(/^listening · \d+k$/.test(pill.listening), `${label}: the listening pill reads ${pill.listening}`);
  await page.close();
}

await nowPlayingTierPill();


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
    assert(snapshot.settingsFontReset.guidance === 'Click or tap the title bar to cycle font selection (modern theme only)', `${label}/${snapshot.theme}: font guidance is missing: ${JSON.stringify(snapshot.settingsFontReset)}`);
    assert(snapshot.settingsFontReset.equalButtons && snapshot.settingsFontReset.resetBelow && snapshot.settingsFontReset.fitsViewport, `${label}/${snapshot.theme}: font reset row geometry is wrong: ${JSON.stringify(snapshot.settingsFontReset)}`);
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
    if (color !== null && part !== 'background' && !part.endsWith('Dot')) assert(color === 'rgb(255, 255, 255)', `${label}/win95: hovered ${part} is ${color}`);
  }
  // The LED of the row you are hearing stays green while it is the selection or under the
  // pointer, as it does in modern; only the muted listened LED turns white to read on the navy.
  assert(win95.listenedLed?.playing === 'rgb(0, 128, 0)', `${label}/win95: the playing LED is ${win95.listenedLed?.playing}`);
  assert(win95.listenedLed?.selectedPlaying === 'rgb(0, 255, 0)' && win95.hover.playingDot === 'rgb(0, 255, 0)', `${label}/win95: the highlighted playing LED is ${JSON.stringify([win95.listenedLed?.selectedPlaying, win95.hover.playingDot])}, not the bright green that reads on the navy`);
  assert(win95.listenedLed?.selectedBuffering === 'rgb(0, 255, 0)', `${label}/win95: the buffering LED of the playing selection is ${win95.listenedLed?.selectedBuffering} — it blinks, it does not change colour`);
  assert(win95.listenedLed?.selectedListened === 'rgb(255, 255, 255)' && win95.hover.listenedDot === 'rgb(255, 255, 255)', `${label}/win95: the highlighted listened LED is ${JSON.stringify([win95.listenedLed?.selectedListened, win95.hover.listenedDot])}, not white`);
  assert(modern.listenedLed?.playing === 'rgb(0, 255, 65)' && modern.listenedLed.selectedPlaying === modern.listenedLed.playing, `${label}/modern: the playing LED does not keep the accent through the selection: ${JSON.stringify(modern.listenedLed)}`);
  assert(amiga.listenedLed?.selectedPlaying === 'rgb(0, 0, 0)', `${label}/amiga: the highlighted playing LED is ${amiga.listenedLed?.selectedPlaying}, not the theme's black`);
  assert(win95.states.disabledColor === 'rgb(128, 128, 128)', `${label}/win95: disabled button text is ${win95.states.disabledColor}`);
  assert(win95.states.linkColor === 'rgb(0, 0, 255)', `${label}/win95: link color is ${win95.states.linkColor}`);
  assert(win95.focusOutline === 'dotted', `${label}/win95: keyboard focus outline is ${win95.focusOutline}, not dotted`);
  const expectedFonts = [
    ['ibm-plex-sans', 'SFP IBM Plex Sans'], ['inter', 'SFP Inter'], ['space-grotesk', 'SFP Space Grotesk'],
    ['manrope', 'SFP Manrope'], ['outfit', 'SFP Outfit'], ['urbanist', 'SFP Urbanist'], ['sora', 'SFP Sora'],
    ['exo-2', 'SFP Exo 2'], ['titillium-web', 'SFP Titillium Web'], ['chakra-petch', 'SFP Chakra Petch'],
    ['rajdhani', 'SFP Rajdhani'], ['oxanium', 'SFP Oxanium'], ['orbitron', 'SFP Orbitron'], ['victor-mono', 'SFP Victor Mono'],
  ];
  assert(modern.fontCycle?.states.length === expectedFonts.length, `${label}/modern: font cycle length is wrong: ${JSON.stringify(modern.fontCycle)}`);
  modern.fontCycle?.states.forEach((state, index) => {
    const [id, family] = expectedFonts[index];
    assert(state.id === id && state.family.includes(family), `${label}/modern: font ${index + 1} is wrong: ${JSON.stringify(state)}`);
    assert(state.loaded, `${label}/modern: ${id} did not load`);
    assert(state.overflow <= 0.5 && !state.headerOverlap && state.rowHeights.length === 1, `${label}/modern: ${id} breaks layout: ${JSON.stringify(state)}`);
    assert(close(state.filterHeight, modern.fontCycle.states[0].filterHeight), `${label}/modern: ${id} changes the filter bar height: ${JSON.stringify(state)}`);
    const expectedStored = index === 0 ? null : id;
    assert(state.stored === expectedStored, `${label}/modern: ${id} was not persisted correctly: ${JSON.stringify(state)}`);
  });
  assert(modern.fontCycle?.wrapped.id === 'ibm-plex-sans' && modern.fontCycle.wrapped.stored === 'ibm-plex-sans', `${label}/modern: font cycle did not wrap: ${JSON.stringify(modern.fontCycle)}`);
  assert(modern.fontCycle?.role === 'button' && modern.fontCycle.tabIndex === 0, `${label}/modern: font title is not keyboard-accessible: ${JSON.stringify(modern.fontCycle)}`);
  assert(modern.settingsFontReset.font.id === 'ibm-plex-sans' && modern.settingsFontReset.font.stored === null, `${label}/modern: reset font did not restore the unstored default: ${JSON.stringify(modern.settingsFontReset)}`);
  for (const themed of [win95, amiga]) {
    assert(themed.fontCycle.after.id === themed.fontCycle.before.id && themed.fontCycle.after.stored === null, `${label}/${themed.theme}: title click changed the Modern font preference: ${JSON.stringify(themed.fontCycle)}`);
    assert(themed.fontCycle.role === null && themed.fontCycle.tabIndex === -1, `${label}/${themed.theme}: title incorrectly exposes the Modern font control: ${JSON.stringify(themed.fontCycle)}`);
  }
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
