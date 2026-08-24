/** Transport icons: one SVG set, identical 16 px box and weight, drawn with currentColor. */
const svg = (inner: string): string =>
  `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false" fill="currentColor">${inner}</svg>`;

export const ICONS = {
  up: svg('<path d="M12 6 4 17h16z"/>'),
  down: svg('<path d="M12 18 4 7h16z"/>'),
  play: svg('<path d="M7 5v14l12-7z"/>'),
  pause: svg('<path d="M6 5h4v14H6zM14 5h4v14h-4z"/>'),
  stop: svg('<path d="M6 6h12v12H6z"/>'),
  back: svg('<path d="M12 6v12L3 12zM21 6v12l-9-6z"/>'),
  fwd: svg('<path d="M12 6v12l9-6zM3 6v12l9-6z"/>'),
} as const;

export function setIcon(el: HTMLElement, name: keyof typeof ICONS): void {
  el.innerHTML = ICONS[name];
}
