"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircleIcon, ExternalIcon, RefreshIcon } from "@/ui/icons";
import { openExternal } from "./open-external";
import { openSettings } from "./open-settings";
import { useThisComputer } from "./this-computer";
import {
    CREDENTIAL_FORMS, detectLocalServers, formConfigured, formFromFocus, friendlyError, meaningfulIntent,
    sectionPayloads, stored, thisMac,
    type CredentialFormSpec, type Draft, type SectionPayload, type SecretIntent, type SetupGroup, type ThisMacSnapshot,
} from "./this-mac";
import {
    AI_PRESETS, CAPABILITIES, EMAIL_HINTS, aiDraftFrom, aiDraftProblem, aiProfile, aiSectionPayload, capabilityStatus,
    emailDraftFrom, emailDraftProblem, emailPayloads, needsKey, needsSetup, presetFor, probeKey, suggestModels, testFailure,
    type AiDraft, type Capability, type EmailDraft,
} from "./server-setup";
import { sendTestEmail } from "./server-setup-email";
import { useThisMacAvailability, useThisMacSnapshot } from "./this-mac-settings";
import { ThisMacDiagnostics } from "./this-mac-advanced";

/** Settings → This Mac → Server setup.
 *
 *  Everything this computer's Lemma server needs a key for, one capability
 *  per card: its status, what it unlocks, a Test, and how to get what it
 *  needs. Replaces the flat list of developer credentials that used to be
 *  Advanced, where "set up" meant any field had anything in it and nothing
 *  said whether it worked.
 *
 *  Secrets go to this computer's credential vault and are never read back:
 *  a stored one shows as "Saved", and replacing or removing one is explicit
 *  (and, for one already in use, confirmed natively by the shell). */

/* ── shared pieces ─────────────────────────────────────────────────── */

type Said = { text: string; bad?: boolean } | null;

function Status({ snapshot, id }: { snapshot: ThisMacSnapshot; id: Capability }) {
    const status = capabilityStatus(snapshot, id);
    const tone = status.state === "ready" ? "ok" : status.state === "needs-setup" ? "warn" : "muted";
    return <span className={"mrow__state mrow__state--" + tone}><i aria-hidden="true" />{status.label}</span>;
}

/** One capability. Closed until asked for, except the one that still needs
 *  setting up, so the page opens on the work that is left. */
function Card({ id, snapshot, open, children }: { id: Capability; snapshot: ThisMacSnapshot; open: boolean; children: ReactNode }) {
    const spec = CAPABILITIES.find((one) => one.id === id)!;
    const ref = useRef<HTMLDetailsElement>(null);
    useEffect(() => {
        if (open) ref.current?.scrollIntoView({ block: "start" });
    }, [open]);
    const needs = capabilityStatus(snapshot, id).state === "needs-setup";
    return (
        <details className="setup-card" id={"server-setup-" + id} ref={ref} open={open || needs || undefined}>
            <summary>
                <span className="thismac-row__text">
                    <span className="thismac-row__name">{spec.title}{spec.required ? " · required" : ""}</span>
                    <span className="thismac-row__said">{spec.unlocks}</span>
                </span>
                <Status snapshot={snapshot} id={id} />
            </summary>
            <div className="setup-card__body">{children}</div>
        </details>
    );
}

function Hint({ steps, url, label }: { steps: string; url: string | null; label: string }) {
    return (
        <p className="thismac-said setup-hint">
            {steps}{" "}
            {url && (
                <button type="button" className="linkish" onClick={() => void openExternal(url)}>
                    {label} <ExternalIcon size={11} />
                </button>
            )}
        </p>
    );
}

function Said({ said }: { said: Said }) {
    if (!said) return null;
    return <p className={"thismac-said" + (said.bad ? " thismac-said--bad" : "")} role={said.bad ? "alert" : "status"}>{said.text}</p>;
}

/** A secret field: empty when stored ("Saved — type to replace"), with an
 *  explicit Remove. Typing and then clearing is "keep". */
function SecretField({ id, label, name, snapshot, intent, onChange }: {
    id: string; label: string; name: string; snapshot: ThisMacSnapshot;
    intent: SecretIntent | undefined; onChange: (intent: SecretIntent) => void;
}) {
    const isStored = stored(snapshot, name);
    const removing = intent?.action === "remove";
    return (
        <div className="field">
            <label htmlFor={id}>{label}</label>
            <input
                id={id}
                type="password"
                autoComplete="new-password"
                spellCheck={false}
                disabled={removing}
                placeholder={removing ? "Will be removed" : isStored ? "Saved — type to replace" : ""}
                value={intent?.action === "replace" ? intent.value : ""}
                onChange={(event) => onChange({ action: "replace", value: event.target.value })}
            />
            {isStored && (
                <button type="button" className="linkish thismac-form__remove"
                    onClick={() => onChange(removing ? { action: "keep" } : { action: "remove" })}>
                    {removing ? "Keep it" : "Remove"}
                </button>
            )}
        </div>
    );
}

/** Save each section in order, each against the revision the one before it
 *  left: the daemon refuses a stale revision rather than overwrite. `null`
 *  when a native confirmation was declined. */
async function applyInOrder(payloads: SectionPayload[], revision: number): Promise<number | null> {
    let current = revision;
    for (const payload of payloads) {
        const answer = await thisMac.applySection({ ...payload, expected_revision: current }) as { cancelled?: boolean; config?: { revision?: number } };
        if (answer?.cancelled) return null;
        current = answer?.config?.revision ?? current + 1;
    }
    return payloads.length;
}

function savedLine(count: number | null): Said {
    if (count === null) return { text: "Not saved. The credentials in use are unchanged." };
    return { text: count ? "Saved. Lemma restarted its server to use it." : "Nothing changed." };
}

/** After a save the server restarted under every open query, so everything
 *  is read again rather than left showing what the old server said. */
function refreshAfterSave(queryClient: ReturnType<typeof useQueryClient>, count: number | null) {
    void (count ? queryClient.invalidateQueries() : queryClient.invalidateQueries({ queryKey: ["this-mac"] }));
}

/* ── the AI model ──────────────────────────────────────────────────── */

/** Kept while this page is open, for the reason Advanced kept its drafts:
 *  Settings unmounts a section as you move around it. Memory only. */
let aiDraftMemory: { draft: AiDraft; key: SecretIntent | undefined } | null = null;

function AiModel({ snapshot }: { snapshot: ThisMacSnapshot }) {
    const queryClient = useQueryClient();
    const noun = useThisComputer();
    const saved = snapshot.operator.config.ai;
    const [draft, setDraftState] = useState<AiDraft>(() => aiDraftMemory?.draft ?? aiDraftFrom(saved));
    const [key, setKeyState] = useState<SecretIntent | undefined>(() => aiDraftMemory?.key);
    const [said, setSaid] = useState<Said>(null);
    /* "Other" with no address typed yet is still a choice, not "nothing chosen". */
    const [chosenOther, setChosenOther] = useState(false);
    const setDraft = (next: AiDraft) => { aiDraftMemory = { draft: next, key }; setDraftState(next); setSaid(null); };
    const setKey = (next: SecretIntent) => { aiDraftMemory = { draft, key: next }; setKeyState(next); setSaid(null); };
    const preset = presetFor({ protocol: draft.protocol, base_url: draft.baseUrl }, chosenOther);
    const keyStored = stored(snapshot, "ai.api_key");
    const local = !needsKey(draft.baseUrl) && draft.baseUrl.trim() !== "";
    const privateHttp = /^http:\/\//i.test(draft.baseUrl.trim()) && !local;

    /* Offers the model servers already answering on this computer. */
    const found = useQuery({ queryKey: ["this-mac-local-model-servers"], queryFn: () => detectLocalServers(), staleTime: 60_000, retry: 0 });

    const list = useMutation({
        mutationFn: async () => {
            const models = await thisMac.discoverModels({ ai: aiProfile({ ...draft, models: [] }, null), ...probeKey(draft, key) });
            return Array.isArray(models) ? models.filter((one): one is string => typeof one === "string") : [];
        },
        onSuccess: (models) => {
            setDraft(suggestModels(draft, models));
            setSaid(models.length ? { text: `Found ${models.length} model${models.length === 1 ? "" : "s"}.` } : { text: "The provider listed no models.", bad: true });
        },
        onError: (problem) => setSaid({ text: testFailure(problem), bad: true }),
    });
    const test = useMutation({
        mutationFn: () => thisMac.testSetup({ service: "ai", ai: aiProfile(draft, null) as unknown as Record<string, unknown>, ...probeKey(draft, key) }),
        onSuccess: (answer) => setSaid({ text: typeof answer?.detail === "string" ? answer.detail : "The model answered." }),
        onError: (problem) => setSaid({ text: testFailure(problem), bad: true }),
    });
    const save = useMutation({
        mutationFn: () => applyInOrder([aiSectionPayload(snapshot, draft, key)], snapshot.operator.config.revision),
        onSuccess: (count) => {
            if (count !== null) { aiDraftMemory = null; setKeyState(undefined); }
            setSaid(savedLine(count));
            refreshAfterSave(queryClient, count);
        },
        onError: (problem) => {
            setSaid({ text: friendlyError(problem), bad: true });
            void queryClient.invalidateQueries({ queryKey: ["this-mac"] });
        },
    });
    const busy = list.isPending || test.isPending || save.isPending;
    const problem = aiDraftProblem(draft, key, keyStored);

    const choose = (id: string) => {
        const next = AI_PRESETS.find((one) => one.id === id)!;
        setChosenOther(id === "other");
        setDraft({ ...draft, protocol: next.protocol, baseUrl: next.baseUrl, models: [], defaultModel: "", imageModel: "", fastModel: "" });
    };

    return (
        <form className="setup-form" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
            <fieldset disabled={busy}>
                <div className="theme__modes" role="radiogroup" aria-label="Provider">
                    {AI_PRESETS.map((one) => {
                        const detected = found.data?.some((server) => server.baseUrl === one.baseUrl);
                        return (
                            <button key={one.id} type="button" role="radio" className="theme__mode"
                                aria-checked={preset?.id === one.id} aria-pressed={preset?.id === one.id} onClick={() => choose(one.id)}>
                                {one.name}{detected ? " · found" : ""}
                            </button>
                        );
                    })}
                </div>
                {!preset && <p className="thismac-said">Choose where the model runs: a provider’s API, or a model server on this computer.</p>}
                {preset && <Hint steps={preset.hint} url={preset.keyUrl} label={"Get a " + preset.name + " key"} />}
                {preset?.id === "other" && (
                    <div className="theme__modes" role="radiogroup" aria-label="API style">
                        {(["openai_compat", "anthropic_compat"] as const).map((protocol) => (
                            <button key={protocol} type="button" role="radio" className="theme__mode"
                                aria-checked={draft.protocol === protocol} aria-pressed={draft.protocol === protocol}
                                onClick={() => setDraft({ ...draft, protocol, models: [] })}>
                                {protocol === "openai_compat" ? "OpenAI-compatible" : "Anthropic-compatible"}
                            </button>
                        ))}
                    </div>
                )}
                {preset && <>
                <div className="field">
                    <label htmlFor="server-setup-ai-url">Address</label>
                    <input id="server-setup-ai-url" value={draft.baseUrl} spellCheck={false} placeholder="https://…/v1"
                        onChange={(event) => setDraft({ ...draft, baseUrl: event.target.value, models: [] })} />
                </div>
                {privateHttp && (
                    <label className="check">
                        <input type="checkbox" checked={draft.allowPrivateNetwork}
                            onChange={(event) => setDraft({ ...draft, allowPrivateNetwork: event.target.checked })} />
                        <span>This address is on a network I trust (it is not encrypted)</span>
                    </label>
                )}
                {!local && (
                    <SecretField id="server-setup-ai-key" label="API key" name="ai.api_key" snapshot={snapshot} intent={key} onChange={setKey} />
                )}
                <div className="setup-form__acts">
                    <button type="button" className="btn" onClick={() => list.mutate()}>
                        <RefreshIcon size={13} className={list.isPending ? "spin" : undefined} /> {draft.models.length ? "List models again" : "List models"}
                    </button>
                </div>
                {draft.models.length > 0 && <>
                    <div className="field">
                        <label htmlFor="server-setup-ai-model">Model teammates use</label>
                        <select id="server-setup-ai-model" value={draft.defaultModel} onChange={(event) => setDraft({ ...draft, defaultModel: event.target.value })}>
                            {draft.models.map((model) => <option key={model} value={model}>{model}</option>)}
                        </select>
                    </div>
                    {draft.protocol !== "anthropic_compat" && (
                        <div className="field">
                            <label htmlFor="server-setup-ai-image">Model that reads images</label>
                            <select id="server-setup-ai-image" value={draft.imageModel} onChange={(event) => setDraft({ ...draft, imageModel: event.target.value })}>
                                <option value="">None — images are not read</option>
                                {draft.models.map((model) => <option key={model} value={model}>{model}</option>)}
                            </select>
                            <span className="thismac-said">Choose one only if it accepts images; the provider’s list does not say which do.</span>
                        </div>
                    )}
                    <div className="field">
                        <label htmlFor="server-setup-ai-fast">Fast model, for titles and summaries</label>
                        <select id="server-setup-ai-fast" value={draft.fastModel} onChange={(event) => setDraft({ ...draft, fastModel: event.target.value })}>
                            <option value="">Same as the model teammates use</option>
                            {draft.models.map((model) => <option key={model} value={model}>{model}</option>)}
                        </select>
                    </div>
                </>}
                <div className="modal__acts">
                    <button type="button" className="btn" disabled={Boolean(problem)} title={problem ?? undefined} onClick={() => test.mutate()}>
                        {test.isPending ? "Testing…" : "Test"}
                    </button>
                    <button className="btn btn--primary" type="submit" disabled={Boolean(problem)} title={problem ?? undefined}>
                        {save.isPending ? "Saving…" : "Save"}
                    </button>
                </div>
                </>}
            </fieldset>
            {preset && problem && !said && <p className="thismac-said">{problem}</p>}
            <Said said={said} />
            <p className="thismac-said">
                Teammates can also use models your organization adds in{" "}
                <button type="button" className="linkish" onClick={() => openSettings("models")}>Models</button>.
                {" "}This one is {noun}’s own, and the one the server uses for its own work.
            </p>
        </form>
    );
}

/* ── email ─────────────────────────────────────────────────────────── */

let emailDraftMemory: { draft: EmailDraft; secrets: Record<string, SecretIntent> } | null = null;

function Email({ snapshot }: { snapshot: ThisMacSnapshot }) {
    const queryClient = useQueryClient();
    const noun = useThisComputer();
    const [draft, setDraftState] = useState<EmailDraft>(() => emailDraftMemory?.draft ?? emailDraftFrom(snapshot.operator.config.email));
    const [secrets, setSecretsState] = useState<Record<string, SecretIntent>>(() => emailDraftMemory?.secrets ?? {});
    const [said, setSaid] = useState<Said>(null);
    const setDraft = (next: EmailDraft) => { emailDraftMemory = { draft: next, secrets }; setDraftState(next); setSaid(null); };
    const setSecret = (name: string, intent: SecretIntent) => {
        const next = { ...secrets, [name]: intent };
        emailDraftMemory = { draft, secrets: next };
        setSecretsState(next);
        setSaid(null);
    };
    const payloads = emailPayloads(snapshot, draft, secrets);
    const unsaved = payloads.length > 0;
    const problem = emailDraftProblem(snapshot, draft, secrets);

    const save = useMutation({
        mutationFn: () => applyInOrder(payloads, snapshot.operator.config.revision),
        onSuccess: (count) => {
            if (count !== null) { emailDraftMemory = null; setSecretsState({}); }
            setSaid(savedLine(count));
            refreshAfterSave(queryClient, count);
        },
        onError: (cause) => {
            setSaid({ text: friendlyError(cause), bad: true });
            void queryClient.invalidateQueries({ queryKey: ["this-mac"] });
        },
    });
    const test = useMutation({
        mutationFn: async () => {
            const lines: string[] = [];
            if (draft.provider === "resend") {
                const checked = await thisMac.testSetup({ service: "resend", from_email: draft.fromEmail.trim() });
                if (typeof checked?.detail === "string") lines.push(checked.detail);
            }
            lines.push(await sendTestEmail());
            return lines.join(" ");
        },
        onSuccess: (text) => setSaid({ text }),
        onError: (cause) => setSaid({ text: testFailure(cause), bad: true }),
    });

    return (
        <form className="setup-form" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
            <fieldset disabled={save.isPending || test.isPending}>
                <div className="theme__modes" role="radiogroup" aria-label="Send email with">
                    {([["none", "Not set up"], ["resend", "Resend"], ["smtp", "SMTP server"]] as const).map(([provider, name]) => (
                        <button key={provider} type="button" role="radio" className="theme__mode"
                            aria-checked={draft.provider === provider} aria-pressed={draft.provider === provider}
                            onClick={() => setDraft({ ...draft, provider })}>{name}</button>
                    ))}
                </div>
                {draft.provider === "none" && (
                    <p className="thismac-said">Mail stays on {noun}: invitations still work by sharing their link, but nobody is emailed.</p>
                )}
                {draft.provider !== "none" && <Hint {...EMAIL_HINTS[draft.provider]} />}
                {draft.provider === "resend" && (
                    <SecretField id="server-setup-resend-key" label="Resend API key" name="surfaces.resend_api_key" snapshot={snapshot}
                        intent={secrets["surfaces.resend_api_key"]} onChange={(intent) => setSecret("surfaces.resend_api_key", intent)} />
                )}
                {draft.provider === "smtp" && <>
                    <div className="field">
                        <label htmlFor="server-setup-smtp-host">SMTP host</label>
                        <input id="server-setup-smtp-host" value={draft.smtpHost} spellCheck={false} placeholder="smtp.example.com"
                            onChange={(event) => setDraft({ ...draft, smtpHost: event.target.value })} />
                    </div>
                    <div className="field">
                        <label htmlFor="server-setup-smtp-port">Port</label>
                        <input id="server-setup-smtp-port" inputMode="numeric" value={draft.smtpPort}
                            onChange={(event) => setDraft({ ...draft, smtpPort: event.target.value })} />
                    </div>
                    <div className="field">
                        <label htmlFor="server-setup-smtp-user">User name</label>
                        <input id="server-setup-smtp-user" value={draft.smtpUser} spellCheck={false} autoComplete="off"
                            onChange={(event) => setDraft({ ...draft, smtpUser: event.target.value })} />
                    </div>
                    <SecretField id="server-setup-smtp-password" label="Password" name="email.smtp_password" snapshot={snapshot}
                        intent={secrets["email.smtp_password"]} onChange={(intent) => setSecret("email.smtp_password", intent)} />
                    <label className="check">
                        <input type="checkbox" checked={draft.smtpUseTls} onChange={(event) => setDraft({ ...draft, smtpUseTls: event.target.checked })} />
                        <span>Encrypt the connection (TLS)</span>
                    </label>
                </>}
                {draft.provider !== "none" && (
                    <div className="field">
                        <label htmlFor="server-setup-from">Send from</label>
                        <input id="server-setup-from" type="email" value={draft.fromEmail} spellCheck={false} placeholder="lemma@yourdomain.com"
                            onChange={(event) => setDraft({ ...draft, fromEmail: event.target.value })} />
                    </div>
                )}
                <div className="modal__acts">
                    {draft.provider !== "none" && (
                        <button type="button" className="btn" disabled={unsaved} title={unsaved ? "Save first, then send a test." : undefined}
                            onClick={() => test.mutate()}>
                            {test.isPending ? "Sending…" : "Send a test email"}
                        </button>
                    )}
                    <button className="btn btn--primary" type="submit" disabled={Boolean(problem) || !unsaved} title={problem ?? undefined}>
                        {save.isPending ? "Saving…" : "Save"}
                    </button>
                </div>
            </fieldset>
            {problem && !said && <p className="thismac-said">{problem}</p>}
            <Said said={said} />
        </form>
    );
}

/* ── a credential form ─────────────────────────────────────────────── */

/** Unsaved edits, kept for as long as this page is open. Memory only, never
 *  storage — some of it is credentials — so a reload forgets it. */
const drafts = new Map<string, { draft: Draft; secrets: Record<string, SecretIntent> }>();

function CredentialForm({ spec, snapshot, open }: { spec: CredentialFormSpec; snapshot: ThisMacSnapshot; open: boolean }) {
    const queryClient = useQueryClient();
    const config = snapshot.operator.config;
    const initial = (): Draft => drafts.get(spec.form)?.draft ?? Object.fromEntries(spec.fields.filter((field) => !field.secret).map((field) => {
        const holder = (field.key in config.integrations ? config.integrations : config.surfaces) as unknown as Record<string, string | boolean>;
        return [field.key, holder[field.key] ?? (field.kind === "toggle" ? false : "")];
    }));
    const [draft, setDraftState] = useState<Draft>(initial);
    const [secrets, setSecretsState] = useState<Record<string, SecretIntent>>(() => drafts.get(spec.form)?.secrets ?? {});
    const [unsaved, setUnsaved] = useState(() => drafts.has(spec.form));
    const setDraft = (next: (was: Draft) => Draft) => {
        const value = next(draft);
        drafts.set(spec.form, { draft: value, secrets });
        setDraftState(value);
        setUnsaved(true);
    };
    const setSecrets = (next: (was: Record<string, SecretIntent>) => Record<string, SecretIntent>) => {
        const value = next(secrets);
        drafts.set(spec.form, { draft, secrets: value });
        setSecretsState(value);
        setUnsaved(true);
    };
    const [said, setSaid] = useState<Said>(null);
    const ref = useRef<HTMLDetailsElement>(null);
    useEffect(() => {
        if (open) ref.current?.scrollIntoView({ block: "start" });
    }, [open]);

    const save = useMutation({
        mutationFn: () => applyInOrder(sectionPayloads(snapshot, spec.form, draft, secrets), config.revision),
        onSuccess: (count) => {
            if (count !== null) {
                drafts.delete(spec.form);
                setUnsaved(false);
                setSecretsState({});
            }
            setSaid(savedLine(count));
            refreshAfterSave(queryClient, count);
        },
        onError: (problem) => {
            /* Some sections may have saved before this one failed; refetch so
               a retry goes against the revision that is there now. */
            setSaid({ text: friendlyError(problem), bad: true });
            void queryClient.invalidateQueries({ queryKey: ["this-mac"] });
        },
    });
    const test = useMutation({
        mutationFn: () => {
            const typed = spec.test ? meaningfulIntent(secrets[spec.test.field]) : null;
            return thisMac.testSetup({ service: spec.test!.service, ...(typed?.action === "replace" ? { credential: typed.value } : {}) });
        },
        onSuccess: (answer) => setSaid({ text: typeof answer?.detail === "string" ? answer.detail : "It worked." }),
        onError: (problem) => setSaid({ text: testFailure(problem), bad: true }),
    });

    const configured = formConfigured(snapshot, spec.form);
    const testable = spec.test && (stored(snapshot, spec.test.field) || meaningfulIntent(secrets[spec.test.field])?.action === "replace");
    return (
        <details className="thismac-form" ref={ref} open={open || unsaved || undefined} id={"this-mac-" + spec.form}>
            <summary>
                <span className="thismac-row__text">
                    <span className="thismac-row__name">{spec.title}</span>
                    <span className="thismac-row__said">{spec.use}{spec.needsPublicLink ? " Needs Public sharing." : ""}</span>
                </span>
                <span className={"mrow__state mrow__state--" + (unsaved ? "warn" : configured ? "ok" : "muted")}>
                    <i aria-hidden="true" />{unsaved ? "Unsaved changes" : configured ? "Set up" : "Not set up"}
                </span>
            </summary>
            <form className="thismac-form__body" onSubmit={(event) => { event.preventDefault(); setSaid(null); save.mutate(); }}>
                <fieldset disabled={save.isPending || test.isPending}>
                    <Hint {...spec.hint} />
                    {spec.needsPublicLink && (
                        <p className="thismac-said">
                            Its messages arrive at a webhook on the internet, so it works only while Lemma is shared publicly.{" "}
                            <button type="button" className="linkish" onClick={() => openSettings("this-mac-sharing")}>Sharing</button>
                        </p>
                    )}
                    {spec.fields.map((field) => {
                        const id = "this-mac-" + spec.form + "-" + field.key.replace(/\W/g, "-");
                        if (field.kind === "toggle") {
                            return (
                                <label className="check" key={field.key}>
                                    <input type="checkbox" checked={draft[field.key] === true}
                                        onChange={(event) => setDraft((was) => ({ ...was, [field.key]: event.target.checked }))} />
                                    <span>{field.label}</span>
                                </label>
                            );
                        }
                        if (field.secret) {
                            return (
                                <SecretField key={field.key} id={id} label={field.label} name={field.key} snapshot={snapshot}
                                    intent={secrets[field.key]} onChange={(intent) => setSecrets((was) => ({ ...was, [field.key]: intent }))} />
                            );
                        }
                        return (
                            <div className="field" key={field.key}>
                                <label htmlFor={id}>{field.label}</label>
                                <input id={id} value={String(draft[field.key] ?? "")} spellCheck={false}
                                    onChange={(event) => setDraft((was) => ({ ...was, [field.key]: event.target.value }))} />
                            </div>
                        );
                    })}
                    {spec.redirect && snapshot.state.api_url && (
                        <p className="thismac-said">
                            Redirect URL for the app: <code>{snapshot.state.api_url.replace(/\/$/, "")}/api/v1/connectors/oauth/callback</code>
                        </p>
                    )}
                    <div className="modal__acts">
                        {unsaved && (
                            <button type="button" className="linkish" onClick={() => {
                                drafts.delete(spec.form);
                                setUnsaved(false);
                                setSecretsState({});
                                setDraftState(initial());
                                setSaid(null);
                            }}>Discard changes</button>
                        )}
                        {spec.test && (
                            <button type="button" className="btn" disabled={!testable} onClick={() => { setSaid(null); test.mutate(); }}>
                                {test.isPending ? "Testing…" : "Test"}
                            </button>
                        )}
                        <button className="btn btn--primary" type="submit">{save.isPending ? "Saving…" : "Save"}</button>
                    </div>
                </fieldset>
                <Said said={said} />
            </form>
        </details>
    );
}

function Forms({ group, snapshot, focus }: { group: SetupGroup; snapshot: ThisMacSnapshot; focus: string | null }) {
    const focused = formFromFocus(focus);
    return <>
        {CREDENTIAL_FORMS.filter((spec) => spec.group === group).map((spec) => (
            <CredentialForm key={spec.form} spec={spec} snapshot={snapshot} open={focused === spec.form} />
        ))}
    </>;
}

/* ── the page ──────────────────────────────────────────────────────── */

/** The capability a focus names: itself, or the card holding a form. */
function cardFor(focus: string | null): Capability | "advanced" | null {
    if (!focus) return null;
    if (focus === "advanced") return "advanced";
    if (CAPABILITIES.some((one) => one.id === focus)) return focus as Capability;
    const form = formFromFocus(focus);
    return form ? CREDENTIAL_FORMS.find((spec) => spec.form === form)!.group : null;
}

export function ThisMacServerSetup({ focus }: { focus: string | null }) {
    const snapshot = useThisMacSnapshot();
    const advanced = useRef<HTMLDivElement>(null);
    const card = cardFor(focus);
    useEffect(() => {
        if (card === "advanced") advanced.current?.scrollIntoView({ block: "start" });
    }, [card]);
    if (snapshot.isPending) return <p className="empty-row" role="status">Reading this computer’s settings…</p>;
    if (snapshot.isError) {
        return (
            <div className="thismac-problem" role="alert">
                <p>{friendlyError(snapshot.error)}</p>
                <button className="btn" onClick={() => void snapshot.refetch()}><RefreshIcon size={13} /> Try again</button>
            </div>
        );
    }
    const data = snapshot.data;
    const allReady = needsSetup(data).length === 0;
    return (
        <div className="thismac">
            <p className={"thismac-health thismac-health--" + (allReady ? "running" : "starting")} role="status">
                <i aria-hidden="true" />
                {allReady ? "This server has what it needs. The rest is optional." : "Set up an AI model so teammates can work."}
            </p>
            <p className="thismac-said">
                Saving restarts Lemma’s server: running agents stop, and open chats reconnect when it is back.
            </p>
            <Card id="ai" snapshot={data} open={card === "ai"}><AiModel snapshot={data} /></Card>
            <Card id="email" snapshot={data} open={card === "email"}><Email snapshot={data} /></Card>
            <Card id="connectors" snapshot={data} open={card === "connectors"}><Forms group="connectors" snapshot={data} focus={focus} /></Card>
            <Card id="channels" snapshot={data} open={card === "channels"}><Forms group="channels" snapshot={data} focus={focus} /></Card>
            <Card id="voice" snapshot={data} open={card === "voice"}><Forms group="voice" snapshot={data} focus={focus} /></Card>
            <Card id="search" snapshot={data} open={card === "search"}>
                <p className="thismac-said"><CheckCircleIcon size={12} /> Built-in search (DuckDuckGo) works with no key.</p>
                <Forms group="search" snapshot={data} focus={focus} />
            </Card>
            <div ref={advanced} id="server-setup-advanced">
                <h4 className="thismac-heading setup-advanced">Advanced</h4>
                <ThisMacDiagnostics snapshot={data} />
            </div>
        </div>
    );
}

/** What still needs setting up here, for the dots beside Settings' entries.
 *  Nothing wherever This Mac is not shown. */
export function useServerSetupNeeds(): Capability[] {
    const availability = useThisMacAvailability();
    const snapshot = useQuery({
        queryKey: ["this-mac"],
        queryFn: () => thisMac.snapshot(),
        enabled: availability === "shown",
        staleTime: 30_000,
        retry: 0,
    });
    return availability === "shown" ? needsSetup(snapshot.data) : [];
}
