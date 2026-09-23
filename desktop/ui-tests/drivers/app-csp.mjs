// The Content-Security-Policy the app serves its own pages with, applied by
// every harness that loads one.
//
// Tested under the policy they ship with, so a page that needs something the
// policy forbids fails here rather than in the app.
import { readFileSync } from 'node:fs';

const config = JSON.parse(
  readFileSync(new URL('../../tauri.conf.json', import.meta.url), 'utf8'),
);

export const APP_CSP = config.app.security.csp;

/** Headers for serving `name` the way the app does. */
export function servedHeaders(name) {
  return name.endsWith('.html') ? { 'content-security-policy': APP_CSP } : {};
}

/** Record every policy violation on `page` into `errors`. */
export function reportPolicyViolations(page, errors) {
  page.on('console', (message) => {
    if (message.type() === 'error' && message.text().includes('Content Security Policy')) {
      errors.push(message.text());
    }
  });
}
