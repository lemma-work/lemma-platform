"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { EmailCodeError, mintEmailNonce, resendEmailCode, continueEmail, verifyEmailCode, type EmailChallenge } from "./email-code";
import { completeAuth } from "./completion";
import { continueWithProvider } from "./provider-login";
import { sayProblem } from "./errors";

export function EmailCodeForm({ onAttempt, onPassword }: { onAttempt: () => void; onPassword: (email: string) => void }) {
    const [email, setEmail] = useState("");
    const [code, setCode] = useState("");
    const [challenge, setChallenge] = useState<EmailChallenge | null>(null);
    const nonce = useRef<string | null>(null);
    const abandoned = useRef<string | null>(null);
    const [provider, setProvider] = useState<"google" | "active-directory" | null>(null);
    const active = useRef(false);
    const [busy, setBusy] = useState(false);
    const [needsNewCode, setNeedsNewCode] = useState(false);
    const [authenticated, setAuthenticated] = useState(false);
    const [said, setSaid] = useState<string | null>(null);
    const [retryAt, setRetryAt] = useState(0);
    const [now, setNow] = useState(() => Date.now());

    useEffect(() => {
        const timer = window.setInterval(() => setNow(Date.now()), 1000);
        return () => window.clearInterval(timer);
    }, []);

    const cooldown = Math.max(0, Math.ceil((retryAt - now) / 1000));
    const expired = needsNewCode || (challenge !== null && Date.parse(challenge.expires_at) <= now);

    async function attempt(action: "submit" | "resend", event?: FormEvent) {
        event?.preventDefault();
        if (active.current) return;
        onAttempt();
        active.current = true;
        setBusy(true);
        setSaid(null);
        try {
            // A consumed code must never be submitted again when navigation failed.
            if (authenticated) { await completeAuth(); return; }
            if (provider) { await continueWithProvider(provider); return; }
            const binding = nonce.current ?? await mintEmailNonce();
            nonce.current = binding;
            if (challenge && action === "submit") {
                await verifyEmailCode(challenge.challenge_id, binding, code);
                setAuthenticated(true);
                await completeAuth();
            } else {
                const next = challenge
                    ? await resendEmailCode(challenge.challenge_id, binding)
                    : await continueEmail(email, binding, abandoned.current);
                abandoned.current = null;
                if ("method" in next) {
                    if (next.method === "password") { onPassword(email.trim()); return; }
                    if (next.method === "thirdparty") { setProvider(next.provider); return; }
                }
                setChallenge(next);
                setNeedsNewCode(false);
                setCode("");
                const at = Date.now();
                setNow(at);
                setRetryAt(at + 60_000);
            }
        } catch (error) {
            setSaid(sayProblem(error));
            if (error instanceof EmailCodeError && error.code === "EMAIL_CODE_EXPIRED") setNeedsNewCode(true);
            if (error instanceof EmailCodeError && error.retryAfter) setRetryAt(Date.now() + error.retryAfter * 1000);
            if (error instanceof EmailCodeError && error.code === "EMAIL_LOGIN_EXPIRED") {
                nonce.current = null;
                setChallenge(null);
                setNeedsNewCode(false);
                abandoned.current = null;
                setRetryAt(0);
            }
        } finally {
            active.current = false;
            setBusy(false);
        }
    }

    return <form onSubmit={event => void attempt("submit", event)}>
        {provider ? <p className="auth__note">This email signs in with {provider === "google" ? "Google" : "Microsoft"}. Continue with that provider to sign in.</p> : challenge ? <>
            <p className="auth__note">Enter the six-digit code sent to {email.trim()}. You have three attempts.</p>
            <div className="field">
                <label htmlFor="email-code">Verification code</label>
                <input id="email-code" value={code} onChange={event => setCode(event.target.value)}
                    inputMode="numeric" autoComplete="one-time-code" maxLength={6} pattern="[0-9]{6}"
                    required={!authenticated} disabled={authenticated} autoFocus />
            </div>
            {expired && !authenticated && <p className="auth__note" role="status">This code can no longer be used. Request a new code below.</p>}
        </> : <div className="field">
            <label htmlFor="code-email">Email</label>
            <input id="code-email" type="email" autoComplete="email" value={email}
                onChange={event => setEmail(event.target.value)} required autoFocus />
            <p className="auth__note">Continue to sign in or create your account. New accounts receive an email code.</p>
        </div>}
        {said && <p className="auth__problem" role="alert">{said}</p>}
        <div className="screen__actions">
            <button className="btn btn--primary" disabled={busy || (!authenticated && !provider && (challenge ? expired : cooldown > 0))}>
                {busy ? "One moment…" : authenticated ? "Continue" : provider ? `Continue with ${provider === "google" ? "Google" : "Microsoft"}` : challenge ? "Verify and continue" : "Continue with email"}
            </button>
            {challenge && !authenticated && <>
                <button className="btn" type="button" disabled={busy || cooldown > 0} onClick={() => void attempt("resend")}>
                    {cooldown > 0 ? `Resend in ${cooldown}s` : "Resend code"}
                </button>
                <button className="linkish" type="button" disabled={busy} onClick={() => {
                    abandoned.current = challenge.challenge_id;
                    setChallenge(null); setNeedsNewCode(false); setCode(""); setSaid(null); setRetryAt(0);
                }}>Change email</button>
            </>}
            {provider && <button className="linkish" type="button" disabled={busy} onClick={() => { setProvider(null); setSaid(null); }}>Change email</button>}
        </div>
        {!challenge && cooldown > 0 && <p className="auth__note" role="status">You can request another code in {cooldown}s.</p>}
    </form>;
}
