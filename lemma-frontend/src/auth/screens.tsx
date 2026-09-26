"use client";

import { LoadingIndicator } from "@/ui/loading";

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { EmailPassword, ThirdParty } from "./supertokens";
import { authFailure, isExistingAccount, sayProblem, type Attempt } from "./errors";
import { PORTAL_PATH, siteOrigin } from "./config";
import { authLink, rememberDestination, pendingDestination, destinationFrom, asksForDestination } from "./redirects";
import { accountAccess, completeAuth, completionDestination } from "./completion";
import { continueWithProvider } from "./provider-login";
import { EmailCodeForm } from "./email-code-form";
import { isLocalDeployment } from "@/site/config";
import { capitalised, useThisComputer } from "@/desktop/this-computer";
import { waitingFor } from "./waiting";
import { CharacterPuppet } from "@/shell/character-puppet";
import { HideIcon, LemmaLogo, ShowIcon } from "@/ui/icons";
import { GoogleMark, MicrosoftMark } from "./marks";
import { fetchConfiguredProviders, type ProviderId } from "./login-methods";

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

/** Google and Microsoft, which are the two the backend can register — see
 *  `build_thirdparty_providers`, where each is conditional on being
 *  configured. Only the ones this deployment registered are drawn: it says
 *  which through SuperTokens' `/loginmethods` (`login-methods.ts`). */
const PROVIDERS: { id: ProviderId; name: string; Mark: typeof GoogleMark }[] = [
    { id: "google", name: "Google", Mark: GoogleMark },
    { id: "active-directory", name: "Microsoft", Mark: MicrosoftMark },
];

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

function Providers({ configured, onProblem }: { configured: ProviderId[]; onProblem: (said: string) => void }) {
    const [going, setGoing] = useState<string | null>(null);

    const leave = useCallback(async (thirdPartyId: string) => {
        setGoing(thirdPartyId);
        try {
            await continueWithProvider(thirdPartyId);
        } catch (error) {
            setGoing(null);
            onProblem(sayProblem(error));
        }
    }, [onProblem]);

    return (
        <div className="auth__providers">
            {PROVIDERS.filter((provider) => configured.includes(provider.id)).map((provider) => (
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
    /* A local install sends no mail until someone sets it up, so a code it
       emails may never arrive: a password is the way in that always works
       there, and the code stays one click away. */
    const [usePassword, setUsePassword] = useState(() => isLocalDeployment());
    const [existingPassword, setExistingPassword] = useState(false);
    const signingIn = mode === "in" || existingPassword;
    const attempted = useRef(false);
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [said, setSaid] = useState<string | null>(null);
    const [fields, setFields] = useState<FieldSaid>({});
    /* Sign-up for an address that already has a password. Its own state rather
       than a field error, because the answer is a way forward -- sign in --
       not something to correct in what was typed. */
    const [hasAccount, setHasAccount] = useState(false);
    const [busy, setBusy] = useState(false);
    const [authenticated, setAuthenticated] = useState(false);
    const configured = useConfiguredProviders();
    /* Whether the cast may look. False only while the password field has the
       caret — not while it merely holds a value, because a filled password on
       a blurred form is not being typed. */
    const [onPassword, setOnPassword] = useState(false);
    const { go, refused } = useLanding();

    const attempt: Attempt = signingIn ? "sign-in" : "sign-up";
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
    }, [email, password, signingIn, go, attempt, authenticated]);

    return (
        <Screen
            title={signingIn ? "Welcome back" : "Make an account"}
            /* The left half already says what is through the door, so the
               right half does not say it again — it asks. Sign-up keeps a line
               because it is the one screen where somebody does not yet know
               what they are agreeing to do. */
            lead={signingIn ? undefined : "Create your account, then hire your first AI teammate."}
            footer={mode === "in"
                ? <>First time here? <a href={other}>Make an account</a></>
                : <>Already have one? <a href={other}>Sign in</a></>}
            looking={!onPassword}
        >
            {refused && <Refused />}
            {configured && configured.length > 0 && <>
                <Providers configured={configured} onProblem={setSaid} />
                <p className="auth__or"><span>or</span></p>
            </>}
            {!usePassword ? <><EmailCodeForm onAttempt={() => { attempted.current = true; }} onPassword={address => {
                setEmail(address); setExistingPassword(true); setUsePassword(true); setSaid(null);
            }} /><Problem said={said} /></> : <form onSubmit={submit} noValidate>
                {existingPassword && <p className="auth__note">This account uses a password. Enter it to sign in.</p>}
                <Field id="email" label="Email" type="email" autoComplete="email" autoFocus
                    value={email} onChange={setEmail} said={fields.email} />
                <Field id="password" label="Password" type="password"
                    autoComplete={signingIn ? "current-password" : "new-password"}
                    onFocus={() => setOnPassword(true)} onBlur={() => setOnPassword(false)}
                    value={password} onChange={setPassword} said={fields.password} />
                {hasAccount && <p className="auth__problem" role="alert">
                    You already have an account with this email.{" "}
                    <button className="linkish" type="button" onClick={() => {
                        setHasAccount(false); setExistingPassword(true); setSaid(null);
                    }}>Sign in</button>
                </p>}
                <Problem said={said} />
                <div className="screen__actions">
                    <button className="btn btn--primary" type="submit" disabled={busy}>
                        {busy ? "One moment\u2026" : authenticated ? "Continue" : signingIn ? "Sign in" : "Make my account"}
                    </button>
                    {signingIn && <a className="screen__aside" href={authLink(PORTAL_PATH + "/reset-password")}>Forgotten your password?</a>}
                </div>
            </form>}
            <button className="linkish" type="button" disabled={busy} onClick={() => {
                setUsePassword(!usePassword); setExistingPassword(false); setOnPassword(false); setSaid(null); setHasAccount(false);
            }}>{usePassword ? existingPassword ? "Use another email" : "Use an email code instead" : "Use a password instead"}</button>
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
    const local = isLocalDeployment();
    const machine = capitalised(useThisComputer());

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
            {/* Lemma on somebody's own computer has no mail until they set it
                up, and then this form can only fail. Said first, rather than
                as the error the form comes back with. The form stays: once
                email is set up it works, and the backend's refusal is still
                shown if it is not. There is deliberately no way round it
                here -- a reset that skips the mailbox is a way into anyone's
                account for whoever can reach this page. */}
            {local && (
                <p className="auth__note">
                    Lemma on your own computer sends reset links only once email is set up
                    ({machine} → Server setup → Email). If you can’t sign in, ask someone who can
                    to set it up, or reset the app’s data from Lemma → Recovery… in the menu bar.
                </p>
            )}
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
