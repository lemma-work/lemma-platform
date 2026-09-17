/**
 * `vncSocketUrl` and the reconnect backoff -- what is left of this file since
 * the JPEG/CDP translation it used to hold moved to a real VNC display and
 * stopped needing one. See `browser-view.ts`'s own docstring.
 */

import { describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/sdk/lemma-client', () => ({
    getLemmaApiBaseUrl: () => 'https://api.lemma.test',
}));

import { reconnectDelayMs, vncSocketUrl } from './browser-view';

describe('vncSocketUrl', () => {
    it('builds a ws URL carrying the mode', () => {
        const url = vncSocketUrl({ mode: 'view' });
        expect(url).toBe('wss://api.lemma.test/workspace/browser/view?mode=view');
    });

    it('carries an origin, for a sign-in', () => {
        const url = vncSocketUrl({ mode: 'control', origin: 'https://example.com' });
        const parsed = new URL(url.replace(/^ws/, 'http'));
        expect(parsed.searchParams.get('origin')).toBe('https://example.com');
        expect(parsed.searchParams.get('mode')).toBe('control');
    });

    it('carries the access token in the query, not a header', () => {
        // A browser cannot set headers on a WebSocket handshake at all, so this
        // is the only way a caller that cannot attach a cookie authenticates.
        const url = vncSocketUrl({ mode: 'view', accessToken: 'tok-abc' });
        const parsed = new URL(url.replace(/^ws/, 'http'));
        expect(parsed.searchParams.get('access_token')).toBe('tok-abc');
    });

    it('names no session or conversation', () => {
        // VNC shows the sandbox's whole shared display, not one session's tab
        // -- so unlike the JSON stream this replaced, there is nothing here to
        // scope a connection to beyond the mode and, for a sign-in, the site.
        const url = vncSocketUrl({ mode: 'view', origin: 'https://example.com' });
        const parsed = new URL(url.replace(/^ws/, 'http'));
        expect(parsed.searchParams.has('conversation')).toBe(false);
        expect(parsed.searchParams.has('session')).toBe(false);
    });
});

describe('reconnecting', () => {
    it('backs off, with jitter, to a ceiling', () => {
        for (let attempt = 0; attempt < 12; attempt += 1) {
            const delay = reconnectDelayMs(attempt);
            expect(delay).toBeGreaterThanOrEqual(0);
            expect(delay).toBeLessThanOrEqual(30_000);
        }
    });
});
