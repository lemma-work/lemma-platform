/**
 * Telling the host where an app is, and opening another app of the same Lemma.
 *
 * An app is one screen of work and the page for one of its records, so the
 * record it has open is part of where the person is: a link they copy should
 * land the next person on the same record. The app cannot write that link.
 * Framed, it has no address bar — the host does — and the browser's history is
 * the host's too. An app that pushed its own entries would leave Back to undo
 * a step inside the frame while the address above it stayed where it was.
 *
 * So the app says where it moved and the host writes it down. When the host's
 * address changes instead — Back, a link someone followed — the host says so
 * and the app follows. Neither side ever answers a move with the same move,
 * which is what keeps the two from chasing each other.
 *
 * Opening another app is the same shape: a deal names its person, and the
 * person lives in a different app of the same Lemma. The app asks; the host,
 * which knows what apps there are, goes, and says it is going.
 *
 * On its own in a tab there is no host. The app's path is then simply the
 * page's, pushed and popped like any other, and another app is opened by going
 * to that app's own address.
 */

import { isEmbeddedInHost } from './embedded.js';

export const LEMMA_LOCATION_MESSAGE_TYPE = 'lemma-location';
export const LEMMA_OPEN_MESSAGE_TYPE = 'lemma-open';
export const LEMMA_OPEN_RESULT_MESSAGE_TYPE = 'lemma-open-result';

/** Longer than this is not somewhere an app is; it is something else in the path. */
const MAX_PATH_LENGTH = 512;

/** How long a host has to say it is opening the app before the page goes itself. */
const HOST_ANSWER_TIMEOUT_MS = 1500;

interface LemmaLocationMessage {
  type: typeof LEMMA_LOCATION_MESSAGE_TYPE;
  path: string;
}

interface LemmaOpenMessage {
  type: typeof LEMMA_OPEN_MESSAGE_TYPE;
  /** Correlates the host's answer with this request. */
  id: string;
  app: string;
  path: string;
}

/**
 * Where another app of this Lemma lives: its own address, or nothing when it
 * has none. `client.apps.open` passes one that asks the API.
 */
export type AppAddressFinder = (app: string) => Promise<string | null | undefined>;

/**
 * A path inside an app, in the one spelling both sides compare: rooted, no
 * trailing slash, no empty or dot segments, no query or fragment. `/` is the
 * app's one screen. Null for anything that is not a path.
 */
export function normalizeAppPath(value: unknown): string | null {
  if (typeof value !== 'string' || !value.startsWith('/') || value.length > MAX_PATH_LENGTH) {
    return null;
  }
  const trimmed = value.length > 1 && value.endsWith('/') ? value.slice(0, -1) : value;
  if (trimmed === '/') return '/';
  const segments = trimmed.slice(1).split('/');
  const readable = segments.every(
    (segment) => segment !== '' && segment !== '.' && segment !== '..' && !/[?#\\\s]/.test(segment),
  );
  return readable ? `/${segments.join('/')}` : null;
}

/**
 * True when a host frames this page.
 *
 * It answers for the frame, not for the room: a host that does not speak this
 * protocol still looks like one from in here. Nothing breaks when it does not
 * — the app keeps its own path, the address above it simply stays put, and
 * `openApp` goes to the other app itself once the host has not answered.
 */
export function isFramedByHost(): boolean {
  return typeof window !== 'undefined' && window.parent !== window;
}

/** The path the page was opened at: where the app starts. */
export function currentAppPath(): string {
  if (typeof window === 'undefined') return '/';
  return normalizeAppPath(window.location.pathname) ?? '/';
}

/**
 * An app's address with a path inside it: where going there lands. Null for
 * anything that is not a web address, so a value from elsewhere can never send
 * the page to a `javascript:` URL.
 */
export function appAddressAt(url: string, path: string): string | null {
  const at = normalizeAppPath(path);
  if (at === null) return null;
  let address: URL;
  try {
    address = new URL(url);
  } catch {
    return null;
  }
  if (address.protocol !== 'https:' && address.protocol !== 'http:') return null;
  address.pathname = address.pathname.replace(/\/$/, '') + at;
  return address.href;
}

function writePath(path: string, push: boolean): void {
  const url = path + window.location.search + window.location.hash;
  try {
    if (push) window.history.pushState(window.history.state, '', url);
    else window.history.replaceState(window.history.state, '', url);
  } catch {
    // A sandboxed frame may refuse; the app still shows the path it was given.
  }
}

/**
 * Move the app to `path`: the list, or the page for one record.
 *
 * Framed, the page's own entry is rewritten in place and the host is told, so
 * the one new history entry is the host's. On its own, it is an ordinary step
 * in the page's own history, so the address bar and Back work as on any page.
 * Returns the path as it was written, or null when `path` is not one.
 */
export function moveAppTo(path: string): string | null {
  const next = normalizeAppPath(path);
  if (next === null || typeof window === 'undefined') return null;
  if (!isFramedByHost()) {
    if (next !== currentAppPath()) writePath(next, true);
    return next;
  }
  writePath(next, false);
  const message: LemmaLocationMessage = { type: LEMMA_LOCATION_MESSAGE_TYPE, path: next };
  try {
    // "*" for the reason `composeInConversation` gives: the frame does not know
    // its host's origin, and a path inside the app is nothing private — it is
    // about to be shown in the address bar.
    window.parent.postMessage(message, '*');
  } catch {
    // No host to tell; the app has still moved.
  }
  return next;
}

/** Ask the host to open `app` at `path`. True once it says it is. */
function askHost(app: string, path: string): Promise<boolean> {
  const id = `open-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  const message: LemmaOpenMessage = { type: LEMMA_OPEN_MESSAGE_TYPE, id, app, path };
  return new Promise<boolean>((resolve) => {
    let settled = false;
    const finish = (took: boolean) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      window.removeEventListener('message', hear);
      resolve(took);
    };
    const hear = (event: MessageEvent) => {
      // Only the host answers, and only about this request.
      if (event.source !== window.parent) return;
      const data = event.data as { type?: unknown; id?: unknown } | null;
      if (data && typeof data === 'object' && data.type === LEMMA_OPEN_RESULT_MESSAGE_TYPE && data.id === id) {
        finish(true);
      }
    };
    const timer = window.setTimeout(() => finish(false), HOST_ANSWER_TIMEOUT_MS);
    window.addEventListener('message', hear);
    try {
      window.parent.postMessage(message, '*');
    } catch {
      finish(false);
    }
  });
}

/**
 * Open another app of the same Lemma at `path` (`/` is its one screen).
 *
 * Framed, the host is asked, and the host — which knows the Lemma's apps and
 * owns the address — goes there. With no host, or one that does not answer, the
 * page goes to that app's own address, found with `find`. Inside an AI tool's
 * conversation the frame is the tool's view, which hands this app its session,
 * so another app loaded into it would arrive signed out: there it does nothing.
 *
 * Resolves true once the app is being opened, and false when it could not be:
 * no `find`, no address, or an AI tool's frame.
 */
export async function openApp(app: string, path = '/', find?: AppAddressFinder): Promise<boolean> {
  const name = typeof app === 'string' ? app.trim() : '';
  const at = normalizeAppPath(path);
  if (!name || at === null || typeof window === 'undefined') return false;
  if (isFramedByHost() && (await askHost(name, at))) return true;
  if (isEmbeddedInHost() || !find) return false;
  let url: string | null | undefined;
  try {
    url = await find(name);
  } catch {
    return false;
  }
  const address = url ? appAddressAt(url, at) : null;
  if (!address) return false;
  window.location.assign(address);
  return true;
}

/**
 * Hear the path change from outside the app: the host's address moved, or —
 * unframed — the person pressed Back.
 *
 * The listener is called with where the app should now be. It is never called
 * for a move the app made itself with `moveAppTo`. Returns the way to stop.
 */
export function watchAppPath(listener: (path: string) => void): () => void {
  if (typeof window === 'undefined') return () => undefined;
  const fromHost = (event: MessageEvent) => {
    // Only the host says where the app is. A frame beside this one does not.
    if (event.source !== window.parent || !isFramedByHost()) return;
    const data = event.data as { type?: unknown; path?: unknown } | null;
    if (!data || typeof data !== 'object' || data.type !== LEMMA_LOCATION_MESSAGE_TYPE) return;
    const path = normalizeAppPath(data.path);
    if (path === null) return;
    writePath(path, false);
    listener(path);
  };
  const popped = () => listener(currentAppPath());
  window.addEventListener('message', fromHost);
  window.addEventListener('popstate', popped);
  return () => {
    window.removeEventListener('message', fromHost);
    window.removeEventListener('popstate', popped);
  };
}
