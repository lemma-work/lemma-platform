import {
    CREDENTIAL_FORMS, LOCAL_SERVERS, formConfigured, meaningfulIntent, stored,
    type EmailConfig, type EmailProvider, type OperatorAi, type SectionPayload, type SectionsPayload, type SecretIntent,
    type SetupGroup, type SurfaceConfig, type ThisMacSnapshot,
} from "./this-mac";

/** Server setup: what this computer's Lemma server can do, and what it still
 *  needs to do it.
 *
 *  A local install runs the whole server, and some of what the server does
 *  needs a key only the person at this Mac can supply: a model to think with,
 *  a way to send mail, OAuth apps for connectors, bots for channels. Each is
 *  one capability here, with a status, one line of what it unlocks, a Test,
 *  and where to get what it needs.
 *
 *  Pure. `this-mac-setup.tsx` draws it and `tests/server-setup.test.ts` holds
 *  it to what it says. */

/* ── capabilities ──────────────────────────────────────────────────── */

export type Capability = "ai" | "email" | SetupGroup;

export type CapabilityState = "ready" | "needs-setup" | "optional";

export interface CapabilitySpec {
    id: Capability;
    title: string;
    /** One line of what it unlocks. */
    unlocks: string;
    required: boolean;
}

export const CAPABILITIES: CapabilitySpec[] = [
    { id: "ai", title: "AI model", required: true,
        unlocks: "Teammates think, write and use tools with it. Also names conversations, summarises long ones and reads images." },
    { id: "email", title: "Email", required: false,
        unlocks: "Sends invitations, password resets and sign-in codes. Without it, share invitation links yourself." },
    { id: "connectors", title: "Connectors", required: false,
        unlocks: "Lets people connect Gmail, GitHub, Slack, Notion and more, so teammates can work in them." },
    { id: "channels", title: "Channels", required: false,
        unlocks: "Lets teammates answer in Telegram, Slack, email, WhatsApp and Teams." },
    { id: "voice", title: "Voice", required: false,
        unlocks: "Lets teammates reply with voice notes and read the ones they are sent." },
    { id: "search", title: "Web search", required: false,
        unlocks: "Lets teammates look things up on the web." },
];

export function capabilitySpec(id: Capability): CapabilitySpec {
    return CAPABILITIES.find((one) => one.id === id)!;
}

export interface CapabilityStatus {
    state: CapabilityState;
    /** The status in words, beside the dot. */
    label: string;
}

const READY = (label = "Ready"): CapabilityStatus => ({ state: "ready", label });

/** Whether this installation's mail can leave it. */
export function emailReady(snapshot: ThisMacSnapshot): boolean {
    const email = snapshot.operator.config.email;
    if (!email.from_email.trim()) return false;
    if (email.provider === "resend") return stored(snapshot, "surfaces.resend_api_key");
    if (email.provider === "smtp") {
        return Boolean(email.smtp_host.trim() && email.smtp_user.trim()) && stored(snapshot, "email.smtp_password");
    }
    return false;
}

export function aiReady(snapshot: ThisMacSnapshot): boolean {
    return snapshot.operator.readiness.ai === "ready";
}

/** The forms of a group that hold anything. */
function configuredForms(snapshot: ThisMacSnapshot, group: SetupGroup): string[] {
    return CREDENTIAL_FORMS.filter((spec) => spec.group === group && formConfigured(snapshot, spec.form)).map((spec) => spec.title);
}

export function capabilityStatus(snapshot: ThisMacSnapshot, id: Capability): CapabilityStatus {
    switch (id) {
        case "ai":
            return aiReady(snapshot) ? READY() : { state: "needs-setup", label: "Needs setup" };
        case "email":
            return emailReady(snapshot) ? READY() : { state: "optional", label: "Optional" };
        case "search":
            return stored(snapshot, "integrations.brave_search_api_key") ? READY("Ready · Brave Search") : READY("Ready · DuckDuckGo");
        default: {
            const set = configuredForms(snapshot, id);
            return set.length ? READY("Ready · " + set.join(", ")) : { state: "optional", label: "Optional" };
        }
    }
}

/** What still stands between this install and a working server. Only the
 *  required ones: an optional capability left alone is a choice, not a gap,
 *  and a dot for it would never go away. */
export function needsSetup(snapshot: ThisMacSnapshot | null | undefined): Capability[] {
    if (!snapshot) return [];
    return CAPABILITIES.filter((one) => one.required && capabilityStatus(snapshot, one.id).state === "needs-setup").map((one) => one.id);
}

/* ── the AI model ──────────────────────────────────────────────────── */

export type AiProtocol = "openai_compat" | "anthropic_compat";

export interface AiPreset {
    id: string;
    name: string;
    protocol: AiProtocol;
    baseUrl: string;
    /** Where to get a key, or null for a server on this computer. */
    keyUrl: string | null;
    hint: string;
}

/** Providers people commonly use, so choosing one fills its address. Any
 *  OpenAI- or Anthropic-compatible endpoint works; "Other" takes its URL. */
export const AI_PRESETS: AiPreset[] = [
    { id: "openai", name: "OpenAI", protocol: "openai_compat", baseUrl: "https://api.openai.com/v1",
        keyUrl: "https://platform.openai.com/api-keys", hint: "Create a key under API keys in the OpenAI platform. It needs billing set up." },
    { id: "anthropic", name: "Anthropic", protocol: "anthropic_compat", baseUrl: "https://api.anthropic.com/v1",
        keyUrl: "https://console.anthropic.com/settings/keys", hint: "Create a key in the Anthropic Console under Settings → API keys." },
    { id: "openrouter", name: "OpenRouter", protocol: "openai_compat", baseUrl: "https://openrouter.ai/api/v1",
        keyUrl: "https://openrouter.ai/settings/keys", hint: "One key for models from many providers. Create one under Keys." },
    { id: "ollama", name: "Ollama", protocol: "openai_compat", baseUrl: LOCAL_SERVERS[0].baseUrl,
        keyUrl: null, hint: "Runs models on this computer. Install Ollama, then pull a model, for example: ollama pull qwen3" },
    { id: "lmstudio", name: "LM Studio", protocol: "openai_compat", baseUrl: LOCAL_SERVERS[1].baseUrl,
        keyUrl: null, hint: "Runs models on this computer. In LM Studio, load a model and start the server from the Developer tab." },
    { id: "other", name: "Other", protocol: "openai_compat", baseUrl: "",
        keyUrl: null, hint: "Any OpenAI-compatible endpoint: its base URL usually ends in /v1." },
];

const LOOPBACK = /^http:\/\/(127\.0\.0\.1|localhost|\[::1\]):\d+(\/|$)/i;

/** A server on this computer needs no key, matching locald's `local_no_auth`. */
export function needsKey(baseUrl: string): boolean {
    return !LOOPBACK.test(baseUrl.trim());
}

/** The preset a profile matches, "other" for any other address, or null
 *  before anything is chosen. */
export function presetFor(ai: Pick<OperatorAi, "protocol" | "base_url">, chosenOther = false): AiPreset | null {
    const url = ai.base_url.replace(/\/+$/, "");
    const other = AI_PRESETS.find((one) => one.id === "other")!;
    if (!url) return chosenOther ? other : null;
    return AI_PRESETS.find((one) => one.baseUrl && one.baseUrl === url && one.protocol === ai.protocol) ?? other;
}

export interface AiDraft {
    protocol: AiProtocol;
    baseUrl: string;
    /** From the last listing, or the saved profile's. */
    models: string[];
    defaultModel: string;
    imageModel: string;
    fastModel: string;
    /** Models the person says read images. The chosen image model is added
     *  by the daemon; this carries the rest of the saved list. */
    visionModels: string[];
    allowPrivateNetwork: boolean;
}

export function aiDraftFrom(ai: OperatorAi): AiDraft {
    const configured = ai.protocol === "openai_compat" || ai.protocol === "anthropic_compat";
    return {
        protocol: ai.protocol === "anthropic_compat" ? "anthropic_compat" : "openai_compat",
        baseUrl: configured ? ai.base_url : "",
        models: configured ? ai.models : [],
        defaultModel: configured ? ai.default_model : "",
        imageModel: configured ? ai.image_model : "",
        fastModel: configured ? ai.fast_model : "",
        visionModels: configured ? ai.vision_models : [],
        allowPrivateNetwork: ai.allow_private_network,
    };
}

/** The profile locald probes and saves. A model no longer in the list is
 *  dropped rather than sent: the daemon would refuse the whole save for it. */
export function aiProfile(draft: AiDraft, lastValidated: number | null): OperatorAi {
    const listed = (model: string) => (model && draft.models.includes(model) ? model : "");
    return {
        protocol: draft.protocol,
        base_url: draft.baseUrl.trim(),
        default_model: listed(draft.defaultModel),
        models: draft.models,
        vision_models: draft.visionModels.filter((model) => draft.models.includes(model)),
        allow_private_network: draft.allowPrivateNetwork,
        last_validated_at_unix_ms: lastValidated,
        image_model: listed(draft.imageModel),
        fast_model: listed(draft.fastModel),
    };
}

/** What asking the provider sends: the typed key, none for a local server,
 *  or nothing at all to use the stored key (which the daemon sends only to
 *  the address it was saved for). */
export function probeKey(draft: AiDraft, key: SecretIntent | undefined): { api_key?: string } {
    const typed = meaningfulIntent(key);
    if (typed?.action === "replace") return { api_key: typed.value };
    if (!needsKey(draft.baseUrl)) return { api_key: "" };
    return {};
}

/** Why the AI draft cannot be saved yet, or null when it can. */
export function aiDraftProblem(draft: AiDraft, key: SecretIntent | undefined, keyStored: boolean): string | null {
    if (!draft.baseUrl.trim()) return "Enter the provider’s address.";
    if (!/^https?:\/\//i.test(draft.baseUrl.trim())) return "The address starts with https:// (or http:// for a server on this computer).";
    const typed = meaningfulIntent(key);
    if (needsKey(draft.baseUrl) && !keyStored && typed?.action !== "replace") return "Enter an API key for this provider.";
    if (typed?.action === "remove" && needsKey(draft.baseUrl)) return "This provider needs a key. Enter a new one rather than removing it.";
    if (!draft.models.length) return "List the provider’s models, then choose one.";
    if (!draft.defaultModel || !draft.models.includes(draft.defaultModel)) return "Choose the model teammates use.";
    return null;
}

export function aiSectionPayload(
    snapshot: ThisMacSnapshot,
    draft: AiDraft,
    key: SecretIntent | undefined,
): SectionPayload {
    const typed = meaningfulIntent(key);
    return {
        expected_revision: snapshot.operator.config.revision,
        section: { name: "ai", value: aiProfile(draft, snapshot.operator.config.ai.last_validated_at_unix_ms) },
        secrets: typed ? { "ai.api_key": typed } : {},
    };
}

/** Picks for a fresh listing: whatever was already chosen and is still
 *  listed, and otherwise the first model as the default. The image and fast
 *  models are left for the person to choose -- a model list does not say
 *  which models read images or which are cheap, and guessing from names is a
 *  per-provider table this server should not keep. */
export function suggestModels(draft: AiDraft, models: string[]): AiDraft {
    const keep = (model: string) => (model && models.includes(model) ? model : "");
    return {
        ...draft,
        models,
        defaultModel: keep(draft.defaultModel) || models[0] || "",
        imageModel: keep(draft.imageModel),
        fastModel: keep(draft.fastModel),
    };
}

/* ── email ─────────────────────────────────────────────────────────── */

export interface EmailDraft {
    provider: EmailProvider;
    fromEmail: string;
    smtpHost: string;
    smtpPort: string;
    smtpUser: string;
    smtpUseTls: boolean;
}

export function emailDraftFrom(email: EmailConfig): EmailDraft {
    return {
        provider: email.provider,
        fromEmail: email.from_email,
        smtpHost: email.smtp_host,
        smtpPort: String(email.smtp_port || 587),
        smtpUser: email.smtp_user,
        smtpUseTls: email.smtp_use_tls,
    };
}

export const EMAIL_HINTS: Record<Exclude<EmailProvider, "none">, { steps: string; url: string; label: string }> = {
    resend: {
        steps: "In Resend, add and verify your domain, then create an API key with sending access. Send from an address on that domain.",
        url: "https://resend.com/api-keys",
        label: "Open Resend",
    },
    smtp: {
        steps: "Use your mail provider’s SMTP settings — for Gmail or Google Workspace, smtp.gmail.com on port 587 with an app password.",
        url: "https://support.google.com/accounts/answer/185833",
        label: "About app passwords",
    },
};

/** Why the email draft cannot be saved yet, or null. */
export function emailDraftProblem(
    snapshot: ThisMacSnapshot,
    draft: EmailDraft,
    secrets: Record<string, SecretIntent>,
): string | null {
    if (draft.provider === "none") return null;
    const from = draft.fromEmail.trim();
    if (!from || !/^[^\s@]+@[^\s@]+$/.test(from)) return "Enter the address mail is sent from.";
    const has = (key: string) => {
        const intent = meaningfulIntent(secrets[key]);
        return intent ? intent.action === "replace" : stored(snapshot, key);
    };
    if (draft.provider === "resend" && !has("surfaces.resend_api_key")) return "Enter your Resend API key.";
    if (draft.provider === "smtp") {
        if (!draft.smtpHost.trim() || /[\s/:]/.test(draft.smtpHost.trim())) return "Enter the SMTP server’s host name, without https:// or a port.";
        const port = Number(draft.smtpPort);
        if (!Number.isInteger(port) || port < 1 || port > 65535) return "Enter the SMTP port, usually 587.";
        if (!draft.smtpUser.trim()) return "Enter the SMTP user name.";
        if (!has("email.smtp_password")) return "Enter the SMTP password.";
    }
    return null;
}

/** The save the email card becomes: one change, whichever sections it spans.
 *
 *  The Resend key belongs to the channels' section — one Resend account
 *  carries mail in and out — so a new key and the email section that sends
 *  with it travel together, and the server restarts once for both. Empty
 *  when nothing changed. */
export function emailPayloads(
    snapshot: ThisMacSnapshot,
    draft: EmailDraft,
    secrets: Record<string, SecretIntent>,
): (SectionPayload | SectionsPayload)[] {
    const parts = emailSections(snapshot, draft, secrets);
    if (parts.length < 2) return parts;
    return [{
        expected_revision: snapshot.operator.config.revision,
        sections: parts.map((part) => part.section),
        secrets: Object.assign({}, ...parts.map((part) => part.secrets)),
    }];
}

function emailSections(
    snapshot: ThisMacSnapshot,
    draft: EmailDraft,
    secrets: Record<string, SecretIntent>,
): SectionPayload[] {
    const config = snapshot.operator.config;
    const payloads: SectionPayload[] = [];
    const resendKey = meaningfulIntent(secrets["surfaces.resend_api_key"]);
    if (resendKey) {
        payloads.push({
            expected_revision: config.revision,
            section: { name: "surfaces", value: { ...config.surfaces } as SurfaceConfig },
            secrets: { "surfaces.resend_api_key": resendKey },
        });
    }
    const value: EmailConfig = {
        provider: draft.provider,
        from_email: draft.fromEmail.trim(),
        smtp_host: draft.smtpHost.trim(),
        smtp_port: Number(draft.smtpPort) || 587,
        smtp_user: draft.smtpUser.trim(),
        smtp_use_tls: draft.smtpUseTls,
    };
    const password = meaningfulIntent(secrets["email.smtp_password"]);
    const changed = password !== null || (Object.keys(value) as (keyof EmailConfig)[]).some((key) => value[key] !== config.email[key]);
    if (changed) {
        payloads.push({
            expected_revision: config.revision,
            section: { name: "email", value },
            secrets: password ? { "email.smtp_password": password } : {},
        });
    }
    return payloads;
}

/** Whether nothing anywhere can think: this server has no model, and the
 *  organization has no provider key either. `undefined` while either is
 *  still being read, which is not the same as "none". */
export function noModelAnywhere(
    snapshot: ThisMacSnapshot | null | undefined,
    runtimes: { kind: string; archived: boolean }[] | undefined,
): boolean {
    if (!snapshot || runtimes === undefined) return false;
    return !aiReady(snapshot) && !runtimes.some((runtime) => runtime.kind === "key" && !runtime.archived);
}

/* ── the first-run checklist ───────────────────────────────────────── */

const CHECKLIST_KEY = "lemma.server-setup.checklist";

/** Whether the first-run checklist has been put away on this install.
 *  Browser storage, because it is a display preference; a storage that
 *  throws or forgets only means the checklist shows once more. */
export function checklistDismissed(storage: Pick<Storage, "getItem"> | null = safeStorage()): boolean {
    try {
        return storage?.getItem(CHECKLIST_KEY) === "done";
    } catch {
        return false;
    }
}

export function dismissChecklist(storage: Pick<Storage, "setItem"> | null = safeStorage()): void {
    try {
        storage?.setItem(CHECKLIST_KEY, "done");
    } catch {
        /* The checklist shows again next time; nothing else depends on it. */
    }
}

function safeStorage(): Storage | null {
    try {
        return typeof window === "undefined" ? null : window.localStorage;
    } catch {
        return null;
    }
}

/** Whether to show the checklist: on a local install whose settings this
 *  window can change, once, until it is put away. */
export function showChecklist({ shown, snapshot, dismissed }: {
    shown: boolean;
    snapshot: ThisMacSnapshot | null | undefined;
    dismissed: boolean;
}): boolean {
    return shown && Boolean(snapshot) && !dismissed;
}

/** One sentence for a Test that failed, without the daemon's plumbing. */
export function testFailure(reason: unknown): string {
    const message = reason instanceof Error ? reason.message : String(reason ?? "");
    const cleaned = message.replace(/^Error:\s*/, "").trim();
    return cleaned ? cleaned.charAt(0).toUpperCase() + cleaned.slice(1) : "The test didn’t work.";
}
