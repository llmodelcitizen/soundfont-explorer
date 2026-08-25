/**
 * The web app never touches node:fs, so the suite carries no @types/node. facetHelpCss.test.ts reads
 * the stylesheets as text (vitest replaces a `?raw` CSS import with an empty string), and this is
 * just enough of the module to type that one call.
 */
declare module 'node:fs' {
  export function readFileSync(path: string | URL, encoding: 'utf8'): string;
}
