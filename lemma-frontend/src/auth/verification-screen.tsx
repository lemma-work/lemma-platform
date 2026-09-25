"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Screen } from "./screens";
import { EmailVerification, Session } from "./supertokens";
import { accountAccess, completionDestination } from "./completion";
import { authLink, pendingDestination, rememberDestination } from "./redirects";
import { PORTAL_PATH } from "./config";
import { authFailure, sayProblem } from "./errors";
import { startVerification, type VerificationPhase } from "./verification";

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
                if ((await EmailVerification.isEmailVerified()).isVerified) {
                    await Session.attemptRefreshingSession();
                    if (await accountAccess() === "ready" && live) setPhase("done");
                }
            } catch { /* The manual check reports failures and can be retried. */ }
            finally { checking = false; }
        };
        const timer = window.setInterval(() => { void check(); }, 3000);
        window.addEventListener("focus", check);
        return () => { live = false; window.clearInterval(timer); window.removeEventListener("focus", check); };
    }, [phase]);

    const waiting = Math.max(0, Math.ceil((retryAt - now) / 1000));
    const title = phase === "checking" ? "Checking your email verification…"
        : phase === "done" ? "Your email is verified"
        : phase === "expired" ? "That link has expired"
        : phase === "signed-out" ? "Sign in to verify your email"
        : phase === "problem" ? "We couldn’t finish verification" : "Check your email";

    return <Screen title={title} lead={phase === "inbox" ? "Open the verification link we sent you, then continue here." : undefined}>
        {said && <p className="auth__problem" role="alert">{said}</p>}
        <div className="screen__actions">
            {phase === "done" && <button className="btn btn--primary" onClick={() => void continueOn()}>Continue</button>}
            {phase === "inbox" && <button className="btn btn--primary" onClick={() => void continueOn()}>I’ve verified my email</button>}
            {(phase === "inbox" || phase === "expired" || phase === "problem") &&
                <button className="btn" disabled={waiting > 0} onClick={() => void run(false)}>
                    {waiting > 0 ? `Send again in ${waiting}s` : "Send verification email"}
                </button>}
            {phase === "problem" && hasToken && <button className="btn" onClick={() => void run(true)}>Retry verification</button>}
            {phase === "signed-out" && <a className="screen__aside" href={authLink(PORTAL_PATH)}>Sign in</a>}
            {phase !== "checking" && phase !== "signed-out" && <button className="linkish" onClick={() => {
                void Session.signOut().then(() => window.location.replace(authLink(PORTAL_PATH)))
                    .catch(error => setSaid(sayProblem(error)));
            }}>Use another account</button>}
        </div>
    </Screen>;
}
