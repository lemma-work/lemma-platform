"use client";

import { useEffect, useLayoutEffect, useRef, useState, type ClipboardEvent, type FormEvent, type KeyboardEvent } from "react";
import { EmailCodeError, mintEmailNonce, resendEmailCode, continueEmail, verifyEmailCode, type EmailChallenge } from "./email-code";
import { completeAuth } from "./completion";
import { continueWithProvider } from "./provider-login";
import { sayProblem } from "./errors";
import { Glyph } from "./marks";
import type { ProviderId } from "./login-methods";

/** Signing in by a code sent to the address -- the default way in, and how
 *  every new account starts.
 *
 *  A hook rather than a form, because the steps are whole screens now: the
 *  email under "Welcome back", then "Check your email" with the six boxes, or
 *  "This email signs in with Google". The screen that holds it decides the
 *  title and the left side; this keeps the exchange (`email-code.ts`) exactly
 *  as it was.
 */
export interface EmailCode {
    email: string;
    setEmail: (next: string) => void;
    /** Which screen the exchange is on. */
    stage: "email" | "code" | "provider";
    /** The provider an address signs in with, once the server has said. */
    provider: ProviderId | null;
    busy: boolean;
    /** Busy checking a code, rather than sending one. */
    checking: boolean;
    authenticated: boolean;
    said: string | null;
    /** What is wrong with the code, in the screen's own words. */
    problem: "mismatch" | "expired" | null;
    /** A new code went out just now. */
    resent: boolean;
    /** Seconds before another code may be asked for. */
    cooldown: number;
    /** Bumped when the boxes should empty and take the caret again. */
    fresh: number;
    /** Ask for a code for this address (or the one typed). */
    start: (address?: string) => void;
    verify: (code: string) => void;
    resend: () => void;
    /** After a code went through but the page couldn't be left: finish
     *  without sending the code again. Or go on to the address's provider. */
    retry: () => void;
    changeEmail: () => void;
}

type Action = "continue" | "verify" | "resend";

export function useEmailCode({ onAttempt, onPassword }: { onAttempt: () => void; onPassword: (email: string) => void }): EmailCode {
    const [email, setEmail] = useState("");
    const [challenge, setChallenge] = useState<EmailChallenge | null>(null);
    const nonce = useRef<string | null>(null);
    const abandoned = useRef<string | null>(null);
    const [provider, setProvider] = useState<ProviderId | null>(null);
    const active = useRef(false);
    const [doing, setDoing] = useState<Action | null>(null);
    const [needsNewCode, setNeedsNewCode] = useState(false);
    const [mismatch, setMismatch] = useState(false);
    const [resent, setResent] = useState(false);
    const [authenticated, setAuthenticated] = useState(false);
    const [said, setSaid] = useState<string | null>(null);
    const [retryAt, setRetryAt] = useState(0);
    const [now, setNow] = useState(() => Date.now());
    const [fresh, setFresh] = useState(0);

    useEffect(() => {
        const timer = window.setInterval(() => setNow(Date.now()), 1000);
        return () => window.clearInterval(timer);
    }, []);

    const cooldown = Math.max(0, Math.ceil((retryAt - now) / 1000));
    const expired = needsNewCode || (challenge !== null && Date.parse(challenge.expires_at) <= now);

    async function attempt(action: Action, value = "", address = email) {
        if (active.current) return;
        onAttempt();
        active.current = true;
        setDoing(action);
        setSaid(null);
        setMismatch(false);
        setResent(false);
        let checked = false;
        try {
            // A consumed code must never be submitted again when navigation failed.
            if (authenticated) { await completeAuth(); return; }
            if (provider) { await continueWithProvider(provider); return; }
            const binding = nonce.current ?? await mintEmailNonce();
            nonce.current = binding;
            if (challenge && action === "verify") {
                await verifyEmailCode(challenge.challenge_id, binding, value);
                checked = true;
                setAuthenticated(true);
                await completeAuth();
            } else {
                const next = challenge
                    ? await resendEmailCode(challenge.challenge_id, binding)
                    : await continueEmail(address, binding, abandoned.current);
                abandoned.current = null;
                if ("method" in next) {
                    if (next.method === "password") { onPassword(address.trim()); return; }
                    if (next.method === "thirdparty") { setProvider(next.provider); return; }
                }
                if (challenge) setResent(true);
                setChallenge(next);
                setNeedsNewCode(false);
                setFresh((count) => count + 1);
                const at = Date.now();
                setNow(at);
                setRetryAt(at + 60_000);
            }
        } catch (error) {
            const code = error instanceof EmailCodeError ? error.code : null;
            /* The two refusals a person meets most are said in the screen's
               own words beside the boxes; everything else is the server's
               sentence, as before. */
            if (code === "EMAIL_CODE_INVALID") setMismatch(true);
            else if (code !== "EMAIL_CODE_EXPIRED") setSaid(sayProblem(error));
            if (code === "EMAIL_CODE_EXPIRED") setNeedsNewCode(true);
            if (error instanceof EmailCodeError && error.retryAfter) setRetryAt(Date.now() + error.retryAfter * 1000);
            if (code === "EMAIL_LOGIN_EXPIRED") {
                nonce.current = null;
                setChallenge(null);
                setNeedsNewCode(false);
                abandoned.current = null;
                setRetryAt(0);
            }
            /* A code that was checked and refused leaves the boxes empty: with
               all six still filled, the first key pressed would send five old
               digits and one new one. */
            if (action === "verify" && !authenticated && !checked) setFresh((count) => count + 1);
        } finally {
            active.current = false;
            setDoing(null);
        }
    }

    return {
        email,
        setEmail,
        stage: provider ? "provider" : challenge ? "code" : "email",
        provider,
        busy: doing !== null,
        checking: doing === "verify",
        authenticated,
        said,
        problem: authenticated ? null : expired ? "expired" : mismatch ? "mismatch" : null,
        resent,
        cooldown,
        fresh,
        start: (address) => {
            if (address !== undefined) setEmail(address);
            void attempt("continue", "", address ?? email);
        },
        verify: (value) => void attempt("verify", value),
        resend: () => void attempt("resend"),
        retry: () => void attempt("verify"),
        changeEmail: () => {
            if (challenge) abandoned.current = challenge.challenge_id;
            setChallenge(null);
            setProvider(null);
            setNeedsNewCode(false);
            setMismatch(false);
            setResent(false);
            setSaid(null);
            setRetryAt(0);
        },
    };
}

/** "Check your email": the six boxes, what went wrong with them, and the way
 *  to another code. */
export function CodeEntry({ code }: { code: EmailCode }) {
    const resend = code.cooldown > 0 || code.busy
        ? <>send a new code</>
        : <button type="button" className="auth-link" onClick={code.resend}>send a new code</button>;
    return (
        <>
            <CodeBoxes
                fresh={code.fresh}
                bad={code.problem !== null}
                disabled={code.busy || code.authenticated || code.problem === "expired"}
                onComplete={code.verify}
            />
            {code.problem === "mismatch" && (
                <p className="auth-problem" role="alert">
                    <Glyph name="warn" />
                    <span>That code didn’t match. Check the email for the newest one, or {resend}.</span>
                </p>
            )}
            {code.problem === "expired" && (
                <p className="auth-problem" role="alert">
                    <Glyph name="warn" />
                    <span>That code has expired. {code.cooldown > 0 || code.busy ? "Send a new code" : (
                        <button type="button" className="auth-link" onClick={code.resend}>Send a new code</button>
                    )}.</span>
                </p>
            )}
            {code.said && (
                <p className="auth-problem" role="alert"><Glyph name="warn" /><span>{code.said}</span></p>
            )}
            {code.resent && !code.said && (
                <p className="auth-ok" role="status"><Glyph name="check" />New code sent. The old one won’t work now.</p>
            )}
            {code.authenticated ? (
                code.said && (
                    <button type="button" className="b b--l b--primary auth-wide" disabled={code.busy} onClick={code.retry}>
                        Continue
                    </button>
                )
            ) : (
                <>
                    <div className="auth-row">
                        {/* Not a live region: a clock read aloud every second
                            is noise. */}
                        {code.cooldown > 0
                            ? <span className="auth-small">Send a new code in <span className="auth-fig">{code.cooldown}</span> s</span>
                            /* An expired code already offers one, in the sentence that says so. */
                            : code.problem !== "expired" && <button type="button" className="auth-link" disabled={code.busy} onClick={code.resend}>Send a new code</button>}
                        <span className="auth-row__gap" />
                        <button type="button" className="auth-link" disabled={code.busy} onClick={code.changeEmail}>Use another email</button>
                    </div>
                    <p className="auth-small">It can take a minute. Check spam if it isn’t there.</p>
                </>
            )}
        </>
    );
}

const EMPTY = ["", "", "", "", "", ""];

/** Six boxes, filled by typing or pasting, sent when the sixth is in.
 *
 *  The first box carries `one-time-code`, so a phone can offer the code from
 *  the email and fill it; that arrives as all six digits in one box and is
 *  spread across them, as a paste is. No `maxlength`, for the same reason: a
 *  limit of one would cut the phone's code to its first digit. */
function CodeBoxes({ fresh, bad, disabled, onComplete }: {
    fresh: number; bad: boolean; disabled: boolean; onComplete: (code: string) => void;
}) {
    const [digits, setDigits] = useState<string[]>(EMPTY);
    const boxes = useRef<Array<HTMLInputElement | null>>([]);
    const seen = useRef(fresh);

    /* Before the next key can land: emptied after it, a digit typed quickly
       would be wiped. */
    useLayoutEffect(() => {
        if (fresh === seen.current) return;
        seen.current = fresh;
        setDigits(EMPTY);
        boxes.current[0]?.focus();
    }, [fresh]);

    const put = (at: number, typed: string) => {
        const next = [...digits];
        if (typed.length <= 1) {
            next[at] = typed;
        } else {
            /* A whole code goes in from the first box wherever it was pasted;
               a few digits go in from the box they were pasted into. */
            const from = typed.length >= 6 ? 0 : at;
            typed.slice(0, 6 - from).split("").forEach((digit, offset) => { next[from + offset] = digit; });
        }
        setDigits(next);
        const gap = next.findIndex((digit) => !digit);
        if (typed.length === 1 && at < 5) boxes.current[at + 1]?.focus();
        else if (typed.length > 1) boxes.current[gap === -1 ? 5 : gap]?.focus();
        if (gap === -1) onComplete(next.join(""));
    };

    const keyed = (at: number, event: KeyboardEvent<HTMLInputElement>) => {
        if (/^\d$/.test(event.key) && !event.metaKey && !event.ctrlKey && !event.altKey) {
            /* Typed over whatever the box held, rather than beside it. */
            event.preventDefault();
            put(at, event.key);
        } else if (event.key === "Backspace" && !digits[at] && at > 0) {
            event.preventDefault();
            boxes.current[at - 1]?.focus();
        } else if (event.key === "ArrowLeft" && at > 0) {
            event.preventDefault();
            boxes.current[at - 1]?.focus();
        } else if (event.key === "ArrowRight" && at < 5) {
            event.preventDefault();
            boxes.current[at + 1]?.focus();
        }
    };

    const changed = (at: number, input: HTMLInputElement) => {
        let typed = input.value.replace(/\D/g, "");
        /* A phone's keyboard reaches here rather than through `keyed`, and
           into a box that already held a digit it adds a second: keep the one
           just typed, which is the one before the caret. */
        if (typed.length === 2 && digits[at] && input.value.length === 2) {
            typed = input.value.charAt(Math.max(0, (input.selectionStart ?? 2) - 1)).replace(/\D/g, "");
        }
        put(at, typed);
    };

    const pasted = (at: number, event: ClipboardEvent<HTMLInputElement>) => {
        event.preventDefault();
        const typed = event.clipboardData.getData("text").replace(/\D/g, "");
        if (typed) put(at, typed);
    };

    const submit = (event: FormEvent) => {
        event.preventDefault();
        if (digits.every(Boolean)) onComplete(digits.join(""));
    };

    return (
        <form onSubmit={submit} noValidate>
            <div className="auth-code" role="group" aria-label="The 6-digit code" data-bad={bad ? "" : undefined}>
                {digits.map((digit, at) => (
                    <input
                        key={at}
                        ref={(box) => { boxes.current[at] = box; }}
                        value={digit}
                        inputMode="numeric"
                        pattern="[0-9]*"
                        autoComplete={at === 0 ? "one-time-code" : "off"}
                        autoFocus={at === 0}
                        aria-label={"Digit " + (at + 1)}
                        aria-invalid={bad ? true : undefined}
                        disabled={disabled}
                        onFocus={(event) => event.target.select()}
                        onKeyDown={(event) => keyed(at, event)}
                        onChange={(event) => changed(at, event.target)}
                        onPaste={(event) => pasted(at, event)}
                    />
                ))}
            </div>
        </form>
    );
}
