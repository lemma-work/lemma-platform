import { useEffect, useRef, useState } from "react";
import { View } from "react-native";
import { WebView, type WebViewMessageEvent } from "react-native-webview";
import { tokensFrom, type Tokens } from "./auth";
import { color } from "../ui/tokens";
import type { Server } from "./server";
import { useSession } from "./session";

/** The app's two sessions meet here, and only here.
 *
 *  Signing in happens on the web's own sign-in page, inside a WebView, so
 *  every way the web signs in (email code, password) works unchanged. That
 *  leaves a cookie session in the WebView, which the app's WebViews keep
 *  using for pod apps and workspace pages. Once `/users/me` answers there,
 *  the script below asks the API for a session of the app's own --
 *  `POST /auth/cli/session-tokens`, the call `lemma auth login` makes from
 *  a browser -- and passes it out. The two sessions have separate refresh
 *  tokens, so neither can trip theft detection on the other; nothing moves
 *  a token from one side to the other except that one freshly minted pair.
 *
 *  A sign-in finished in the phone's browser (`handoff.ts`) arrives as a
 *  claim instead: the script first trades the handoff's verifier at
 *  `POST /auth/desktop/session` for a cookie session of the WebView's own,
 *  then mints from it as above. The claim goes first, before `/users/me` is
 *  believed, so a cookie session some earlier sign-in left behind cannot be
 *  the one that gets minted. */

export interface Claim {
    requestId: string;
    verifier: string;
    expiresAt: number;
}

function mintScript(api: string, nonce: string, claim: Claim | null): string {
    return `(function () {
  if (window.__lemmaMint) return; window.__lemmaMint = true;
  var raw = window.__lemmaFetch || window.fetch.bind(window);
  var api = ${JSON.stringify(api)};
  var claim = ${JSON.stringify(claim)};
  var post = function (kind, body) { window.ReactNativeWebView.postMessage(JSON.stringify({ nonce: ${JSON.stringify(nonce)}, kind: kind, body: body })); };
  var minted = false;
  var mint = function () {
    var ask = function (f) { return f(api + "/auth/cli/session-tokens", { method: "POST", credentials: "include", headers: { Accept: "application/json", rid: "anti-csrf", "st-auth-mode": "cookie" } }); };
    return ask(raw).then(function (r) { return r.ok ? r : ask(window.fetch.bind(window)); }).then(function (r) {
      if (!r.ok) { post("problem", "A session for the app could not be made (" + r.status + ")."); return; }
      return r.json().then(function (body) { minted = true; post("session", body); });
    });
  };
  var check = function () {
    if (minted) return;
    raw(api + "/users/me", { credentials: "include", cache: "no-store", headers: { Accept: "application/json" } })
      .then(function (r) { if (r.ok) return r.json().then(function (me) { post("signed-in", me && me.email ? me.email : null); return mint(); }); setTimeout(check, 1500); })
      .catch(function () { setTimeout(check, 3000); });
  };
  if (!claim) { check(); return; }
  var CLAIMED = "lemma.mobile.claimed";
  var claimed = function () { try { return window.sessionStorage.getItem(CLAIMED) === claim.requestId; } catch (e) { return false; } };
  var waited = 0;
  var exchange = function () {
    if (Date.now() > claim.expiresAt) { post("problem", "That sign-in ran out of time. Start again."); return; }
    /* Through the page's own fetch, once its session layer has wrapped it, so
       the web app records the session it is given like one it made itself. */
    if (window.fetch === window.__lemmaNativeFetch && waited < 20) { waited += 1; setTimeout(exchange, 250); return; }
    window.fetch(api + "/auth/desktop/session", { method: "POST", credentials: "include", headers: { "Content-Type": "application/json", Accept: "application/json", "st-auth-mode": "cookie" }, body: JSON.stringify({ request_id: claim.requestId, code_verifier: claim.verifier }) })
      .then(function (r) {
        if (r.status === 409) { setTimeout(exchange, 1500); return; }
        if (r.ok) { try { window.sessionStorage.setItem(CLAIMED, claim.requestId); } catch (e) {} check(); return; }
        post("problem", r.status === 404 ? "That sign-in ran out of time. Start again." : "Signing in could not finish (" + r.status + ").");
      }, function () { setTimeout(exchange, 3000); });
  };
  if (claimed()) check(); else exchange();
})(); true;`;
}

/** Kept before the page's own scripts run, so the checks above use a fetch
 *  the page's session layer has not wrapped (it would take a 401 as "refresh
 *  me" and keep asking). The unbound original too, so a claim can tell when
 *  that layer has replaced it. */
const KEEP_FETCH = "window.__lemmaNativeFetch = window.fetch; window.__lemmaFetch = window.fetch.bind(window); true;";

export function SignInWebView({ server, nonce, claim = null, onSession, onProblem, onSignedIn, onLeave }: {
    server: Server;
    nonce: string;
    /** A sign-in finished in the browser, for this WebView to take up. */
    claim?: Claim | null;
    onSession: (tokens: Tokens) => void;
    onProblem: (said: string) => void;
    onSignedIn: (email: string | null) => void;
    /** A sign-in that leaves for a provider that refuses WebViews. */
    onLeave: (url: string) => void;
}) {
    const handled = useRef(false);
    const onMessage = (event: WebViewMessageEvent) => {
        let message: { nonce?: string; kind?: string; body?: unknown };
        try {
            message = JSON.parse(event.nativeEvent.data) as typeof message;
        } catch {
            return;
        }
        if (message.nonce !== nonce) return;
        if (message.kind === "signed-in") onSignedIn(typeof message.body === "string" ? message.body : null);
        if (message.kind === "problem" && typeof message.body === "string") onProblem(message.body);
        if (message.kind === "session" && !handled.current) {
            const tokens = tokensFrom(message.body);
            if (!tokens) {
                onProblem("The session came back incomplete.");
                return;
            }
            handled.current = true;
            onSession(tokens);
        }
    };
    return (
        <WebView
            source={{ uri: `${server.web}/auth` }}
            originWhitelist={["http://*", "https://*"]}
            injectedJavaScriptBeforeContentLoaded={KEEP_FETCH}
            injectedJavaScript={mintScript(server.api, nonce, claim)}
            onMessage={onMessage}
            sharedCookiesEnabled
            thirdPartyCookiesEnabled
            domStorageEnabled
            javaScriptEnabled
            setSupportMultipleWindows={false}
            onShouldStartLoadWithRequest={(request) => {
                /* Google refuses to sign anyone in inside an app's WebView. */
                if (/^https:\/\/accounts\.google\.com\//.test(request.url)) {
                    onLeave(request.url);
                    return false;
                }
                return true;
            }}
            style={{ flex: 1, backgroundColor: color.paper }}
        />
    );
}

function signOutScript(api: string): string {
    return `(function () {
  if (window.__lemmaOut) return; window.__lemmaOut = true;
  var api = ${JSON.stringify(api)};
  var raw = window.__lemmaFetch || window.fetch.bind(window);
  var done = function (said) { window.ReactNativeWebView.postMessage(said); };
  var check = function (left) {
    raw(api + "/users/me", { credentials: "include", cache: "no-store" })
      .then(function (r) { if (r.status === 401) done("out"); else if (left > 0) setTimeout(function () { check(left - 1); }, 700); else done("still in: " + r.status); })
      .catch(function () { done("out"); });
  };
  window.fetch(api + "/st/auth/signout", { method: "POST", credentials: "include", headers: { rid: "session", "st-auth-mode": "cookie", "fdi-version": "4.2", "Content-Type": "application/json" }, body: "{}" })
    .then(function (r) { check(8); }, function (e) { done("signout failed: " + e); });
})(); true;`;
}

/** After the app signs out, the WebViews' cookie session is signed out too,
 *  on the web's own origin, so the next sign-in starts clean. */
export function WebSignOut() {
    const { webSignOuts, server } = useSession();
    const [pending, setPending] = useState<{ n: number; server: Server } | null>(null);
    useEffect(() => {
        if (webSignOuts > 0) setPending({ n: webSignOuts, server });
    }, [webSignOuts, server]);
    if (!pending) return null;
    return (
        <View style={{ width: 1, height: 1, position: "absolute", opacity: 0 }} pointerEvents="none">
            <WebView
                key={pending.n}
                source={{ uri: `${pending.server.web}/auth` }}
                injectedJavaScript={signOutScript(pending.server.api)}
                onMessage={(event) => {
                    if (__DEV__ && event.nativeEvent.data !== "out") console.warn("web sign-out:", event.nativeEvent.data);
                    setPending(null);
                }}
                injectedJavaScriptBeforeContentLoaded={KEEP_FETCH}
                sharedCookiesEnabled
                thirdPartyCookiesEnabled
            />
        </View>
    );
}
