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
 * can steer the browser there before attaching. `conversation`, when given
 * and `origin` is not, names the conversation whose agent browser this
 * should show -- `run_browser_script` runs every agent browser command in
 * its own session, named for the conversation, so without this a plain
 * watch/drive resolved to the shared default session and found nothing the
 * agent had touched (its `DevToolsActivePort` never written, the pane
 * refusing forever with "no browser running" while the agent's browser was
 * live the whole time, just in a different session). The picture itself is
 * still the sandbox's one shared display -- see `browser_relay/app.py`'s
 * `/vnc` route -- but which session's Chrome is checked for "is it up at
 * all" is per-conversation, and that is what this selects.
 */
export const vncSocketUrl = (options: {
    mode: string;
    origin?: string;
    conversationId?: string;
    accessToken?: string;
}): string => {
    const base = getLemmaApiBaseUrl().replace(/^http/, 'ws').replace(/\/$/, '');
    const query = new URLSearchParams({ mode: options.mode });
    if (options.origin) query.set('origin', options.origin);
    else if (options.conversationId) query.set('conversation', options.conversationId);
    // In the URL because a browser cannot set headers on a WebSocket handshake
    // — the same reason the datastore changes socket does it.
    if (options.accessToken) query.set('access_token', options.accessToken);
    return `${base}/workspace/browser/view?${query.toString()}`;
};
