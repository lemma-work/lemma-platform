import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { View } from "react-native";
import { WebView, type WebViewMessageEvent } from "react-native-webview";
import * as Crypto from "expo-crypto";
import { authedFetch } from "./auth";
import { openRequest } from "./handoff";
import type { Server } from "./server";
import { useSession } from "./session";
import { BLANK_PAGE, probeScript, seedScript, type Probe, type Seed } from "./web-cookie-script";

/** Keeps the WebViews' cookie session alive for as long as the app's own.
 *
 *  Pod apps, workspace pages and live widgets sign in with the cookie
 *  session the sign-in WebView left (`web-session.tsx`). That session is a
 *  separate one from the app's header session, and it can end while the
 *  app's carries on. On Android a refreshed cookie that had not reached disk
 *  when the app was stopped comes back stale; SuperTokens takes the reuse of
 *  a spent refresh token for theft and ends the session. From then on every
 *  pod app opened on the web's sign-in page, inside the app, while the rest
 *  of the app worked.
 *
 *  So before a framed page opens, a hidden WebView on the web app's origin
 *  asks the API whether the cookie session is there (`probeScript`). When it
 *  is not, the app makes a new one from its own, with no token crossing
 *  over: it opens a desktop-handoff request with a fresh PKCE pair
 *  (`POST /auth/desktop/requests`), completes it with its own header session
 *  (`POST /auth/desktop/requests/{id}/complete`, as the signed-in browser
 *  does in a desktop sign-in), and the hidden WebView trades the verifier
 *  for a cookie session of its own (`POST /auth/desktop/session`). The two
 *  sessions still never share a refresh token. */

interface WebSession {
    /** Resolves once the WebViews hold a cookie session ("in"), or once one
     *  could not be made. `fresh` skips the answer remembered from a moment
     *  ago, for a page that was just sent to sign in anyway. */
    ensure: (fresh?: boolean) => Promise<Probe>;
}

const WebSessionContext = createContext<WebSession | null>(null);

/** A session seen alive is believed this long without asking again. */
const TRUST_MS = 5 * 60_000;
/** A hidden WebView that has not answered by now never will. */
const STEP_MS = 12_000;

interface Step {
    nonce: string;
    script: string;
    server: Server;
    done: (probe: Probe) => void;
}

/** A request this app opened and completed itself, ready to trade. */
async function approvedSeed(server: Server): Promise<Seed | null> {
    try {
        const { requestId, verifier } = await openRequest(server);
        const completed = await authedFetch(`${server.api}/auth/desktop/requests/${encodeURIComponent(requestId)}/complete`, {
            method: "POST",
            headers: { Accept: "application/json" },
        });
        return completed.ok ? { requestId, verifier } : null;
    } catch {
        return null;
    }
}

export function WebSessionProvider({ children }: { children: ReactNode }) {
    const { mode, server } = useSession();
    const [step, setStep] = useState<Step | null>(null);
    const running = useRef<Promise<Probe> | null>(null);
    const trusted = useRef<{ serverId: string; until: number } | null>(null);

    /* A sign-out or another server: what was seen no longer holds. */
    useEffect(() => {
        trusted.current = null;
    }, [mode, server.id]);

    const run = useCallback((at: Server, script: (nonce: string) => string) => new Promise<Probe>((resolve) => {
        const nonce = Crypto.randomUUID();
        let settled = false;
        const done = (probe: Probe) => {
            if (settled) return;
            settled = true;
            clearTimeout(timer);
            setStep(null);
            resolve(probe);
        };
        const timer = setTimeout(() => done("unsure"), STEP_MS);
        setStep({ nonce, script: script(nonce), server: at, done });
    }), []);

    const ensure = useCallback((fresh = false): Promise<Probe> => {
        if (mode !== "live") return Promise.resolve<Probe>("unsure");
        const at = server;
        const seen = trusted.current;
        if (!fresh && seen?.serverId === at.id && seen.until > Date.now()) return Promise.resolve<Probe>("in");
        running.current ??= (async () => {
            let probe = await run(at, (nonce) => probeScript(at.api, nonce));
            if (probe === "out") {
                const seed = await approvedSeed(at);
                if (seed) probe = await run(at, (nonce) => seedScript(at.api, nonce, seed));
            }
            trusted.current = probe === "in" ? { serverId: at.id, until: Date.now() + TRUST_MS } : null;
            return probe;
        })().finally(() => {
            running.current = null;
        });
        return running.current;
    }, [mode, server, run]);

    const onMessage = (event: WebViewMessageEvent) => {
        let message: { nonce?: string; kind?: string };
        try {
            message = JSON.parse(event.nativeEvent.data) as typeof message;
        } catch {
            return;
        }
        if (!step || message.nonce !== step.nonce) return;
        if (message.kind === "in" || message.kind === "out" || message.kind === "unsure") step.done(message.kind);
    };

    const value = useMemo(() => ({ ensure }), [ensure]);
    return (
        <WebSessionContext.Provider value={value}>
            {children}
            {step ? (
                <View style={{ position: "absolute", width: 1, height: 1, opacity: 0 }} pointerEvents="none">
                    <WebView
                        key={step.nonce}
                        source={{ html: BLANK_PAGE, baseUrl: step.server.web + "/" }}
                        originWhitelist={["*"]}
                        injectedJavaScript={step.script}
                        onMessage={onMessage}
                        onError={() => step.done("unsure")}
                        sharedCookiesEnabled
                        thirdPartyCookiesEnabled
                        domStorageEnabled
                        javaScriptEnabled
                    />
                </View>
            ) : null}
        </WebSessionContext.Provider>
    );
}

export function useWebSession(): WebSession {
    const value = useContext(WebSessionContext);
    if (!value) throw new Error("useWebSession outside WebSessionProvider");
    return value;
}

/** Whether a page that rides the cookie session may load yet: true once
 *  `ensure` has settled, whatever it found, so a page is never held back
 *  for good. Always true outside a live session. */
export function useWebSessionReady(active: boolean): boolean {
    const { ensure } = useWebSession();
    const [ready, setReady] = useState(false);
    useEffect(() => {
        if (!active) return;
        let live = true;
        setReady(false);
        void ensure().finally(() => {
            if (live) setReady(true);
        });
        return () => {
            live = false;
        };
    }, [active, ensure]);
    return ready || !active;
}
