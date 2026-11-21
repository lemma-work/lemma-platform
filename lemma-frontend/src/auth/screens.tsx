"use client";

import { LoadingIndicator } from "@/ui/loading";

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { EmailPassword, ThirdParty } from "./supertokens";
import { authFailure, isExistingAccount, sayProblem, type Attempt } from "./errors";
import { appsDomainSuffix, DEFAULT_LANDING, PORTAL_PATH, siteOrigin } from "./config";
import { authLink, rememberDestination, pendingDestination, destinationFrom, asksForDestination, installsTemplate, addsToSlack, invitationIn } from "./redirects";
import { accountAccess, completeAuth, completionDestination } from "./completion";
import { forgetReturns, noteReturn, wouldLoop } from "./return-loop";
import { captureSignInLoop } from "@/site/analytics/client";
import { continueWithProvider } from "./provider-login";
import { heldCliRequest } from "./cli-login";
import { heldConsentRequest } from "./mcp-consent";
import { heldRequestId } from "@/desktop/auth-handoff";
import { CodeEntry, useEmailCode } from "./email-code-form";
import { isLocalDeployment } from "@/site/config";
import { capitalised, useThisComputer } from "@/desktop/this-computer";
import { crowd, waitingFor } from "./waiting";
import { headingTo } from "./heading";
import { Glyph, GoogleMark, MicrosoftMark } from "./marks";
import { fetchConfiguredProviders, type ProviderId } from "./login-methods";
import { portalTrip } from "@/session/portal-trip";
import { Chip, Problem, Screen, type Speaker } from "./frame";

/* The frame lives in `frame.tsx`; Lemma Desktop's and Slack's screens still
   import it from here. */
export { Screen } from "./frame";

/** The one sentence the cast stands over, for somebody who does not know yet
 *  what a Lemma is. */
const CAST_CAPTION = "Each Lemma looks after one part of your work.";

/* ── fields ─────────────────────────────────────────────────────────── */

/** A field's own complaint, from the server.
 *
 *  SuperTokens answers `FIELD_ERROR` with one entry per input rather than a
 *  sentence, and those are the useful refusals — "this email already has an
 *  account", the password rules. Shown against the input they are about,
 *  because a password rule printed at the top of a form is a rule somebody
 *  reads after they have already failed it. */
type FieldSaid = Record<string, string>;

function fieldErrors(formFields: { id: string; error: string }[] | undefined): FieldSaid {
    const said: FieldSaid = {};
    for (const field of formFields ?? []) said[field.id] = field.error;
    return said;
}

/** One big underlined input with its label above it. A password gets Show
 *  and Hide at its end. */
function Field({ id, label, type, value, onChange, said, autoComplete, autoFocus, placeholder }: {
    id: string; label: string; type: string; value: string;
    onChange: (next: string) => void; said?: string; autoComplete?: string; autoFocus?: boolean; placeholder?: string;
}) {
    const input = useRef<HTMLInputElement>(null);
    const secret = type === "password";
    const [shown, setShown] = useState(false);

    /* Hidden again the moment the form goes. A password left readable on the
       screen after "Make my account" is one somebody walks away from, and a
       password manager deciding whether to offer to save it looks for a
       password field, not a text one. */
    useEffect(() => {
        const form = input.current?.form;
        if (!secret || !form) return;
        const hide = () => setShown(false);
        form.addEventListener("submit", hide);
        return () => form.removeEventListener("submit", hide);
    }, [secret]);

    /* Changing an input's type drops its selection, so somebody who stopped
       mid-word to check a character would find the caret thrown to the start. */
    const caret = useRef<[number | null, number | null] | null>(null);
    const reveal = () => {
        const field = input.current;
        caret.current = field && document.activeElement === field ? [field.selectionStart, field.selectionEnd] : null;
        setShown((now) => !now);
    };
    useLayoutEffect(() => {
        const field = input.current;
        const at = caret.current;
        caret.current = null;
        if (!field || !at) return;
        field.setSelectionRange(...at);
        /* And again a frame later: Chrome rebuilds the field's editor on the
           layout after a type change, and on a real click that rebuild lands
           after this effect and puts the caret back at zero. */
        const frame = requestAnimationFrame(() => field.setSelectionRange(...at));
        return () => cancelAnimationFrame(frame);
    }, [shown]);

    return (
        <div className="auth-big" data-secret={secret ? "" : undefined}>
            <label htmlFor={id}>{label}</label>
            <div className="auth-big__box">
                <input
                    ref={input}
                    id={id}
                    type={secret && shown ? "text" : type}
                    value={value}
                    placeholder={placeholder}
                    autoComplete={autoComplete}
                    autoFocus={autoFocus}
                    /* Shown as text, a password is still not prose: a
                       spellchecker that underlines it, or sends it off to be
                       checked, is the wrong reader. */
                    spellCheck={secret ? false : undefined}
                    autoCapitalize={secret || type === "email" ? "off" : undefined}
                    autoCorrect={secret || type === "email" ? "off" : undefined}
                    aria-invalid={said ? true : undefined}
                    aria-describedby={said ? id + "-said" : undefined}
                    onChange={(event) => onChange(event.target.value)}
                />
                {secret && (
                    /* Pressing it leaves the caret where it was: the mousedown
                       is swallowed so the input never blurs. It stays
                       reachable by Tab. */
                    <button
                        type="button"
                        className="auth-big__reveal"
                        aria-controls={id}
                        aria-label={shown ? "Hide password" : "Show password"}
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={reveal}
                    >
                        {shown ? "Hide" : "Show"}
                    </button>
                )}
            </div>
            {said && <span className="auth-big__said" id={id + "-said"}>{said}</span>}
        </div>
    );
}

/* ── where to, once in ──────────────────────────────────────────────── */

/** Where to go once somebody is in.
 *
 *  A refused destination is said out loud rather than swapped. The whole
 *  reason the portal moved here is that the old one substituted its default in
 *  silence, so somebody signed in successfully and landed somewhere they had
 *  not asked for with nothing on screen explaining it. */
function useLanding() {
    const search = typeof window === "undefined" ? "" : window.location.search;
    const asked = asksForDestination(search);
    const allowed = destinationFrom(search);
    return {
        go: () => completeAuth(search),
        refused: asked && !allowed,
    };
}

function Refused() {
    return (
        <p className="auth-note">
            The link that sent you here asked to go on somewhere Lemma doesn’t recognise, so it won’t.
            You’ll land in your workspace instead.
        </p>
    );
}

/** What this sign-in is on its way to, read once for the visit: who to put on
 *  the left, and what to say under the title. */
function useArriving() {
    const [arriving] = useState(() => {
        const destination = pendingDestination(window.location.search);
        const origin = siteOrigin();
        const waiting = waitingFor(destination, destination ?? "lemma");
        return {
            installing: installsTemplate(destination, origin) || addsToSlack(destination, origin),
            invited: invitationIn(destination, origin) !== null,
            waiting,
            /* Three strangers when the link named nobody; when it named a
               Lemma, that one plus two more for the picture's sidebar. */
            crowd: waiting.id ? crowd(waiting.id, 3) : waiting.faces,
            then: headingTo({
                destination,
                origin,
                appsSuffix: appsDomainSuffix(),
                desktop: heldRequestId() !== null,
                cli: heldCliRequest() !== null,
                consent: heldConsentRequest() !== null,
            }),
        };
    });
    return arriving;
}

/** Stopped instead of sending a signed-in person back to an app a third time.
 *
 *  Trying again is the first action because the cause is often a state the app
 *  repairs once on its next load. The footnote is the fix that works when it
 *  does not: everything the app keeps about the session lives on its own host,
 *  so clearing that host's data is the reset. */
function ReturnLoop({ destination }: { destination: string }) {
    const host = new URL(destination, siteOrigin()).host;
    return (
        <Screen
            icon={{ glyph: "warn", tone: "warn" }}
            title={host + " isn’t picking up your sign-in"}
            lead="You’re signed in, but this app keeps sending you back here. I’ve stopped the loop."
            footer={<>Still stuck? Clear this browser{"’"}s site data for {host}, then open it again.</>}
        >
            <div className="auth-row">
                <button className="b b--l b--primary" onClick={() => {
                    forgetReturns(destination);
                    window.location.replace(destination);
                }}>
                    Try again
                </button>
                <button className="b b--l b--secondary" onClick={() => window.location.assign(DEFAULT_LANDING)}>
                    Open your workspace
                </button>
            </div>
        </Screen>
    );
}

/* ── the providers ──────────────────────────────────────────────────── */

/** Google and Microsoft, which are the two the backend can register — see
 *  `build_thirdparty_providers`, where each is conditional on being
 *  configured. Only the ones this deployment registered are drawn: it says
 *  which through SuperTokens' `/loginmethods` (`login-methods.ts`). */
const PROVIDERS: { id: ProviderId; name: string; Mark: typeof GoogleMark }[] = [
    { id: "google", name: "Google", Mark: GoogleMark },
    { id: "active-directory", name: "Microsoft", Mark: MicrosoftMark },
];

function providerOf(id: string | null | undefined) {
    return PROVIDERS.find((provider) => provider.id === id) ?? null;
}

/** The configured providers, or null until the deployment has said. Nothing
 *  is drawn while asking, so a button never appears and then vanishes. */
function useConfiguredProviders(): ProviderId[] | null {
    const [configured, setConfigured] = useState<ProviderId[] | null>(null);
    useEffect(() => {
        let live = true;
        void fetchConfiguredProviders().then((ids) => { if (live) setConfigured(ids); });
        return () => { live = false; };
    }, []);
    return configured;
}

function Providers({ configured, onProblem, onLeaving }: {
    configured: ProviderId[]; onProblem: (said: string) => void; onLeaving: (leaving: boolean) => void;
}) {
    const [going, setGoing] = useState<string | null>(null);

    const leave = useCallback(async (thirdPartyId: string) => {
        setGoing(thirdPartyId);
        onLeaving(true);
        try {
            await continueWithProvider(thirdPartyId);
        } catch (error) {
            setGoing(null);
            onLeaving(false);
            onProblem(sayProblem(error));
        }
    }, [onProblem, onLeaving]);

    return (
        <div className="auth-stack">
            {PROVIDERS.filter((provider) => configured.includes(provider.id)).map((provider) => (
                <button
                    key={provider.id}
                    type="button"
                    className="b b--l b--secondary auth-wide"
                    disabled={going !== null}
                    onClick={() => void leave(provider.id)}
                >
                    <provider.Mark />
                    {going === provider.id ? <LoadingIndicator inline label={"Connecting to " + provider.name} /> : "Continue with " + provider.name}
                </button>
            ))}
        </div>
    );
}

/* ── sign in and sign up ────────────────────────────────────────────── */

export function SignInUp({ mode }: { mode: "in" | "up" }) {
    /* A local install sends no mail until someone sets it up, so a code it
       emails may never arrive: a password is the way in that always works
       there, and the code stays one click away. */
    const [usePassword, setUsePassword] = useState(() => isLocalDeployment());
    const [existingPassword, setExistingPassword] = useState(false);
    /* Whether the address is decided -- typed before switching to a password,
       or the server said it has one -- so the password asks on its own, under
       the address in a chip. */
    const [settled, setSettled] = useState(false);
    const signingIn = mode === "in" || existingPassword;
    const attempted = useRef(false);
    const [password, setPassword] = useState("");
    const [said, setSaid] = useState<string | null>(null);
    const [wrongPassword, setWrongPassword] = useState(false);
    const [fields, setFields] = useState<FieldSaid>({});
    /* Sign-up for an address that already has a password. Its own state rather
       than a field error, because the answer is a way forward -- sign in --
       not something to correct in what was typed. */
    const [hasAccount, setHasAccount] = useState(false);
    const [busy, setBusy] = useState(false);
    const [authenticated, setAuthenticated] = useState(false);
    const [leaving, setLeaving] = useState(false);
    const [loopingTo, setLoopingTo] = useState<string | null>(null);
    const configured = useConfiguredProviders();
    const { go, refused } = useLanding();
    const arriving = useArriving();
    const code = useEmailCode({
        onAttempt: () => { attempted.current = true; },
        onPassword: () => {
            setExistingPassword(true); setUsePassword(true); setSettled(true); setSaid(null);
        },
    });
    const email = code.email;

    const attempt: Attempt = signingIn ? "sign-in" : "sign-up";
    const other = authLink(mode === "in" ? PORTAL_PATH + "/signup" : PORTAL_PATH);

    useEffect(() => {
        let live = true;
        rememberDestination(pendingDestination(window.location.search));
        void accountAccess().then(access => {
            /* Signed out here too, so this trip is not one half of a loop: the
               workspace's "couldn't finish signing you in" is for a tab this
               page sent back, not for someone who pressed Back on its form. */
            if (access === "signed-out") portalTrip.clear();
            if (!live || attempted.current || access === "signed-out") return;
            const destination = completionDestination(access, window.location.search);
            if (access === "ready" && wouldLoop(destination)) {
                setLoopingTo(destination);
                captureSignInLoop();
                return;
            }
            if (access === "ready") noteReturn(destination);
            window.location.replace(destination);
        }).catch(error => { if (live && !attempted.current) setSaid(sayProblem(error)); });
        return () => { live = false; };
    }, []);

    const submit = useCallback(async (event: FormEvent) => {
        event.preventDefault();
        attempted.current = true;
        setSaid(null);
        setWrongPassword(false);
        setFields({});
        setHasAccount(false);
        setBusy(true);
        try {
            if (authenticated) { await go(); return; }
            const formFields = [{ id: "email", value: email.trim() }, { id: "password", value: password }];
            const answer = signingIn
                ? await EmailPassword.signIn({ formFields })
                : await EmailPassword.signUp({ formFields });

            if (answer.status === "OK") { setAuthenticated(true); await go(); return; }
            if (answer.status === "FIELD_ERROR") {
                const complaints = fieldErrors(answer.formFields);
                if (!signingIn && isExistingAccount(complaints.email)) {
                    delete complaints.email;
                    setHasAccount(true);
                }
                setFields(complaints);
                setBusy(false);
                return;
            }
            if (answer.status === "WRONG_CREDENTIALS_ERROR") {
                setWrongPassword(true);
                setBusy(false);
                return;
            }

            setSaid(("reason" in answer && answer.reason) ? String(answer.reason) : "That couldn’t be finished. Try again.");
            setBusy(false);
        } catch (error) {
            setBusy(false);
            /* A `Response` reaches here when the API refused before SuperTokens
               could read it — the rate limiter and the proof-of-work both do
               that — so it is read for what it is rather than printed. */
            if (error instanceof Response) {
                const body = await error.clone().json().catch(() => ({}));
                setSaid(authFailure(attempt, error.status, error.headers.get("retry-after"), (body as { message?: string }).message ?? ""));
                return;
            }
            setSaid(sayProblem(error));
        }
    }, [email, password, signingIn, go, attempt, authenticated]);

    if (loopingTo) return <ReturnLoop destination={loopingTo} />;

    /* While something is being checked the left says so, and once it went
       through it says that; otherwise it is whoever this door is for. */
    const signedIn = authenticated || code.authenticated;
    const checking = leaving || code.checking || (busy && !authenticated);
    const progress: Speaker | null = signedIn
        ? { kind: "still", status: "Signed in", done: true }
        : checking
            ? { kind: "still", status: signingIn || leaving || code.checking ? "Signing you in" : "Making your account", busy: true }
            : null;
    const STILL: Speaker = { kind: "still" };

    /* ── the code, once one was sent ── */
    if (code.stage === "code") {
        return (
            <Screen title="Check your email" lead={<>I sent a 6-digit code to <b>{email.trim()}</b>.</>} speaker={progress ?? STILL}>
                <CodeEntry code={code} />
            </Screen>
        );
    }

    /* ── an address that signs in with a provider ── */
    const provider = providerOf(code.provider);
    if (code.stage === "provider" && provider) {
        return (
            <Screen
                title={"This email signs in with " + provider.name}
                lead={<>Continue with {provider.name} to sign in as <b>{email.trim()}</b>.</>}
                speaker={progress ?? STILL}
            >
                <form className="auth-stack" onSubmit={(event) => { event.preventDefault(); code.retry(); }}>
                    <button className="b b--l b--primary auth-wide" disabled={code.busy}>
                        <provider.Mark />
                        {code.busy ? <LoadingIndicator inline label={"Connecting to " + provider.name} /> : "Continue with " + provider.name}
                    </button>
                </form>
                <Problem said={code.said} />
                <div className="auth-row">
                    <button type="button" className="auth-link" disabled={code.busy} onClick={code.changeEmail}>Use another email</button>
                </div>
            </Screen>
        );
    }

    const emailLabel = arriving.invited ? "The email you were invited at" : mode === "up" ? "Your work email" : "Your email";

    const toCode = () => {
        setUsePassword(false); setSettled(false); setExistingPassword(false);
        setSaid(null); setWrongPassword(false); setHasAccount(false); setFields({});
    };

    const passwordForm = (chip: boolean) => (
        <form className="auth-stack auth-stack--form" onSubmit={submit} noValidate>
            {chip ? (
                <>
                    <Chip email={email.trim()} action={
                        <button type="button" className="auth-link" disabled={busy} onClick={() => {
                            /* Back where the address was typed: the code, if
                               the server sent them here; the password form,
                               if they chose it. */
                            if (existingPassword) setUsePassword(false);
                            setSettled(false); setExistingPassword(false);
                            setSaid(null); setWrongPassword(false); setHasAccount(false); setFields({});
                        }}>Change</button>
                    } />
                    {/* For a password manager, which files a password under
                        the username beside it. */}
                    <input type="email" name="username" autoComplete="username" value={email.trim()} readOnly hidden />
                    {fields.email && <Problem said={fields.email} />}
                </>
            ) : (
                <Field id="email" label={emailLabel} type="email" autoComplete="email" autoFocus={!email}
                    placeholder="you@company.com" value={email} onChange={code.setEmail} said={fields.email} />
            )}
            <Field id="password" label="Password" type="password" autoFocus={chip || Boolean(email)}
                autoComplete={signingIn ? "current-password" : "new-password"}
                value={password} onChange={setPassword} said={fields.password} />
            {hasAccount && (
                <p className="auth-problem" role="alert">
                    <Glyph name="warn" />
                    <span>
                        You already have an account with this email.{" "}
                        <button className="auth-link" type="button" onClick={() => {
                            setHasAccount(false); setExistingPassword(true); setSettled(true); setSaid(null);
                        }}>Sign in</button>
                    </span>
                </p>
            )}
            {wrongPassword && (
                <p className="auth-problem" role="alert">
                    <Glyph name="warn" />
                    <span>
                        That email and password don’t go together. Try again, or{" "}
                        <a className="auth-link" href={authLink(PORTAL_PATH + "/reset-password")}>set a new password</a>.
                    </span>
                </p>
            )}
            <Problem said={said} />
            <button className="b b--l b--primary auth-wide" type="submit" disabled={busy}>
                {busy ? "One moment…" : authenticated ? "Continue" : signingIn ? "Sign in" : "Make my account"}
            </button>
            <div className="auth-row">
                {signingIn && <a className="auth-link" href={authLink(PORTAL_PATH + "/reset-password")}>Forgot your password?</a>}
                <span className="auth-row__gap" />
                {!existingPassword && (
                    <button type="button" className="auth-link" disabled={busy} onClick={() => {
                        /* With the address already in hand, the code goes out
                           now; without one, back to where it is typed. */
                        if (chip && email.trim()) { toCode(); code.start(email.trim()); } else toCode();
                    }}>Email me a code instead</button>
                )}
            </div>
        </form>
    );

    /* ── the password, under an address already decided ── */
    if (usePassword && settled) {
        return (
            <Screen title={signingIn ? "Your password" : "Choose a password"} then={arriving.then} speaker={progress ?? STILL}>
                {passwordForm(true)}
            </Screen>
        );
    }

    /* ── the door ── */
    const { waiting } = arriving;
    const title = arriving.invited ? "Accept your invitation"
        : mode === "up" ? "Make your account"
        : arriving.installing ? "Sign in or make your account"
        : waiting.name ? "Sign in to open " + waiting.name
        : "Welcome back";
    /* "Then we'll make your first Lemma" is only true for an account that
       goes on into the way in; one on its way somewhere else is told where. */
    const lead = arriving.invited ? "Sign in, or make an account with the email the invitation went to."
        : mode === "up" && !arriving.then ? "Then we’ll make your first Lemma together."
        : undefined;
    const speaker: Speaker = progress
        ?? (arriving.invited ? { kind: "cast", faces: arriving.crowd, caption: CAST_CAPTION, foot: "You’ll join where you were invited once you’re in." }
            : waiting.id ? { kind: "preview", id: waiting.id, name: waiting.name, face: waiting.faces[0], others: arriving.crowd }
            : mode === "up" ? { kind: "cast", faces: arriving.crowd, caption: CAST_CAPTION }
            : STILL);
    const footer: ReactNode = arriving.invited && !usePassword
        ? <>Have an account already? Same steps: I’ll find it.</>
        : mode === "in"
            ? <>New to Lemma? <a className="auth-link" href={other}>Make an account</a></>
            : <>Already have an account? <a className="auth-link" href={other}>Sign in</a></>;

    return (
        <Screen title={title} lead={lead} then={arriving.invited ? null : arriving.then} speaker={speaker} footer={footer}>
            {refused && <Refused />}
            {configured && configured.length > 0 && <>
                <Providers configured={configured} onProblem={setSaid} onLeaving={setLeaving} />
                <p className="auth-or">or</p>
            </>}
            {usePassword ? passwordForm(false) : (
                <form className="auth-stack auth-stack--form" noValidate onSubmit={(event) => {
                    event.preventDefault();
                    setSaid(null);
                    code.start();
                }}>
                    <Field id="email" label={emailLabel} type="email" autoComplete="email" autoFocus
                        placeholder="you@company.com" value={email} onChange={code.setEmail} />
                    <Problem said={said ?? code.said} />
                    <button className="b b--l b--primary auth-wide" disabled={code.busy || code.cooldown > 0}>
                        {code.busy ? "One moment…" : "Continue"}
                    </button>
                    {code.cooldown > 0 && (
                        <p className="auth-small" role="status">
                            You can ask for another code in <span className="auth-fig">{code.cooldown}</span> s.
                        </p>
                    )}
                    <div className="auth-row">
                        <button type="button" className="auth-link" disabled={code.busy} onClick={() => {
                            setUsePassword(true);
                            setSettled(/\S@\S/.test(email.trim()));
                            setSaid(null); setWrongPassword(false); setHasAccount(false); setFields({});
                        }}>Use a password instead</button>
                    </div>
                </form>
            )}
            {mode === "up" && (
                <p className="auth-small">
                    By making an account you agree to the <a href="/tos">Terms</a> and the <a href="/privacy">Privacy policy</a>.
                </p>
            )}
        </Screen>
    );
}

/* ── coming back from a provider ────────────────────────────────────── */

/** `provider` is read off the path for its name only. Which provider this was
 *  is SuperTokens' to recover from the state it stored before leaving; the
 *  path is just what to call it on screen. */
export function Callback({ provider: id }: { provider?: string }) {
    const provider = providerOf(id);
    const name = provider?.name ?? null;
    const [failure, setFailure] = useState<{ kind: "refused" | "failed" | "after"; said: string | null } | null>(null);
    const [done, setDone] = useState(false);
    const started = useRef(false);

    useEffect(() => {
        if (started.current) return;
        started.current = true;
        let signedIn = false;
        void (async () => {
            try {
                const answer = await ThirdParty.signInAndUp();
                if (answer.status === "OK") {
                    signedIn = true;
                    setDone(true);
                    /* The destination was put away before we left, because the
                       URL we came back on is the provider's, not ours. */
                    await completeAuth("");
                    return;
                }
                if (answer.status === "SIGN_IN_UP_NOT_ALLOWED") {
                    setFailure({ kind: "refused", said: answer.reason || null });
                } else {
                    setFailure({ kind: "failed", said: null });
                }
            } catch (error) {
                setFailure({ kind: signedIn ? "after" : "failed", said: sayProblem(error) });
            }
        })();
    }, []);

    const again = async () => {
        if (!provider) return;
        try {
            await continueWithProvider(provider.id);
        } catch (error) {
            setFailure({ kind: "failed", said: sayProblem(error) });
        }
    };
    const instead = <a className="b b--l b--secondary auth-wide" href={authLink(PORTAL_PATH)}>Use your email instead</a>;

    if (failure?.kind === "refused") {
        return (
            <Screen
                icon={{ glyph: "lock", tone: "warn" }}
                title="This account can’t sign in here"
                lead={failure.said ?? "It isn’t one this workspace lets in. Try your work account."}
            >
                <div className="auth-stack">
                    {provider && (
                        <button type="button" className="b b--l b--primary auth-wide" onClick={() => void again()}>
                            <provider.Mark />Use another {provider.name} account
                        </button>
                    )}
                    {instead}
                </div>
            </Screen>
        );
    }
    if (failure?.kind === "failed") {
        return (
            <Screen
                icon={{ glyph: "warn", tone: "warn" }}
                title={name ? name + " didn’t sign you in" : "Sign-in didn’t finish"}
                lead={failure.said ?? "The sign-in was closed or didn’t finish. Nothing changed."}
            >
                <div className="auth-stack">
                    {provider && (
                        <button type="button" className="b b--l b--primary auth-wide" onClick={() => void again()}>
                            <provider.Mark />Try {provider.name} again
                        </button>
                    )}
                    {instead}
                </div>
            </Screen>
        );
    }
    if (failure?.kind === "after") {
        return (
            <Screen icon={{ glyph: "warn", tone: "warn" }} title="That didn’t finish" lead={failure.said}
                speaker={{ kind: "still", status: "Signed in" }}>
                <div className="auth-row">
                    <a className="b b--l b--primary" href={authLink(PORTAL_PATH)}>Try again</a>
                </div>
            </Screen>
        );
    }
    return (
        <Screen
            title={name ? "Signing you in with " + name + "…" : "Signing you in…"}
            speaker={done ? { kind: "still", status: "Signed in", done: true } : { kind: "still", status: "Signing you in", busy: true }}
        />
    );
}

/* ── the password ───────────────────────────────────────────────────── */

/** Both halves of a reset, decided by whether the URL carries a token.
 *
 *  One screen rather than two routes because the backend builds the emailed
 *  link and it points at this path with a token on it — the reader never
 *  chooses between them, so the choice belongs here rather than in the URL. */
export function Reset() {
    const token = typeof window === "undefined" ? "" : EmailPassword.getResetPasswordTokenFromURL();
    return token ? <ResetSet /> : <ResetAsk />;
}

function BackToSignIn() {
    return (
        <a className="auth-back" href={authLink(PORTAL_PATH)}>
            <Glyph name="back" size={14} />Back to sign in
        </a>
    );
}

function ResetAsk() {
    const [email, setEmail] = useState("");
    const [said, setSaid] = useState<string | null>(null);
    const [sent, setSent] = useState(false);
    const [again, setAgain] = useState(false);
    const [busy, setBusy] = useState(false);
    const local = isLocalDeployment();
    const machine = capitalised(useThisComputer());

    const send = useCallback(async () => {
        setSaid(null);
        setBusy(true);
        try {
            rememberDestination(pendingDestination(window.location.search));
            await EmailPassword.sendPasswordResetEmail({ formFields: [{ id: "email", value: email.trim() }] });
            /* Sent, whatever the answer was. The endpoint deliberately does not
               say whether the address has an account, and a screen that
               reported the difference would hand that back. */
            setAgain(sent);
            setSent(true);
        } catch (error) {
            if (error instanceof Response) {
                const body = await error.clone().json().catch(() => ({}));
                setSaid(authFailure("reset", error.status, error.headers.get("retry-after"), (body as { message?: string }).message ?? ""));
            } else {
                setSaid(sayProblem(error));
            }
        }
        setBusy(false);
    }, [email, sent]);

    if (sent) {
        return (
            <Screen
                icon={{ glyph: "mail" }}
                title="Check your email"
                lead={<>If <b>{email.trim() || "that address"}</b> has an account, a link to choose a new password is on its way.</>}
            >
                {again && !said && <p className="auth-ok" role="status"><Glyph name="check" />Sent again. Use the newest link.</p>}
                <Problem said={said} />
                <div className="auth-row">
                    <a className="b b--m b--secondary" href={authLink(PORTAL_PATH)}>Back to sign in</a>
                    <button type="button" className="auth-link" disabled={busy} onClick={() => void send()}>
                        {busy ? "Sending…" : "Send it again"}
                    </button>
                </div>
            </Screen>
        );
    }

    return (
        <Screen back={<BackToSignIn />} title="Set a new password" lead="I’ll email you a link to choose one.">
            {/* Lemma on somebody's own computer has no mail until they set it
                up, and then this form can only fail. Said first, rather than
                as the error the form comes back with. The form stays: once
                email is set up it works, and the backend's refusal is still
                shown if it is not. There is deliberately no way round it
                here -- a reset that skips the mailbox is a way into anyone's
                account for whoever can reach this page. */}
            {local && (
                <p className="auth-note">
                    Lemma on your own computer sends reset links only once email is set up
                    ({machine} → Server setup → Email). If you can’t sign in, ask someone who can
                    to set it up, or reset the app’s data from Lemma → Recovery… in the menu bar.
                </p>
            )}
            <form className="auth-stack auth-stack--form" onSubmit={(event) => { event.preventDefault(); void send(); }} noValidate>
                <Field id="email" label="The email on your account" type="email" autoComplete="email" autoFocus
                    placeholder="you@company.com" value={email} onChange={setEmail} />
                <Problem said={said} />
                <button className="b b--l b--primary auth-wide" type="submit" disabled={busy}>
                    {busy ? "Sending…" : "Email me a link"}
                </button>
            </form>
        </Screen>
    );
}

function ResetSet() {
    const [password, setPassword] = useState("");
    const [said, setSaid] = useState<string | null>(null);
    const [fields, setFields] = useState<FieldSaid>({});
    const [done, setDone] = useState(false);
    const [expired, setExpired] = useState(false);
    const [busy, setBusy] = useState(false);

    const submit = useCallback(async (event: FormEvent) => {
        event.preventDefault();
        setSaid(null);
        setFields({});
        setBusy(true);
        try {
            const answer = await EmailPassword.submitNewPassword({ formFields: [{ id: "password", value: password }] });
            if (answer.status === "OK") { setDone(true); setBusy(false); return; }
            if (answer.status === "FIELD_ERROR") { setFields(fieldErrors(answer.formFields)); setBusy(false); return; }
            /* The token is single-use and expires, and this is the common way
               to arrive here: an old email, or a second click on the same link. */
            setExpired(true);
            setBusy(false);
        } catch (error) {
            setBusy(false);
            if (error instanceof Response) {
                const body = await error.clone().json().catch(() => ({}));
                setSaid(authFailure("new-password", error.status, error.headers.get("retry-after"), (body as { message?: string }).message ?? ""));
                return;
            }
            setSaid(sayProblem(error));
        }
    }, [password]);

    if (done) {
        /* Changing a password does not sign anybody in, so this says what is
           true and hands over to sign-in, rather than "Opening your
           workspace". */
        return (
            <Screen icon={{ glyph: "check", tone: "ok" }} title="Password changed" lead="Sign in with your new password.">
                <div className="auth-row">
                    <a className="b b--l b--primary" href={authLink(PORTAL_PATH)}>Sign in</a>
                </div>
            </Screen>
        );
    }

    if (expired) {
        return (
            <Screen
                icon={{ glyph: "warn", tone: "warn" }}
                title="That link has expired"
                lead="It’s been used, or it’s too old. Ask for another and use the newest one."
            >
                <div className="auth-row">
                    <a className="b b--l b--primary" href={authLink(PORTAL_PATH + "/reset-password")}>Email me a new link</a>
                </div>
            </Screen>
        );
    }

    return (
        <Screen title="Choose a new password" footer={<a className="auth-link" href={authLink(PORTAL_PATH + "/reset-password")}>Ask for another link</a>}>
            <form className="auth-stack auth-stack--form" onSubmit={submit} noValidate>
                <Field id="password" label="New password" type="password" autoComplete="new-password" autoFocus
                    value={password} onChange={setPassword} said={fields.password} />
                <Problem said={said} />
                <button className="b b--l b--primary auth-wide" type="submit" disabled={busy}>
                    {busy ? "Saving…" : "Save password"}
                </button>
            </form>
        </Screen>
    );
}
