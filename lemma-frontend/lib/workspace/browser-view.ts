/**
 * Building the URL `BrowserPane` opens for a VNC view of the sandbox display,
 * and the backoff it reconnects with.
 *
 * This used to be a much larger file: a translation from DOM events into
 * `agent-browser`'s JSON stream protocol, and the coordinate mapping between
 * a canvas element, a scaled-down JPEG picture, and the page's own CSS
 * pixels. All of that is gone along with the transport it was written for --
 * `@novnc/novnc`'s `RFB` class owns rendering and input capture itself, so
 * there is no frame protocol here to translate and no coordinate space here
 * to get wrong. See `browser-pane.tsx` for what replaced it, and
 * `sandbox_runtime/browser_relay/stream_proxy.py` for the sandbox half.
 */

import { getLemmaApiBaseUrl } from '@/lib/sdk/lemma-client';

//: How long to wait before reconnecting, and how that grows. Capped, with
//: jitter so a sandbox restart does not have every open pane retry in lockstep.
const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 30_000;

export const reconnectDelayMs = (attempt: number): number =>
    Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** attempt) * Math.random();

/**
 * Where to open a VNC view of this person's sandbox display.
 *
 * `origin`, when given, means a sign-in: it is passed through so the backend
 * can steer the browser there before attaching. No `conversation` -- VNC
 * shows the sandbox's whole shared display rather than one session's tab, so
 * there is no session for one to select.
 */
export const vncSocketUrl = (options: {
    mode: string;
    origin?: string;
    accessToken?: string;
}): string => {
    const base = getLemmaApiBaseUrl().replace(/^http/, 'ws').replace(/\/$/, '');
    const query = new URLSearchParams({ mode: options.mode });
    if (options.origin) query.set('origin', options.origin);
    // In the URL because a browser cannot set headers on a WebSocket handshake
    // — the same reason the datastore changes socket does it.
    if (options.accessToken) query.set('access_token', options.accessToken);
    return `${base}/workspace/browser/view?${query.toString()}`;
};
