// The Content-Security-Policy the app serves its own pages with, applied by
// every harness that loads one.
//
// The pages used to be tested with no policy at all, while the app ran them
// under one -- so nothing checked that the policy and the pages agreed. That
// is also why the policy could keep `'unsafe-inline'` for scripts: nothing
// would have noticed the page that needed it, or the day none did.
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
