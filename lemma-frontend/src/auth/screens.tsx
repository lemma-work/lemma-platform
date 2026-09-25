"use client";

import { LoadingIndicator } from "@/ui/loading";

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { EmailPassword, ThirdParty } from "./supertokens";
import { authFailure, sayProblem, type Attempt } from "./errors";
import { PORTAL_PATH, siteOrigin } from "./config";
import { authLink, rememberDestination, pendingDestination, destinationFrom, asksForDestination } from "./redirects";
import { accountAccess, completeAuth, completionDestination } from "./completion";
import { EmailCodeForm } from "./email-code-form";
import { waitingFor } from "./waiting";
import { CharacterPuppet } from "@/shell/character-puppet";
import { HideIcon, LemmaLogo, ShowIcon } from "@/ui/icons";
import { GoogleMark, MicrosoftMark } from "./marks";

/* ── the two panes ──────────────────────────────────────────────────── */

/** What a teammate is for, in the words the product already uses.
 *
 *  Drawn from jobs that actually exist in this app rather than invented for a
 *  sign-in page — the competitor watch and the quote approval are the sample
 *  pod's real standing work, and the reconciliation is a real workflow. A
 *  marketing line nobody could point at would be the wrong thing to put on the
 *  one screen somebody sees before they have anything.
 */
const POSSIBILITIES = [
    "Watches five competitors. Tells you what moved.",
    "Drafts the quote. Asks you before it sends.",
    "Reconciles the ledger against the bank, every Monday.",
];

function Aside({ destination, looking }: { destination: string | null; looking: boolean }) {
    const { name, faces } = waitingFor(destination, destination ?? "lemma");
    const target = destination ? new URL(destination, siteOrigin()) : null;
    const destinationLabel = target
        ? target.host + (target.pathname === "/" ? "" : target.pathname)
        : "Your workspace";

    /* A wave on arrival, and another when they turn back from the password.
     *
     *  `greeting` is a counter the rigs watch: bump it and the character
     *  performs one wave and settles. It is not a loop — waving forever is a
     *  sticker with extra steps — so it is spent at the two moments that
     *  actually are a greeting. Between them the ambient layer in
     *  `app/characters/life.ts` keeps them breathing and swaying on its own. */
    const [greeting, setGreeting] = useState(0);
    useEffect(() => { if (looking) setGreeting((count) => count + 1); }, [looking]);

    return (
        <aside className="portal__aside">
            <div className="portal__brand"><LemmaLogo /></div>

            <div className="portal__middle">
                {/* They are not decoration. Which faces appear is read off
                    where the link is sending somebody: arrive from
                    `/t/marketing/library` and this is Marketing, their own
                    creature and their own name, because the address grammar
                    can say so. Somebody who typed the address gets a few of
                    the cast — the honest picture of "your teammates" when we
                    cannot say which. */}
                <div className="waiting" data-looking={looking ? "" : undefined}>
                    <div className="waiting__cast">
                        {faces.map((face, at) => (
                            <span className="waiting__one" key={face} style={{ ["--at" as string]: String(at) }}>
                                {/* The rig, not the still. Every one of the
                                    twenty-four has one; they breathe, sway,
                                    follow the pointer and take a short beat
                                    from their own vocabulary every few
                                    seconds. `delighted` is the state they hold
                                    while somebody is here — eyes smiling,
                                    stance open — and it drops while the
                                    password is being typed, because grinning
                                    at somebody over their shoulder is not the
                                    idea. */}
                                <CharacterPuppet
                                    character={face}
                                    size={112}
                                    greeting={greeting}
                                    mood={looking ? "delighted" : "idle"}
                                />
                            </span>
                        ))}
                    </div>
                </div>

                {/* "Kept working", not "is waiting". Waiting is what a login
                    screen does; the entire point of a teammate is that it got
                    on with the job while you were gone. */}
                <p className="portal__headline">
                    {!looking
                        ? "Your password"
                        : name
                            ? <>{name} is in your workspace.</>
                            : "Welcome to Lemma."}
                </p>
                <p className="portal__sub">
                    {!looking
                        ? "Enter your password to sign in."
                        : name
                            ? "Sign in to continue your work."
                            : "Give one an ongoing responsibility. It gets on with it."}
                </p>
            </div>

            <ul className="portal__does">
                {POSSIBILITIES.map((line) => <li key={line}>{line}</li>)}
            </ul>

            <p className="portal__destination">
                <span>After signing in to Lemma, you’ll continue to</span>
                <strong>{destinationLabel}</strong>
            </p>
        </aside>
    );
}

/** The frame: aspiration on the left, the ask on the right.
 *
 *  One frame for every screen in the portal rather than a special case for the
 *  short ones. A verification result that arrived in a different shape from
 *  the sign-in that preceded it would read as a different site.
 *
 *  `looking` is only ever false on the screens that have a password field; the
 *  rest pass nothing and the cast simply waits.
 */
export function Screen({ title, lead, children, footer, looking = true }: {
    title: string; lead?: ReactNode; children?: ReactNode; footer?: ReactNode; looking?: boolean;
}) {
    const destination = typeof window === "undefined" ? null : pendingDestination(window.location.search);
    return (
        <div className="portal">
            <Aside destination={destination} looking={looking} />
            <main className="portal__pane">
                <div className="portal__form">
                    <h2>{title}</h2>
                    {lead && <p className="portal__lead">{lead}</p>}
                    {children}
                    {footer && <p className="screen__footnote">{footer}</p>}
                </div>
            </main>
        </div>
    );
}

function Problem({ said }: { said: string | null }) {
    if (!said) return null;
    /* `alert` rather than `status`: a refusal that a screen reader announces
       only when it happens to be visited is one somebody retypes a correct
       password against, three times. */
    return <p className="auth__problem" role="alert">{said}</p>;
}

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

function Field({ id, label, type, value, onChange, said, autoComplete, autoFocus, onFocus, onBlur }: {
    id: string; label: string; type: string; value: string;
    onChange: (next: string) => void; said?: string; autoComplete?: string; autoFocus?: boolean;
    onFocus?: () => void; onBlur?: () => void;
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

    const control = (
        <input
            ref={input}
            id={id}
            type={secret && shown ? "text" : type}
            value={value}
            autoComplete={autoComplete}
            autoFocus={autoFocus}
            /* Shown as text, a password is still not prose: a spellchecker
               that underlines it, or sends it off to be checked, is the
               wrong reader. */
            spellCheck={secret ? false : undefined}
            autoCapitalize={secret ? "off" : undefined}
            autoCorrect={secret ? "off" : undefined}
            aria-invalid={said ? true : undefined}
            aria-describedby={said ? id + "-said" : undefined}
            onFocus={onFocus}
            onBlur={onBlur}
            onChange={(event) => onChange(event.target.value)}
        />
    );

    return (
        <div className="field">
            <label htmlFor={id}>{label}</label>
            {!secret ? control : (
                <div className="field__secret">
                    {control}
                    {/* Pressing the eye leaves the caret where it was: the
                        mousedown is swallowed so the input never blurs, and the
                        cast does not turn round mid-password. It stays
                        reachable by Tab. */}
                    <button
                        type="button"
                        className="field__reveal"
                        aria-controls={id}
                        aria-label={shown ? "Hide password" : "Show password"}
                        title={shown ? "Hide password" : "Show password"}
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={reveal}
                    >
                        {shown ? <HideIcon size={18} /> : <ShowIcon size={18} />}
                    </button>
                </div>
            )}
            {said && <span className="field__said" id={id + "-said"}>{said}</span>}
        </div>
    );
}

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
        <p className="auth__note">
            The link that sent you here asked us to hand you on somewhere we do not recognise.
            We will not. You will land in your workspace instead.
        </p>
    );
}

/* ── the providers ──────────────────────────────────────────────────── */

/** Google and Microsoft, which are the two the backend registers — see
 *  `build_thirdparty_providers`, where each is conditional on being
 *  configured. Nothing here can tell whether a given deployment has them, and
 *  the honest failure is the provider's own "unknown client" rather than this
 *  app guessing and hiding a working button. */
const PROVIDERS = [
    { id: "google", name: "Google", Mark: GoogleMark },
    { id: "active-directory", name: "Microsoft", Mark: MicrosoftMark },
];

function Providers({ onProblem }: { onProblem: (said: string) => void }) {
    const [going, setGoing] = useState<string | null>(null);

    const leave = useCallback(async (thirdPartyId: string) => {
        setGoing(thirdPartyId);
        try {
            /* The destination is put away before we leave: the provider brings
               the browser back to the callback with a URL of its own making,
               and whatever was asked for originally is not in it. */
            rememberDestination(pendingDestination(window.location.search));
            const url = await ThirdParty.getAuthorisationURLWithQueryParamsAndSetState({
                thirdPartyId,
                frontendRedirectURI: siteOrigin() + PORTAL_PATH + "/callback/" + thirdPartyId,
            });
            window.location.assign(url);
        } catch (error) {
            setGoing(null);
            onProblem(sayProblem(error));
        }
    }, [onProblem]);

    return (
        <div className="auth__providers">
            {PROVIDERS.map((provider) => (
                <button
                    key={provider.id}
                    className="btn auth__provider"
                    disabled={going !== null}
                    onClick={() => void leave(provider.id)}
                >
                    <provider.Mark />
                    <span>{going === provider.id ? <LoadingIndicator inline label={"Connecting to " + provider.name} /> : "Continue with " + provider.name}</span>
                </button>
            ))}
        </div>
    );
}

/* ── sign in and sign up ────────────────────────────────────────────── */

export function SignInUp({ mode }: { mode: "in" | "up" }) {
    const [usePassword, setUsePassword] = useState(false);
    const attempted = useRef(false);
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [said, setSaid] = useState<string | null>(null);
    const [fields, setFields] = useState<FieldSaid>({});
    const [busy, setBusy] = useState(false);
    const [authenticated, setAuthenticated] = useState(false);
    /* Whether the cast may look. False only while the password field has the
       caret — not while it merely holds a value, because a filled password on
       a blurred form is not being typed. */
    const [onPassword, setOnPassword] = useState(false);
    const { go, refused } = useLanding();

    const attempt: Attempt = mode === "in" ? "sign-in" : "sign-up";
    const other = authLink(mode === "in" ? PORTAL_PATH + "/signup" : PORTAL_PATH);

    useEffect(() => {
        let live = true;
        rememberDestination(pendingDestination(window.location.search));
        void accountAccess().then(access => {
            if (live && !attempted.current && access !== "signed-out") {
                window.location.replace(completionDestination(access, window.location.search));
            }
        }).catch(error => { if (live && !attempted.current) setSaid(sayProblem(error)); });
        return () => { live = false; };
    }, []);

    const submit = useCallback(async (event: FormEvent) => {
        event.preventDefault();
        attempted.current = true;
        setSaid(null);
        setFields({});
        setBusy(true);
        try {
            if (authenticated) { await go(); return; }
            const formFields = [{ id: "email", value: email.trim() }, { id: "password", value: password }];
            const answer = mode === "in"
                ? await EmailPassword.signIn({ formFields })
                : await EmailPassword.signUp({ formFields });

            if (answer.status === "OK") { setAuthenticated(true); await go(); return; }
            if (answer.status === "FIELD_ERROR") {
                setFields(fieldErrors(answer.formFields));
                setBusy(false);
                return;
            }
            if (answer.status === "WRONG_CREDENTIALS_ERROR") {

                setSaid("That email and password do not go together.");
                setBusy(false);
                return;
            }

            setSaid(("reason" in answer && answer.reason) ? String(answer.reason) : "That could not be completed. Try again.");
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
    }, [email, password, mode, go, attempt, authenticated]);

    return (
        <Screen
            title={mode === "in" ? "Welcome back" : "Make an account"}
            /* The left half already says what is through the door, so the
               right half does not say it again — it asks. Sign-up keeps a line
               because it is the one screen where somebody does not yet know
               what they are agreeing to do. */
            lead={mode === "in" ? undefined : "Create your account, then hire your first AI teammate."}
            footer={mode === "in"
                ? <>First time here? <a href={other}>Make an account</a></>
                : <>Already have one? <a href={other}>Sign in</a></>}
            looking={!onPassword}
        >
            {refused && <Refused />}
            <Providers onProblem={setSaid} />
            <p className="auth__or"><span>or</span></p>
            {!usePassword ? <><EmailCodeForm onAttempt={() => { attempted.current = true; }} /><Problem said={said} /></> : <form onSubmit={submit} noValidate>
                <Field id="email" label="Email" type="email" autoComplete="email" autoFocus
                    value={email} onChange={setEmail} said={fields.email} />
                <Field id="password" label="Password" type="password"
                    autoComplete={mode === "in" ? "current-password" : "new-password"}
                    onFocus={() => setOnPassword(true)} onBlur={() => setOnPassword(false)}
                    value={password} onChange={setPassword} said={fields.password} />
                <Problem said={said} />
                <div className="screen__actions">
                    <button className="btn btn--primary" type="submit" disabled={busy}>
                        {busy ? "One moment\u2026" : authenticated ? "Continue" : mode === "in" ? "Sign in" : "Make my account"}
                    </button>
                    {mode === "in" && <a className="screen__aside" href={authLink(PORTAL_PATH + "/reset-password")}>Forgotten your password?</a>}
                </div>
            </form>}
            <button className="linkish" type="button" disabled={busy} onClick={() => {
                setUsePassword(!usePassword); setOnPassword(false); setSaid(null);
            }}>{usePassword ? "Use an email code instead" : "Use a password instead"}</button>
        </Screen>
    );
}

/* ── coming back from a provider ────────────────────────────────────── */

export function Callback() {
    const [said, setSaid] = useState<string | null>(null);
    const [done, setDone] = useState(false);
    const started = useRef(false);

    useEffect(() => {
        if (started.current) return;
        started.current = true;
        void (async () => {
            try {
                const answer = await ThirdParty.signInAndUp();
                if (answer.status === "OK") {
                    setDone(true);
                    /* The destination was put away before we left, because the
                       URL we came back on is the provider's, not ours. */
                    await completeAuth("");
                    return;
                }
                if (answer.status === "SIGN_IN_UP_NOT_ALLOWED") {
                    setSaid(answer.reason || "That account is not allowed to sign in here.");
                } else {
                    setSaid("That sign-in did not complete. Try again.");
                }
            } catch (error) {
                setSaid(sayProblem(error));
            }
        })();
    }, []);

    if (said) {
        return (
            <Screen title="Sign-in didn’t complete" lead={said}>
                <div className="screen__actions">
                    <a className="btn btn--primary" href={authLink(PORTAL_PATH)}>Try again</a>
                </div>
            </Screen>
        );
    }
    return <Screen title={done ? "Signing you in\u2026" : "Nearly there\u2026"} lead="One moment." />;
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

function ResetAsk() {
    const [email, setEmail] = useState("");
    const [said, setSaid] = useState<string | null>(null);
    const [sent, setSent] = useState(false);
    const [busy, setBusy] = useState(false);

    const submit = useCallback(async (event: FormEvent) => {
        event.preventDefault();
        setSaid(null);
        setBusy(true);
        try {
            rememberDestination(pendingDestination(window.location.search));
            await EmailPassword.sendPasswordResetEmail({ formFields: [{ id: "email", value: email.trim() }] });
            /* Sent, whatever the answer was. The endpoint deliberately does not
               say whether the address has an account, and a screen that
               reported the difference would hand that back. */
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
    }, [email]);

    if (sent) {
        return (
            <Screen
                title="Check your email"
                lead={<>If there is an account for {email.trim() || "that address"}, you’ll receive a password reset link.</>}
                footer={<a href={authLink(PORTAL_PATH)}>Back to sign in</a>}
            />
        );
    }

    return (
        <Screen
            title="Reset your password"
            lead="Tell us the address on the account and we will email you a link."
            footer={<a href={authLink(PORTAL_PATH)}>Back to sign in</a>}
        >
            <form onSubmit={submit} noValidate>
                <Field id="email" label="Email" type="email" autoComplete="email" autoFocus value={email} onChange={setEmail} />
                <Problem said={said} />
                <div className="screen__actions">
                    <button className="btn btn--primary" type="submit" disabled={busy}>
                        {busy ? "Sending\u2026" : "Email me a link"}
                    </button>
                </div>
            </form>
        </Screen>
    );
}

function ResetSet() {
    const [password, setPassword] = useState("");
    const [said, setSaid] = useState<string | null>(null);
    const [fields, setFields] = useState<FieldSaid>({});
    const [done, setDone] = useState(false);
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
            setSaid("That link has expired, or has already been used. Ask for another.");
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
        return (
            <Screen title="Password updated." lead="Sign in with it.">
                <div className="screen__actions">
                    <a className="btn btn--primary" href={authLink(PORTAL_PATH)}>Sign in</a>
                </div>
            </Screen>
        );
    }

    return (
        <Screen title="Choose a new password" footer={<a href={authLink(PORTAL_PATH + "/reset-password")}>Ask for another link</a>}>
            <form onSubmit={submit} noValidate>
                <Field id="password" label="New password" type="password" autoComplete="new-password" autoFocus
                    value={password} onChange={setPassword} said={fields.password} />
                <Problem said={said} />
                <div className="screen__actions">
                    <button className="btn btn--primary" type="submit" disabled={busy}>
                        {busy ? "Saving\u2026" : "Save password"}
                    </button>
                </div>
            </form>
        </Screen>
    );
}
