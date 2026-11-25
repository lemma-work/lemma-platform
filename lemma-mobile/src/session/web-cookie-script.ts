/** The scripts and URL rules behind `web-cookie.tsx`, as plain functions so
 *  they can be tested without a WebView. */

/** What the hidden WebView reports. */
export type Probe = "in" | "out" | "unsure";

/** A desktop-handoff request this app opened and approved itself, for the
 *  WebView to trade for a cookie session. */
export interface Seed {
    requestId: string;
    verifier: string;
}

function header(api: string, nonce: string): string {
    return `var api = ${JSON.stringify(api)};
  var post = function (kind, body) { window.ReactNativeWebView.postMessage(JSON.stringify({ nonce: ${JSON.stringify(nonce)}, kind: kind, body: body === undefined ? null : body })); };
  var me = function () { return fetch(api + "/users/me", { credentials: "include", cache: "no-store", headers: { Accept: "application/json" } }); };`;
}

/** Is the WebViews' cookie session alive? `/users/me` first; on a 401, one
 *  refresh, the way a pod app's SDK would on opening, then `/users/me`
 *  again. A refused refresh is "out": the refresh token is gone, or
 *  SuperTokens took it for a stolen one and ended the session. */
export function probeScript(api: string, nonce: string): string {
    return `(function () {
  if (window.__lemmaProbe) return; window.__lemmaProbe = true;
  ${header(api, nonce)}
  var refresh = function () { return fetch(api + "/st/auth/session/refresh", { method: "POST", credentials: "include", headers: { rid: "session", "fdi-version": "4.2", "st-auth-mode": "cookie", Accept: "application/json" } }); };
  me().then(function (r) {
    if (r.ok) { post("in"); return; }
    if (r.status !== 401) { post("unsure", r.status); return; }
    return refresh().then(function (again) {
      if (!again.ok) { post("out", again.status); return; }
      return me().then(function (after) { post(after.ok ? "in" : "out", after.status); });
    });
  }).catch(function (error) { post("unsure", String(error)); });
})(); true;`;
}

/** Trade an approved request's verifier for a cookie session of the
 *  WebViews' own (`POST /auth/desktop/session`, cookie mode), then confirm
 *  it with `/users/me`. */
export function seedScript(api: string, nonce: string, seed: Seed): string {
    return `(function () {
  if (window.__lemmaSeed) return; window.__lemmaSeed = true;
  ${header(api, nonce)}
  fetch(api + "/auth/desktop/session", { method: "POST", credentials: "include", headers: { "Content-Type": "application/json", Accept: "application/json", "st-auth-mode": "cookie" }, body: ${JSON.stringify(JSON.stringify({ request_id: seed.requestId, code_verifier: seed.verifier }))} })
    .then(function (r) {
      if (!r.ok) { post("out", r.status); return; }
      return me().then(function (after) { post(after.ok ? "in" : "out", after.status); });
    })
    .catch(function (error) { post("unsure", String(error)); });
})(); true;`;
}

/** A blank page on the web app's origin, for the scripts above to run in.
 *  Its fetches to the API are same-site, so they carry the session cookies
 *  pod apps read, and nothing of the web app loads to race them. */
export const BLANK_PAGE = "<!doctype html><html><head><meta charset=\"utf-8\"></head><body></body></html>";

/** Run in a framed page before its own scripts. A host whose SuperTokens
 *  update marker outlived its front token is one SuperTokens calls signed
 *  out without asking the server, so a page that once failed to refresh
 *  stays on its sign-in screen even after the session is back. Dropping the
 *  marker there makes the SDK ask; the session checked before the page
 *  opened answers. The same recovery the SDK runs on a return from sign-in
 *  (`isHalfCleared` in lemma-typescript/src/auth.ts). */
export const UNSTICK_MARKER = `(function () {
  try {
    var has = function (name) { return document.cookie.split(";").some(function (part) { return part.trim().indexOf(name + "=") === 0; }); };
    if (has("st-last-access-token-update") && !has("sFrontToken")) {
      document.cookie = "st-last-access-token-update=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
    }
  } catch (e) {}
})(); true;`;

/** Where a page was sent to sign in on this server's web app, if it was:
 *  the page it should come back to (`redirect_uri`), or `fallback` when it
 *  names none. Null for any other navigation. */
export function signInRedirect(url: string, web: string, fallback: string): string | null {
    let to: URL;
    let home: URL;
    try {
        to = new URL(url);
        home = new URL(web);
    } catch {
        return null;
    }
    if (to.origin !== home.origin) return null;
    if (to.pathname !== "/auth" && !to.pathname.startsWith("/auth/")) return null;
    const back = to.searchParams.get("redirect_uri");
    if (back && /^https?:\/\//i.test(back)) return back;
    return fallback;
}
