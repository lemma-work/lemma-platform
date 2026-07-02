// Keep SDK_VERSION in sync with package.json "version". The CI codegen/drift
// gate (workstream A) asserts they match so this can't silently drift.
export const SDK_VERSION = "0.5.5";

/** Sent as `X-Lemma-Client` on every request so the backend can log which client
 *  + version hit an endpoint (User-Agent is a forbidden header in browser fetch). */
export const CLIENT_HEADER_NAME = "X-Lemma-Client";
export const CLIENT_HEADER_VALUE = `lemma-sdk-ts/${SDK_VERSION}`;
