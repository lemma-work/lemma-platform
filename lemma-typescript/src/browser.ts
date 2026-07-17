/**
 * Browser bundle entry point.
 * Exposes LemmaClient as globalThis.LemmaClient.LemmaClient
 *
 * Usage in HTML (the host injects window.__LEMMA_CONFIG__ — construct with NO
 * args so the app validator doesn't flag a hardcoded podId):
 *   <script src="/public/sdk/lemma-client.js"></script>
 *   <script>
 *     const client = new window.LemmaClient.LemmaClient(); // reads window.__LEMMA_CONFIG__
 *   </script>
 *
 * Canonical paths for this one bundle (kept in sync by the CI bundle-freshness
 * gate) — don't let these drift:
 *   - served URL:        /public/sdk/lemma-client.js  (backend public_sdk_controller)
 *   - committed on disk: lemma-typescript/public/lemma-client.js
 *   - package export:    dist/browser/lemma-client.js  (unpkg / "./browser-bundle")
 */
import { LemmaClient } from "./client.js";
import {
  AuthManager,
  buildAuthUrl,
  buildFederatedLogoutUrl,
  clearTestingToken,
  getTestingToken,
  resolveSafeRedirectUri,
  setTestingToken,
} from "./auth.js";
import { ApiError } from "./http.js";
import { POD_DEFAULT_AGENT_SELECTOR } from "./namespaces/conversations.js";

export {
  LemmaClient,
  AuthManager,
  buildAuthUrl,
  buildFederatedLogoutUrl,
  clearTestingToken,
  getTestingToken,
  resolveSafeRedirectUri,
  setTestingToken,
  ApiError,
  POD_DEFAULT_AGENT_SELECTOR,
};

// Browser globals. We standardize on `window.LemmaClient` (the skills, the app
// HTML lint, and the no-build starter all construct `new
// window.LemmaClient.LemmaClient()`), and keep `window.Lemma` as a back-compat
// alias for conversation widgets that historically loaded this bundle as
// `new Lemma.LemmaClient(...)`. Both point at the same surface object.
if (typeof globalThis !== "undefined") {
  const scope = globalThis as Record<string, unknown>;
  const surface = {
    LemmaClient,
    AuthManager,
    buildAuthUrl,
    buildFederatedLogoutUrl,
    clearTestingToken,
    getTestingToken,
    resolveSafeRedirectUri,
    setTestingToken,
    ApiError,
    POD_DEFAULT_AGENT_SELECTOR,
  };
  if (!scope.LemmaClient) {
    scope.LemmaClient = surface;
  }
  if (!scope.Lemma) {
    scope.Lemma = surface;
  }
}
