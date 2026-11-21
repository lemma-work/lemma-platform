"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Problem, Screen } from "./frame";
import { EmailVerification, Session } from "./supertokens";
import { accountAccess, completionDestination } from "./completion";
import { authLink, pendingDestination, rememberDestination } from "./redirects";
import { appsDomainSuffix, PORTAL_PATH, siteOrigin } from "./config";
import { headingTo } from "./heading";
import { authFailure, sayProblem } from "./errors";
import { checkInbox, startVerification, type VerificationPhase } from "./verification";

const SENT_KEY = "lemma-app:auth:verification-sent";
const COOLDOWN = 60_000;

export function Verify() {
    const [phase, setPhase] = useState<VerificationPhase>("checking");
    const [said, setSaid] = useState<string | null>(null);
    const [retryAt, setRetryAt] = useState(0);
    const [now, setNow] = useState(() => Date.now());
    const started = useRef(false);
    const verifiedToken = useRef(false);
    const hasToken = Boolean(EmailVerification.getEmailVerificationTokenFromURL());

    const run = useCallback(async (verifyToken: boolean) => {
        setPhase("checking");
        setSaid(null);
        try {
            let recent = false;
            let userId: string | null = null;
            if (!verifyToken && await Session.doesSessionExist()) {
                userId = await Session.getUserId();
                try {
                    const stored: unknown = JSON.parse(sessionStorage.getItem(SENT_KEY) ?? "null");
                    if (stored && typeof stored === "object" && "userId" in stored && stored.userId === userId &&
                        "at" in stored && typeof stored.at === "number" && stored.at + COOLDOWN > Date.now()) {
                        recent = true;
                        setRetryAt(stored.at + COOLDOWN);
                    }
                } catch { /* Storage is optional; server resend limits still apply. */ }
            }
            const next = await startVerification(verifyToken && !verifiedToken.current, {
                access: accountAccess,
                verify: async () => {
                    const result = await EmailVerification.verifyEmail();
                    if (result.status === "OK") verifiedToken.current = true;
                    return result;
                },
                refresh: () => Session.attemptRefreshingSession(),
                send: async () => {
                    const result = await EmailVerification.sendVerificationEmail();
                    if (result.status === "OK") {
                        const at = Date.now();
                        setRetryAt(at + COOLDOWN);
                        try { sessionStorage.setItem(SENT_KEY, JSON.stringify({ userId, at })); }
                        catch { /* Resending remains limited in memory and by the server. */ }
                    }
                    return result;
                },
            }, recent);
            setPhase(next);
        } catch (error) {
            setSaid(error instanceof Response
                ? authFailure("verify", error.status, error.headers.get("retry-after"))
                : sayProblem(error));
            setPhase("problem");
        }
    }, []);

    useEffect(() => {
        if (started.current) return;
        started.current = true;
        rememberDestination(pendingDestination(window.location.search));
        void run(hasToken);
    }, [hasToken, run]);

    useEffect(() => {
        if (retryAt <= Date.now()) return;
        const timer = window.setInterval(() => setNow(Date.now()), 1000);
        return () => window.clearInterval(timer);
    }, [retryAt]);

    const continueOn = useCallback(async () => {
        try {
            await Session.attemptRefreshingSession();
            const access = await accountAccess();
            if (access === "verify") {
                setSaid("Your email is not verified yet. Open the link in your email, then try again.");
                return;
            }
            window.location.replace(completionDestination(access, window.location.search));
        } catch (error) { setSaid(sayProblem(error)); }
    }, []);

    useEffect(() => {
        if (phase !== "inbox") return;
        let live = true;
        let checking = false;
        const check = async () => {
            if (checking) return;
            checking = true;
            try {
                const next = await checkInbox({
                    verified: async () => (await EmailVerification.isEmailVerified()).isVerified,
                    refresh: () => Session.attemptRefreshingSession(),
                });
                if (live && next === "done") setPhase("done");
            } catch { /* The manual check reports failures and can be retried. */ }
            finally { checking = false; }
        };
        const timer = window.setInterval(() => { void check(); }, 3000);
        window.addEventListener("focus", check);
        return () => { live = false; window.clearInterval(timer); window.removeEventListener("focus", check); };
    }, [phase]);

    /* "Signed in" on the left only when it is so: a link opened in another
       browser verifies the address without signing anybody in there. */
    const [signedIn, setSignedIn] = useState(false);
    useEffect(() => {
        if (phase !== "done") return;
        let live = true;
        void Session.doesSessionExist().then((exists) => { if (live) setSignedIn(exists); }).catch(() => undefined);
        return () => { live = false; };
    }, [phase]);

    const waiting = Math.max(0, Math.ceil((retryAt - now) / 1000));
    const sendAgain = (label: string, primary: boolean) => (
        <button
            type="button"
            className={primary ? "b b--l b--primary" : "auth-link"}
            disabled={waiting > 0}
            onClick={() => void run(false)}
        >
            {label}{waiting > 0 && <> in <span className="auth-fig">{waiting}</span> s</>}
        </button>
    );
    const another = (
        <button type="button" className="auth-link" onClick={() => {
            void Session.signOut().then(() => window.location.replace(authLink(PORTAL_PATH)))
                .catch(error => setSaid(sayProblem(error)));
        }}>Use another account</button>
    );

    switch (phase) {
        case "checking":
            return <Screen title={hasToken ? "Checking your link…" : "Checking your email…"} />;
        case "done":
            return (
                <Screen
                    icon={{ glyph: "check", tone: "ok" }}
                    title="Your email is verified"
                    then={headingTo({ destination: pendingDestination(window.location.search), origin: siteOrigin(), appsSuffix: appsDomainSuffix() })}
                    speaker={signedIn ? { kind: "still", status: "Signed in", done: true } : undefined}
                >
                    <Problem said={said} />
                    <div className="auth-row">
                        <button type="button" className="b b--l b--primary" onClick={() => void continueOn()}>Continue</button>
                        <span className="auth-row__gap" />
                        {another}
                    </div>
                </Screen>
            );
        case "expired":
            return (
                <Screen icon={{ glyph: "warn", tone: "warn" }} title="That link has expired" lead="Send a new one and use the newest link.">
                    <Problem said={said} />
                    <div className="auth-row">
                        {sendAgain("Send a new link", true)}
                        <span className="auth-row__gap" />
                        {another}
                    </div>
                </Screen>
            );
        case "signed-out":
            return (
                <Screen icon={{ glyph: "mail" }} title="Sign in to verify your email" lead="Sign in with the account the link was for, and I’ll check it from there.">
                    <Problem said={said} />
                    <div className="auth-row">
                        <a className="b b--l b--primary" href={authLink(PORTAL_PATH)}>Sign in</a>
                    </div>
                </Screen>
            );
        case "problem":
            return (
                <Screen icon={{ glyph: "warn", tone: "warn" }} title="I couldn’t finish verifying your email">
                    <Problem said={said} />
                    <div className="auth-row">
                        {hasToken && <button type="button" className="b b--l b--primary" onClick={() => void run(true)}>Try the link again</button>}
                        {sendAgain("Send a new link", !hasToken)}
                        <span className="auth-row__gap" />
                        {another}
                    </div>
                </Screen>
            );
        default:
            return (
                <Screen icon={{ glyph: "mail" }} title="Check your email" lead="Open the link I sent you, then come back here.">
                    <Problem said={said} />
                    <div className="auth-row">
                        <button type="button" className="b b--l b--primary" onClick={() => void continueOn()}>I’ve opened it</button>
                        {sendAgain("Send it again", false)}
                    </div>
                    <div className="auth-row">{another}</div>
                    <p className="auth-small">It can take a minute. Check spam if it isn’t there.</p>
                </Screen>
            );
    }
}
