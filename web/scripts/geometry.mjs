#!/usr/bin/env node
/**
 * Compare modern, Windows 95, and Amiga geometry — and the behaviour that only a real browser
 * shows — at desktop and phone sizes.
 *
 * This is a MANUAL check, not part of CI: it needs a published catalog with real audio (`out/public`
 * on http://127.0.0.1:8000) and a dev server, neither of which a PR runner has. Run it by hand:
 *
 *   python3 -m http.server 8000 --directory out/public   # in one shell
 *   cd web && npm run dev                                # in another
 *   cd web && npm run test:geometry [-- --url=http://localhost:5173/]
 *
 * Anything that can be pinned without a browser belongs in `web/test/unit` instead, where CI runs it.
 */
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
    const trackOptions = Array.from(document.querySelectorAll('.settings .trackopts input[type="checkbox"]'));
    // #41: how far the ● would sit below the centre of the parentheses if nothing lifted it, and
    // how far it is actually lifted. Measured from the ink of the faces the page really resolved
    // (● comes from a fallback in every theme), so this checks the alignment, not the stylesheet.
    const glyph = document.querySelector('.settings .listened-glyph');
    const glyphLabel = glyph?.closest('label');
    const shorthand = (element) => {
      const s = getComputedStyle(element);
      return `${s.fontStyle} ${s.fontWeight} ${s.fontSize} ${s.fontFamily}`;
    };
    const pen = document.createElement('canvas').getContext('2d');
    const inkCentre = (text, font) => {
      pen.font = font;
      const m = pen.measureText(text);
      return (m.actualBoundingBoxAscent - m.actualBoundingBoxDescent) / 2;
    };
    const glyphStyle = glyph ? getComputedStyle(glyph) : null;
    const listenedGlyph = glyph && glyphLabel && glyphStyle
      ? {
        position: glyphStyle.position,
        top: glyphStyle.top,
        // a lift is a negative `top`; 'auto' (no rule) is no lift at all
        lift: glyphStyle.top === 'auto' ? 0 : -parseFloat(glyphStyle.top),
        deficit: inkCentre('()', shorthand(glyphLabel)) - inkCentre('●', shorthand(glyph)),
      }
      : null;
    // "close" / "clear all site data" centred between the reset-font row and the window edge.
    // Measured from the row the button sits in, not the button: its guidance text wraps to two
    // lines on a phone, and what the issue asks to centre is the room below the last section.
    const closing = Array.from(document.querySelectorAll('.settings .footrow .btn')).map((button) => button.getBoundingClientRect());
    const fontfootBox = document.querySelector('.settings .fontfoot')?.getBoundingClientRect();
    const footrow = closing.length && fontfootBox && box
      ? {
        above: Math.min(...closing.map((b) => b.top)) - fontfootBox.bottom,
        below: box.bottom - Math.max(...closing.map((b) => b.bottom)),
      }
      : null;
    reset?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    return {
      guidance,
      equalButtons: !!defaultsBox && !!resetBox && Math.abs(defaultsBox.width - resetBox.width) <= 0.5 && Math.abs(defaultsBox.height - resetBox.height) <= 0.5,
      resetBelow: !!defaultsBox && !!resetBox && resetBox.top >= defaultsBox.bottom,
      fitsViewport: !!box && box.top >= 0 && box.bottom <= innerHeight,
      footrow,
      // #28: both track options are in Settings at every window size, whatever the Tracks caption does
      trackOptions: trackOptions.map((input) => input.closest('label')?.textContent?.trim() ?? ''),
      trackOptionsVisible: trackOptions.every((input) => input.getBoundingClientRect().width > 0),
      listenedGlyph,
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
 * The Tracks caption (#28) drops both toggles when the pane cannot hold them and shows them when
 * it can. Two things have to hold and neither is visible to a unit test: the caption is narrowed
 * by the pane rather than the pane widened by the caption (so the overflow it measures can happen
 * at all), and the decision is re-taken when the *text* changes at a fixed box width — a theme
 * switch swaps Topaz for the Modern face without resizing anything.
 */
async function trackCaptionFollowsThePane() {
  const page = await browser.newPage({ viewport: viewports.desktop });
  page.on('pageerror', (error) => errors.push(`desktop/tracks: ${String(error)}`));
  const target = new URL(url);
  target.searchParams.set('theme', 'modern');
  await page.goto(target.href, { waitUntil: 'networkidle' });
  await page.waitForSelector('.rows .row', { timeout: 20000 });
  await page.evaluate(() => document.fonts.ready);
  /** the two content widths (full and short labels) whatever classes the caption currently carries */
  const state = async () => {
    await page.waitForTimeout(80); // one ResizeObserver delivery
    return page.evaluate(() => {
      const head = document.querySelector('.tracks .np-head');
      const right = document.querySelector('.main > .right');
      const app = document.querySelector('#app');
      const had = { short: head.classList.contains('short'), cramped: head.classList.contains('cramped') };
      head.classList.remove('short', 'cramped');
      const needed = head.scrollWidth;
      head.classList.add('short');
      const neededShort = head.scrollWidth;
      head.classList.remove('short');
      if (had.short) head.classList.add('short');
      if (had.cramped) head.classList.add('cramped');
      const toggles = Array.from(document.querySelectorAll('.tracks .track-toggles input'));
      const shown = (selector) => Array.from(document.querySelectorAll(`.tracks .track-toggles ${selector}`)).some((span) => span.getBoundingClientRect().width > 0);
      return {
        theme: document.documentElement.dataset.theme,
        client: head.clientWidth,
        needed,
        neededShort,
        short: had.short,
        cramped: had.cramped,
        pastPane: head.getBoundingClientRect().right - right.getBoundingClientRect().right,
        appOverflow: app.scrollWidth - app.clientWidth,
        toggles: toggles.length,
        togglesVisible: toggles.length === 2 && toggles.every((box) => box.getBoundingClientRect().width > 0),
        fullLabels: shown('.lbl-full'),
        shortLabels: shown('.lbl-short'),
        tracksVisible: getComputedStyle(document.querySelector('.tracks')).display !== 'none',
      };
    });
  };
  const setPane = async (width) => {
    await page.evaluate((w) => {
      if (w === null) document.querySelector('.main').style.removeProperty('--right-w');
      else document.querySelector('.main').style.setProperty('--right-w', `${w}px`);
    }, width);
    return state();
  };
  /** the caption must be wearing the first of the three steps that actually fits its pane */
  const consistent = (s) => {
    if (s.needed <= s.client) return !s.short && !s.cramped;
    if (s.neededShort <= s.client) return s.short && !s.cramped;
    return s.cramped;
  };
  const opened = await state();
  assert(opened.pastPane <= 0.5 && opened.appOverflow <= 0.5, `desktop/modern: the Tracks caption runs ${opened.pastPane}px past its pane: ${JSON.stringify(opened)}`);
  assert(consistent(opened), `desktop/modern: the Tracks caption is stale at the default split: ${JSON.stringify(opened)}`);

  // a caption wider than any sensible pane: both toggles go, and nothing spills out of the pane
  const narrow = await setPane(300);
  assert(narrow.cramped && !narrow.togglesVisible && narrow.pastPane <= 0.5, `desktop/modern: a 300px Tracks pane does not drop the caption toggles: ${JSON.stringify(narrow)}`);
  // room to spare: both come back, spelled out in full
  const wide = await setPane(900);
  assert(!wide.cramped && wide.togglesVisible && wide.toggles === 2, `desktop/modern: a 900px Tracks pane does not show the caption toggles: ${JSON.stringify(wide)}`);
  assert(wide.fullLabels && !wide.shortLabels, `desktop/modern: a 900px Tracks pane does not spell the options out: ${JSON.stringify(wide)}`);

  // park the pane just wide enough for the Modern caption, then change only the text
  const content = await setPane(200);
  const fitted = await setPane(content.needed + 36);
  assert(!fitted.cramped, `desktop/modern: the caption did not fit a pane sized to its own content: ${JSON.stringify({ content, fitted })}`);
  await page.evaluate(() => {
    const picker = document.querySelector('.themepick');
    picker.value = 'amiga';
    picker.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await page.evaluate(() => document.fonts.ready);
  const swapped = await state();
  assert(swapped.theme === 'amiga' && swapped.needed > swapped.client, `desktop/amiga: the Topaz caption unexpectedly fits the Modern fit width — this check no longer proves anything: ${JSON.stringify(swapped)}`);
  assert(consistent(swapped) && !swapped.fullLabels && swapped.pastPane <= 0.5, `desktop/amiga: switching theme did not re-take the caption decision (full wording left showing, ${swapped.pastPane}px past the pane): ${JSON.stringify(swapped)}`);
  await page.evaluate(() => {
    const picker = document.querySelector('.themepick');
    picker.value = 'modern';
    picker.dispatchEvent(new Event('change', { bubbles: true }));
  });
  const restored = await state();
  assert(!restored.cramped && restored.togglesVisible && restored.fullLabels, `desktop/modern: switching back left the caption in its narrow wording where the full one fits: ${JSON.stringify(restored)}`);
  await setPane(null);
  await page.close();

  // The reference desktop viewport at the default split: the short wording is what keeps both
  // options on screen there (the full one needs ~490px of caption in Modern, ~585 in Topaz, and
  // the pane gives about 410), so an all-or-nothing rule would hide them for most desktop users.
  for (const theme of ['modern', 'win95', 'amiga']) {
    const desk = await browser.newPage({ viewport: viewports.desktop });
    desk.on('pageerror', (error) => errors.push(`desktop/${theme}: ${String(error)}`));
    const deskTarget = new URL(url);
    deskTarget.searchParams.set('theme', theme);
    await desk.goto(deskTarget.href, { waitUntil: 'networkidle' });
    await desk.waitForSelector('.rows .row', { timeout: 20000 });
    await desk.evaluate(() => document.fonts.ready);
    await desk.waitForTimeout(120);
    const split = await desk.evaluate(() => {
      const head = document.querySelector('.tracks .np-head');
      const right = document.querySelector('.main > .right');
      return {
        cramped: head.classList.contains('cramped'),
        short: head.classList.contains('short'),
        togglesVisible: Array.from(document.querySelectorAll('.tracks .track-toggles input')).every((box) => box.getBoundingClientRect().width > 0),
        pastPane: head.getBoundingClientRect().right - right.getBoundingClientRect().right,
        appOverflow: document.querySelector('#app').scrollWidth - document.querySelector('#app').clientWidth,
      };
    });
    assert(split.togglesVisible && !split.cramped, `desktop/${theme}: the Tracks caption hides both options at the default split: ${JSON.stringify(split)}`);
    assert(split.pastPane <= 0.5 && split.appOverflow <= 0.5, `desktop/${theme}: the Tracks caption runs ${split.pastPane}px past its pane: ${JSON.stringify(split)}`);
    await desk.close();
  }

  // the case #28 names: iOS mobile-landscape is above the 720px breakpoint, so Tracks is on show
  for (const theme of ['modern', 'win95', 'amiga']) {
    const landscape = await browser.newPage({ viewport: { width: 844, height: 390 }, hasTouch: true });
    landscape.on('pageerror', (error) => errors.push(`landscape/${theme}: ${String(error)}`));
    const phoneTarget = new URL(url);
    phoneTarget.searchParams.set('theme', theme);
    await landscape.goto(phoneTarget.href, { waitUntil: 'networkidle' });
    await landscape.waitForSelector('.rows .row', { timeout: 20000 });
    await landscape.evaluate(() => document.fonts.ready);
    await landscape.waitForTimeout(120);
    const shown = await landscape.evaluate(() => {
      const head = document.querySelector('.tracks .np-head');
      const right = document.querySelector('.main > .right');
      return {
        tracksVisible: getComputedStyle(document.querySelector('.tracks')).display !== 'none',
        cramped: head.classList.contains('cramped'),
        togglesVisible: Array.from(document.querySelectorAll('.tracks .track-toggles input')).some((box) => box.getBoundingClientRect().width > 0),
        pastPane: head.getBoundingClientRect().right - right.getBoundingClientRect().right,
        appOverflow: document.querySelector('#app').scrollWidth - document.querySelector('#app').clientWidth,
      };
    });
    assert(shown.tracksVisible && shown.cramped && !shown.togglesVisible, `landscape/${theme}: the Tracks caption keeps its toggles at 844x390: ${JSON.stringify(shown)}`);
    assert(shown.pastPane <= 0.5 && shown.appOverflow <= 0.5, `landscape/${theme}: the Tracks caption runs ${shown.pastPane}px past its pane: ${JSON.stringify(shown)}`);
    await landscape.close();
  }
}

await trackCaptionFollowsThePane();

/**
 * Topaz is `font-display: swap`: on a cold load the caption is first measured in the fallback
 * face, which is ~90px wider than Topaz at the same size. The head's box does not change when
 * the real face arrives, so no ResizeObserver notification is delivered and the first decision
 * would stand forever — the toggles staying hidden on a pane that now has room for them.
 */
async function trackCaptionSurvivesALateWebfont() {
  const page = await browser.newPage({ viewport: viewports.desktop });
  page.on('pageerror', (error) => errors.push(`desktop/amiga: ${String(error)}`));
  await page.route('**/Topaz_a500*', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 2500));
    await route.continue();
  });
  const target = new URL(url);
  target.searchParams.set('theme', 'amiga');
  await page.goto(target.href, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.rows .row', { timeout: 20000 });
  const caption = () => page.evaluate(() => {
    const head = document.querySelector('.tracks .np-head');
    const had = { short: head.classList.contains('short'), cramped: head.classList.contains('cramped') };
    head.classList.remove('short', 'cramped');
    const needed = head.scrollWidth;
    if (had.short) head.classList.add('short');
    if (had.cramped) head.classList.add('cramped');
    return {
      ...had,
      needed,
      client: head.clientWidth,
      topaz: getComputedStyle(head).fontFamily,
      fullLabels: Array.from(document.querySelectorAll('.tracks .track-toggles .lbl-full')).some((span) => span.getBoundingClientRect().width > 0),
      togglesVisible: Array.from(document.querySelectorAll('.tracks .track-toggles input')).every((box) => box.getBoundingClientRect().width > 0),
    };
  });
  const setPane = async (width) => {
    await page.evaluate((w) => document.querySelector('.main').style.setProperty('--right-w', `${w}px`), width);
    await page.waitForTimeout(80);
  };
  // a pane 20px narrower than the fallback face needs to spell both options out, which Topaz —
  // some 90px narrower at the same size — will comfortably fit into
  await setPane(200);
  const fallback = await caption();
  await setPane(fallback.needed + 4);
  const early = await caption();
  assert(early.short || early.cramped, `desktop/amiga: the fallback-face caption was not measured as too wide for its pane: ${JSON.stringify({ fallback, early })}`);
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(150);
  const late = await caption();
  // scrollWidth clamps to the box once the content fits, so "fits" reads as needed === client
  assert(late.needed <= late.client && early.needed > early.client, `desktop/amiga: Topaz did not narrow the caption below the pane — this check no longer proves anything: ${JSON.stringify({ early, late })}`);
  assert(!late.cramped && !late.short && late.fullLabels && late.togglesVisible, `desktop/amiga: the caption kept a decision taken in the fallback face after Topaz arrived: ${JSON.stringify({ early, late })}`);
  await page.close();
}

await trackCaptionSurvivesALateWebfont();

/**
 * Every song switch builds a new TrackList, so the old one's caption observer has to go with it:
 * otherwise a long listening session accumulates one live ResizeObserver per switch, each holding
 * a detached caption element. Counted from a wrapper installed before the app boots.
 */
async function trackListObserversAreReleased() {
  const page = await browser.newPage({ viewport: viewports.desktop });
  page.on('pageerror', (error) => errors.push(`desktop/observers: ${String(error)}`));
  await page.addInitScript(() => {
    const Real = window.ResizeObserver;
    window.__observerCounts = { made: 0, disconnected: 0 };
    window.ResizeObserver = class extends Real {
      constructor(callback) {
        super(callback);
        window.__observerCounts.made += 1;
      }

      disconnect() {
        window.__observerCounts.disconnected += 1;
        return super.disconnect();
      }
    };
  });
  const target = new URL(url);
  target.searchParams.set('theme', 'modern');
  await page.goto(target.href, { waitUntil: 'networkidle' });
  await page.waitForSelector('.rows .row', { timeout: 20000 });
  const songs = await page.evaluate(() => Array.from(document.querySelector('.songpicker')?.options ?? []).map((option) => option.value));
  if (songs.length < 2) {
    process.stderr.write('desktop/modern: only one song served — skipping the TrackList observer-release check\n');
    await page.close();
    return;
  }
  for (const song of [songs[1], songs[0], songs[1]]) {
    await page.evaluate((id) => {
      const picker = document.querySelector('.songpicker');
      picker.value = id;
      picker.dispatchEvent(new Event('change', { bubbles: true }));
    }, song);
    await page.waitForTimeout(400);
  }
  const counts = await page.evaluate(() => window.__observerCounts);
  assert(counts.made >= 4, `desktop/modern: the song switches did not rebuild the Tracks pane: ${JSON.stringify(counts)}`);
  // one observer stays live: the caption currently on screen
  assert(counts.disconnected >= counts.made - 1, `desktop/modern: ${counts.made - counts.disconnected} caption observers are still attached after ${counts.made} TrackLists: ${JSON.stringify(counts)}`);
  await page.close();
}

await trackListObserversAreReleased();

/**
 * #35: turning "preserve track position" off resets the positions a reload would restore — the
 * saved map and the URL's `t=` — and leaves what is currently audible where it is.
 */
async function unpreservingClearsTheSavedPositionOnly() {
  const page = await browser.newPage({ viewport: viewports.desktop });
  page.on('pageerror', (error) => errors.push(`desktop/preserve: ${String(error)}`));
  const target = new URL(url);
  target.searchParams.set('theme', 'modern');
  target.searchParams.set('t', '30');
  await page.goto(target.href, { waitUntil: 'networkidle' });
  await page.waitForSelector('.rows .row', { timeout: 20000 });
  try {
    await page.waitForFunction(() => new URLSearchParams(location.search).get('t') === '30', undefined, { timeout: 20000 });
  } catch (error) {
    failures.push(`desktop/preserve: the URL never carried the boot position: ${String(error)}`);
    await page.close();
    return;
  }
  const before = await page.evaluate(() => ({ t: new URLSearchParams(location.search).get('t'), seek: Number(document.querySelector('.transport .seek').value) }));
  const clicked = await page.evaluate(() => {
    document.querySelector('.top [aria-label="settings"]')?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    const box = Array.from(document.querySelectorAll('.settings .trackopts input[type="checkbox"]'))
      .find((input) => (input.closest('label')?.textContent ?? '').includes('Preserve track position'));
    box?.click();
    return { found: !!box, checked: box?.checked ?? null };
  });
  // the transport clock follows the engine on the next frame, and the URL settles a beat later
  await page.waitForTimeout(400);
  const after = {
    ...clicked,
    ...await page.evaluate(() => ({ t: new URLSearchParams(location.search).get('t'), seek: Number(document.querySelector('.transport .seek').value) })),
  };
  assert(close(before.seek, 30, 1), `desktop/preserve: the boot position did not reach the transport: ${JSON.stringify(before)}`);
  assert(after.found && after.checked === false, `desktop/preserve: the Settings checkbox did not go off: ${JSON.stringify(after)}`);
  assert(after.t === null, `desktop/preserve: the URL still carries a position a reload would restore: ${JSON.stringify(after)}`);
  assert(close(after.seek, before.seek, 1), `desktop/preserve: unchecking the option moved the audible playhead from ${before.seek} to ${after.seek}`);
  // the symmetric case: switching it back on has to make a reload resume where the listener is,
  // which means writing the position the URL stopped carrying while the option was off
  await page.evaluate(() => {
    Array.from(document.querySelectorAll('.settings .trackopts input[type="checkbox"]'))
      .find((input) => (input.closest('label')?.textContent ?? '').includes('Preserve track position'))
      ?.click();
  });
  await page.waitForTimeout(400);
  const again = await page.evaluate(() => ({ t: new URLSearchParams(location.search).get('t'), seek: Number(document.querySelector('.transport .seek').value) }));
  assert(again.t !== null && close(Number(again.t), again.seek, 2), `desktop/preserve: re-checking the option left the URL without the position the transport shows: ${JSON.stringify(again)}`);
  await page.close();

  // a shared or bookmarked `t=` is a saved position too: with the option off it must not restore
  const off = await browser.newPage({ viewport: viewports.desktop });
  off.on('pageerror', (error) => errors.push(`desktop/preserve-off: ${String(error)}`));
  await off.addInitScript(() => localStorage.setItem('sfp.prefs.v1', JSON.stringify({ preserveTrackPosition: false })));
  await off.goto(target.href, { waitUntil: 'networkidle' });
  await off.waitForSelector('.rows .row', { timeout: 20000 });
  await off.waitForTimeout(400);
  const booted = await off.evaluate(() => ({
    seek: Number(document.querySelector('.transport .seek').value),
    t: new URLSearchParams(location.search).get('t'),
    checked: Array.from(document.querySelectorAll('.tracks .track-toggles input[type="checkbox"]')).map((input) => input.checked),
  }));
  assert(close(booted.seek, 0, 1) && booted.t === null, `desktop/preserve-off: a bookmarked t=30 resumed although positions are not preserved: ${JSON.stringify(booted)}`);
  await off.close();
}

await unpreservingClearsTheSavedPositionOnly();

/**
 * #28 end to end: a track that runs out steps to the next one and keeps playing — and does it
 * without taking the keyboard away. The step rebuilds every control, so focus falls to <body>
 * unless it is put back, and from <body> the keymap reads a soundfont name typed into the search
 * box as a string of shortcuts (t theme, f favorites, l loop…). With the option off the same
 * track simply ends, leaving no `t=` behind for a reload to resume two seconds from the end.
 */
async function steppingKeepsPlayingAndKeepsTheKeyboard() {
  // the step has to happen on its own, on a clock we do not drive: let audio start without a gesture
  const stepper = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
  const target = new URL(url);
  target.searchParams.set('theme', 'modern');
  const nearTheEnd = (page, seconds) => page.evaluate((left) => {
    const seek = document.querySelector('.transport .seek');
    seek.value = String(Math.max(0, Number(seek.max) - left));
    seek.dispatchEvent(new Event('input', { bubbles: true }));
    seek.dispatchEvent(new Event('change', { bubbles: true }));
  }, seconds);

  const page = await stepper.newPage({ viewport: viewports.desktop });
  page.on('pageerror', (error) => errors.push(`desktop/stepping: ${String(error)}`));
  await page.goto(target.href, { waitUntil: 'networkidle' });
  await page.waitForSelector('.rows .row', { timeout: 20000 });
  const first = await page.$eval('.songpicker', (select) => select.value);
  await page.click('.transport .btn.play');
  await nearTheEnd(page, 3);
  // a user typing a soundfont name straight through the end of the track
  await page.click('.filterbar .search');
  await page.keyboard.type('flu');
  try {
    await page.waitForFunction((id) => document.querySelector('.songpicker').value !== id, first, { timeout: 25000 });
  } catch (error) {
    failures.push(`desktop/stepping: the track never stepped to the next one: ${String(error)}`);
    await page.close();
    await stepper.close();
    return;
  }
  await page.keyboard.type('te');
  const stepped = await page.evaluate(() => ({
    song: document.querySelector('.songpicker').value,
    query: document.querySelector('.filterbar .search').value,
    activeIsSearch: document.activeElement === document.querySelector('.filterbar .search'),
    theme: document.documentElement.dataset.theme,
    loop: document.querySelector('.transport .btn.toggle[title="loop (L)"]').getAttribute('aria-pressed'),
    position: Number(document.querySelector('.transport .seek').value),
  }));
  await page.waitForTimeout(700);
  const advanced = await page.evaluate(() => Number(document.querySelector('.transport .seek').value));
  assert(stepped.song !== first, `desktop/stepping: the automatic step did not change track: ${JSON.stringify(stepped)}`);
  assert(advanced > stepped.position, `desktop/stepping: the track stepped to did not start playing (${stepped.position} → ${advanced})`);
  assert(stepped.activeIsSearch && stepped.query === 'flute', `desktop/stepping: the automatic rebuild took the keyboard away from the search box: ${JSON.stringify(stepped)}`);
  assert(stepped.theme === 'modern' && stepped.loop === 'false', `desktop/stepping: typed letters reached the global keymap during the step: ${JSON.stringify(stepped)}`);
  await page.close();

  // the same track with the option off: playback ends there, and nothing is left to resume from
  const quiet = await stepper.newPage({ viewport: viewports.desktop });
  quiet.on('pageerror', (error) => errors.push(`desktop/stepping-off: ${String(error)}`));
  await quiet.goto(target.href, { waitUntil: 'networkidle' });
  await quiet.waitForSelector('.rows .row', { timeout: 20000 });
  const unchecked = await quiet.evaluate(() => {
    document.querySelector('.top [aria-label="settings"]').dispatchEvent(new MouseEvent('click', { bubbles: true }));
    const box = Array.from(document.querySelectorAll('.settings .trackopts input[type="checkbox"]'))
      .find((input) => (input.closest('label')?.textContent ?? '').includes('Automatically step'));
    box?.click();
    document.querySelector('.settings .btn.close-settings')?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    return { found: !!box, checked: box?.checked ?? null };
  });
  const staying = await quiet.$eval('.songpicker', (select) => select.value);
  await quiet.click('.transport .btn.play');
  await nearTheEnd(quiet, 2);
  try {
    await quiet.waitForFunction(() => document.querySelector('.transport .status')?.textContent?.trim() === 'end', undefined, { timeout: 25000 });
  } catch (error) {
    failures.push(`desktop/stepping-off: the track never reached its end: ${String(error)}`);
    await quiet.close();
    await stepper.close();
    return;
  }
  await quiet.waitForTimeout(400);
  const ended = await quiet.evaluate(() => ({
    song: document.querySelector('.songpicker').value,
    t: new URLSearchParams(location.search).get('t'),
  }));
  assert(unchecked.found && unchecked.checked === false, `desktop/stepping-off: the Settings checkbox did not go off: ${JSON.stringify(unchecked)}`);
  assert(ended.song === staying, `desktop/stepping-off: the track stepped on with automatic stepping switched off: ${JSON.stringify(ended)}`);
  assert(ended.t === null, `desktop/stepping-off: a track that ran to its end left a position for a reload to resume from: ${JSON.stringify(ended)}`);
  await quiet.close();
  await stepper.close();
}

await steppingKeepsPlayingAndKeepsTheKeyboard();

/**
 * The newest switch wins. Two song loads can be in flight at once now that one of them starts by
 * itself — the automatic step's fetch, and a track the user picks while it is still loading — and
 * whichever set document arrived last used to rebuild the UI over the other. Here the first pick
 * is served slowly and the second quickly, so without the guard the app ends up on the track the
 * user moved away from.
 */
async function theNewestSwitchWins() {
  const page = await browser.newPage({ viewport: viewports.desktop });
  page.on('pageerror', (error) => errors.push(`desktop/switching: ${String(error)}`));
  const target = new URL(url);
  target.searchParams.set('theme', 'modern');
  await page.goto(target.href, { waitUntil: 'networkidle' });
  await page.waitForSelector('.rows .row', { timeout: 20000 });
  const songs = await page.$eval('.songpicker', (select) => Array.from(select.options).map((option) => option.value));
  if (songs.length < 3) {
    process.stderr.write('desktop/modern: fewer than three songs served — skipping the concurrent-switch check\n');
    await page.close();
    return;
  }
  const [, slow, quick] = songs;
  await page.route('**/s/**/*.json', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, route.request().url().includes(`/s/${slow}/`) ? 2500 : 100));
    await route.continue();
  });
  const pick = (id) => page.evaluate((value) => {
    const picker = document.querySelector('.songpicker');
    picker.value = value;
    picker.dispatchEvent(new Event('change', { bubbles: true }));
  }, id);
  await pick(slow);
  await page.waitForTimeout(150);
  await pick(quick);
  await page.waitForTimeout(3500);
  const landed = await page.evaluate(() => ({
    picker: document.querySelector('.songpicker').value,
    song: new URLSearchParams(location.search).get('song'),
  }));
  assert(landed.picker === quick && (landed.song === null || landed.song === quick), `desktop/switching: a slower earlier switch rebuilt over the track the user picked: ${JSON.stringify({ ...landed, slow, quick })}`);
  await page.close();
}

await theNewestSwitchWins();


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
    // The closing buttons sit centred in the room below the last section (#40, modern+amiga only:
    // win95 keeps the base layout, so a leak of either declaration shows up as changed numbers).
    const footrow = snapshot.settingsFontReset.footrow;
    const expectedFootrow = snapshot.theme === 'win95' ? { above: 14, below: 23 } : { above: 29, below: 29 };
    assert(footrow && close(footrow.above, expectedFootrow.above, 0.5) && close(footrow.below, expectedFootrow.below, 0.5), `${label}/${snapshot.theme}: closing buttons sit ${JSON.stringify(footrow)}, expected ${JSON.stringify(expectedFootrow)}`);
    if (snapshot.theme !== 'win95') assert(footrow && close(footrow.above, footrow.below, 1), `${label}/${snapshot.theme}: closing buttons are not centred below the reset-font button: ${JSON.stringify(footrow)}`);
    // #41: the ● ends up on the centre of its parentheses — the lift applied has to match the ink
    // measurement, in every theme, so a fallback face with different metrics fails this instead of
    // passing on a restated stylesheet value. Amiga is the only theme that needs (and has) a lift.
    const glyph = snapshot.settingsFontReset.listenedGlyph;
    assert(glyph && close(glyph.lift, glyph.deficit, 1), `${label}/${snapshot.theme}: the listened dot is lifted ${glyph?.lift}px where its ink asks for ${glyph?.deficit}px: ${JSON.stringify(glyph)}`);
    if (snapshot.theme === 'amiga') assert(glyph?.position === 'relative' && glyph.lift >= 2, `${label}/amiga: the listened dot is not lifted onto the parentheses' centre: ${JSON.stringify(glyph)}`);
    else assert(glyph && glyph.top === 'auto', `${label}/${snapshot.theme}: the amiga listened-dot lift leaked into this theme: ${JSON.stringify(glyph)}`);
    // #28: Settings carries both track options at every window size, whatever the Tracks caption does
    assert(snapshot.settingsFontReset.trackOptions.length === 2 && snapshot.settingsFontReset.trackOptionsVisible, `${label}/${snapshot.theme}: Settings does not show both track options: ${JSON.stringify(snapshot.settingsFontReset.trackOptions)}`);
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
