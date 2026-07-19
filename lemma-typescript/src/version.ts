// Keep SDK_VERSION in sync with package.json "version". The CI codegen/drift
// gate (workstream A) asserts they match so this can't silently drift.
export const SDK_VERSION = "0.6.3";

/** Sent as `X-Lemma-Client` when it will not add an otherwise avoidable browser
 *  preflight, so the backend can log which client + version hit an endpoint. */
export const CLIENT_HEADER_NAME = "X-Lemma-Client";
export const CLIENT_HEADER_VALUE = `lemma-sdk-ts/${SDK_VERSION}`;

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
