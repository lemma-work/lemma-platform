// Keep SDK_VERSION in sync with package.json "version". The CI codegen/drift
// gate (workstream A) asserts they match so this can't silently drift.
export const SDK_VERSION = "0.7.0";

/** Sent as `X-Lemma-Client` when it will not add an otherwise avoidable browser
 *  preflight, so the backend can log which client + version hit an endpoint. */
export const CLIENT_HEADER_NAME = "X-Lemma-Client";

/** Which published app is calling. Bounded to a UUID by the backend, which
 *  ignores anything else -- it is a caller-supplied header, so it names a
 *  dimension, never grants anything. */
export const APP_HEADER_NAME = "X-Lemma-App";

/** Clients the backend recognises as an origin of their own (`app/core/origin.py`).
 *  Anything else degrades to `SDK`, which is the honest answer for a caller that
 *  did not name itself — so this list is an allowlist, not a suggestion, and it
 *  mirrors `_KNOWN_CLIENTS` in `lemma-python/lemma_sdk/transport.py`. */
export const KNOWN_CLIENTS = [
  "lemma-sdk-ts",
  "lemma-web",
  "lemma-desktop",
  "lemma-app",
  "lemma-cli",
] as const;

export type KnownClient = (typeof KNOWN_CLIENTS)[number];

export const DEFAULT_CLIENT: KnownClient = "lemma-sdk-ts";

/** Build the header value for `client`.
 *
 *  The browser and Desktop both ship this SDK, so without naming themselves they
 *  are indistinguishable from somebody's script — which left `WEB` and `DESKTOP`
 *  unreachable as origins and put every human's traffic under `SDK`.
 */
export function clientHeaderValue(client: KnownClient = DEFAULT_CLIENT): string {
  const named = KNOWN_CLIENTS.includes(client) ? client : DEFAULT_CLIENT;
  return `${named}/${SDK_VERSION}`;
}


export function shouldSendClientHeader(apiUrl: string, method: string): boolean {
  const normalizedMethod = method.toUpperCase();
  if (normalizedMethod !== "GET" && normalizedMethod !== "HEAD") {
    return true;
  }

  if (typeof window === "undefined") {
    return true;
  }

  try {
    return new URL(apiUrl, window.location.origin).origin === window.location.origin;
  } catch {
    return true;
  }
}
