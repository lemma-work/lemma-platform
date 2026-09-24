/** Reading the connectable-surface catalog.
 *
 *  `podSurfaces.available()` answers the question a connect button is really
 *  being asked: **how much work is this going to be?** It knows, per platform,
 *  whether a Lemma-run bot can answer with no account of yours, whether this
 *  organization has already spent its one claim on that bot, whether a guided
 *  setup exists, and what connecting your own account would involve.
 *
 *  All of that is on the wire and none of it is legible from a platform name.
 *  Four identical grey icons leave the difference between "one click" and
 *  "ask your Slack admin" to be discovered by clicking. The whole point of
 *  this file is that the effort is knowable **before** the click, so the UI
 *  can sort by it and say it out loud.
 *
 *  The backend is the authority on the rules; this only projects them:
 *    - `supported_credential_modes` containing SYSTEM means a Lemma-managed
 *      identity exists in this deployment. It is configured per environment,
 *      so it is never assumed from the platform name.
 *    - `system_claim.available` is whether this org can still take it. The
 *      shared identity is claimable exactly once per organization — which is
 *      why it is reported here rather than discovered as a failed save.
 *    - `managed_setup_available` is the guided path that ends with a bot of
 *      your own instead of a shared one.
 *
 *  Pure, so the rules are testable without a session. */

export type ConnectEffort = "instant" | "guided" | "account" | "unavailable";

export interface Connectable {
    /** SLACK | TEAMS | WHATSAPP | TELEGRAM | RESEND */
    platform: string;
    /** The catalog entry an account is authorised against. */
    connectorId: string;
    title: string;
    description: string;
    /** The cheapest way in that is actually open right now. */
    effort: ConnectEffort;
    /** A Lemma-run identity exists for this platform in this deployment. */
    system: boolean;
    /** ...and this organization has not already claimed it. */
    systemFree: boolean;
    /** The pod holding the claim, when somebody else took it. */
    claimedBy?: { podId?: string; surfaceName?: string };
    /** A guided setup that ends with an identity of your own. */
    guided: boolean;
    /** Connecting your own account is possible at all. */
    account: boolean;
    /** Lemma supplies the OAuth app, so there is no app to register first. */
    hostedOAuth: boolean;
    /** Email surfaces are minted on this domain. */
    emailDomain?: string;
    credentialSchema?: unknown;
    kind?: string;
}

interface RawConnectable {
    platform?: string;
    connector_id?: string;
    title?: string | null;
    description?: string | null;
    connector_available?: boolean;
    managed_setup_available?: boolean;
    email_domain?: string | null;
    supported_credential_modes?: string[];
    system_claim?: { available?: boolean; claimed_by_pod_id?: string | null; claimed_by_surface_name?: string | null } | null;
    kind?: string;
    connect?: { system_oauth_available?: boolean; credential_schema?: unknown } | null;
}

/** What the fastest open route actually is.
 *
 *  Order matters and is not arbitrary: a platform that offers both a shared
 *  identity and a guided one leads with the shared one, because that is one
 *  click against roughly a minute. Once the shared identity is spoken for, the
 *  guided path is the cheapest thing left — and it is a better outcome anyway,
 *  since the bot carries your own name. */
export function effortOf(entry: Connectable): ConnectEffort {
    if (entry.system && entry.systemFree) return "instant";
    if (entry.guided) return "guided";
    if (entry.account) return "account";
    return "unavailable";
}

export function readConnectable(raw: unknown): Connectable | null {
    const entry = (raw ?? {}) as RawConnectable;
    const platform = String(entry.platform ?? "").toUpperCase();
    if (!platform) return null;

    const modes = (entry.supported_credential_modes ?? []).map((mode) => String(mode).toUpperCase());
    const claim = entry.system_claim ?? null;
    const system = modes.includes("SYSTEM");

    const base: Connectable = {
        platform,
        connectorId: String(entry.connector_id ?? "").trim(),
        title: String(entry.title ?? "").trim(),
        description: String(entry.description ?? "").trim(),
        effort: "unavailable",
        system,
        /* No claim block on a platform that has a system identity means the
           backend had nothing to report against it, not that it is taken.
           Treating silence as "taken" hides the one-click path; treating it as
           "free" is recoverable — the save says so. */
        systemFree: system && claim?.available !== false,
        claimedBy:
            claim?.claimed_by_pod_id || claim?.claimed_by_surface_name
                ? { podId: claim.claimed_by_pod_id ?? undefined, surfaceName: claim.claimed_by_surface_name ?? undefined }
                : undefined,
        guided: entry.managed_setup_available === true,
        account: entry.connector_available === true,
        hostedOAuth: entry.connect?.system_oauth_available === true,
        emailDomain: entry.email_domain ?? undefined,
        credentialSchema: entry.connect?.credential_schema,
        kind: entry.kind,
    };

    return { ...base, effort: effortOf(base) };
}

/** What a channel is, in the reader's terms.
 *
 *  The catalog's own `description` is connector documentation — "Credential-
 *  managed Telegram bot surface connector for agent messaging" is accurate and
 *  is not a sentence to put in front of somebody deciding where their
 *  colleague should answer. It is kept only as the fallback for a platform
 *  this app has never heard of, where developer copy beats a blank line. */
const BLURB: Record<string, string> = {
    TELEGRAM: "A bot people message directly.",
    WHATSAPP: "On the number people already message.",
    RESEND: "An address of its own. Write to it like a colleague.",
    SLACK: "In the channels your team already works in.",
    TEAMS: "For teams that live in Microsoft 365.",
};

export function blurbOf(entry: Connectable): string {
    return BLURB[entry.platform] ?? entry.description;
}

/** Cheapest first, and never show a dead end above a live one. */
const RANK: Record<ConnectEffort, number> = { instant: 0, guided: 1, account: 2, unavailable: 3 };

export function byEffort(a: Connectable, b: Connectable): number {
    return RANK[a.effort] - RANK[b.effort] || a.platform.localeCompare(b.platform);
}
