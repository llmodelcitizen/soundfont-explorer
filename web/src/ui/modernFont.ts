/** Modern-theme font choices. The selection is local-only and intentionally absent from URLs. */
export const MODERN_FONTS = [
  { id: 'ibm-plex-sans', label: 'IBM Plex Sans', family: "'SFP IBM Plex Sans', system-ui, sans-serif" },
  { id: 'inter', label: 'Inter', family: "'SFP Inter', system-ui, sans-serif" },
  { id: 'space-grotesk', label: 'Space Grotesk', family: "'SFP Space Grotesk', system-ui, sans-serif" },
  { id: 'manrope', label: 'Manrope', family: "'SFP Manrope', system-ui, sans-serif" },
  { id: 'outfit', label: 'Outfit', family: "'SFP Outfit', system-ui, sans-serif" },
  { id: 'urbanist', label: 'Urbanist', family: "'SFP Urbanist', system-ui, sans-serif" },
  { id: 'sora', label: 'Sora', family: "'SFP Sora', system-ui, sans-serif" },
  { id: 'exo-2', label: 'Exo 2', family: "'SFP Exo 2', system-ui, sans-serif" },
  { id: 'titillium-web', label: 'Titillium Web', family: "'SFP Titillium Web', system-ui, sans-serif" },
  { id: 'chakra-petch', label: 'Chakra Petch', family: "'SFP Chakra Petch', system-ui, sans-serif" },
  { id: 'rajdhani', label: 'Rajdhani', family: "'SFP Rajdhani', system-ui, sans-serif" },
  { id: 'oxanium', label: 'Oxanium', family: "'SFP Oxanium', system-ui, sans-serif" },
  { id: 'orbitron', label: 'Orbitron', family: "'SFP Orbitron', system-ui, sans-serif" },
  { id: 'victor-mono', label: 'Victor Mono', family: "'SFP Victor Mono', ui-monospace, monospace" },
] as const;

export type ModernFontId = (typeof MODERN_FONTS)[number]['id'];
export const DEFAULT_MODERN_FONT: ModernFontId = MODERN_FONTS[0].id;
export const MODERN_FONT_KEY = 'sfp.modern-font.v1';

export function modernFont(id: ModernFontId) {
  return MODERN_FONTS.find((font) => font.id === id) ?? MODERN_FONTS[0];
}

export function readModernFont(): ModernFontId {
  try {
    const value = localStorage.getItem(MODERN_FONT_KEY);
    return MODERN_FONTS.some((font) => font.id === value) ? (value as ModernFontId) : DEFAULT_MODERN_FONT;
  } catch {
    return DEFAULT_MODERN_FONT;
  }
}

export function saveModernFont(id: ModernFontId): void {
  try {
    localStorage.setItem(MODERN_FONT_KEY, id);
  } catch {
    /* private mode: the applied in-memory selection still works */
  }
}

export function applyModernFont(id: ModernFontId): void {
  const font = modernFont(id);
  document.documentElement.style.setProperty('--modern-font', font.family);
  document.documentElement.dataset.modernFont = font.id;
}

export function nextModernFont(id: ModernFontId): ModernFontId {
  const index = MODERN_FONTS.findIndex((font) => font.id === id);
  return MODERN_FONTS[(index + 1) % MODERN_FONTS.length]!.id;
}
