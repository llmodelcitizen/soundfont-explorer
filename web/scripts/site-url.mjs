import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

/** The deployed site's URL: $SFP_SITE_URL, else `site_url` from infra/live/outputs.json (private overlay), else null. */
export function siteUrl() {
  if (process.env.SFP_SITE_URL) return process.env.SFP_SITE_URL;
  try {
    const p = fileURLToPath(new URL('../../infra/live/outputs.json', import.meta.url));
    return JSON.parse(readFileSync(p, 'utf8')).site_url.value;
  } catch {
    return null;
  }
}
