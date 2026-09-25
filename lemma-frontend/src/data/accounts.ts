/** Connectors, and the accounts connected to them.
 *
 *  Two corrections live in this file, both of them the same mistake: reading
 *  fields that were never on the wire.
 *
 *  The settings page rendered `name`, `slug`, `logo` and
 *  `connected_accounts_count`. The schema has `title`, `icon`, `description`
 *  and `kinds`. Every name silently fell back to the raw connector id and no
 *  logo ever loaded, which is what made that page look like a placeholder —
 *  it was reading a shape nothing sends.
 *
 *  And an account's status is `CONNECTED`. It was being compared against
 *  `"ACTIVE"`, so a perfectly good account never counted as usable and the
 *  authorisation poll in the reach sheet waited forever for something that
 *  had already arrived.
 *
 *  Pure, because both of those were the kind of thing a test catches and a
 *  screenshot does not. */

import type { ConnectorKind } from "@/connect/install";

export interface Connector {
    id: string;
    title: string;
    description: string;
    icon: string;
    active: boolean;
    /** How this one can be installed.
     *
     *  Carried whole from the list, schemas included. Whether a connector is
     *  one an organization points somewhere itself, signs into, or hands a
     *  token to is read off its auth scheme and install schema — the kind name
     *  alone cannot say, because `http` is GitHub and WhatsApp as well as every
     *  OpenAPI spec. Stripped to the name, every one of those was drawn as a
     *  server to add. */
    kinds: ConnectorKind[];
}

export interface ConnectorAccount {
    id: string;
    connectorId: string;
    /** The install this account was authorised against.
     *
     *  Needed because a connector may have several, deliberately — and
     *  replacing a credential has to be validated against the schema of the
     *  one this account actually belongs to, not whichever install happens to
     *  be first. */
    authConfigId: string;
    /** Who this account is, when the provider gave us a name for it. Empty
     *  when it did not — an opaque provider id is not a name, and showing one
     *  where a name goes ("Using U077S2UCG4S") reads like debug output. */
    label: string;
    /** The provider's own id. Not a name; kept because it is the only thing
     *  that tells two unnamed accounts apart. */
    ref: string;
    /** CONNECTED | REAUTH_REQUIRED | DISCONNECTED */
    status: string;
    /** READY | INSTALL_REQUIRED | CHOOSE_INSTALL | PENDING_APPROVAL */
    installState: string;
    /** Connected, and able to actually reach anything. */
    usable: boolean;
    isDefault: boolean;
    createdAt: string;
}

function asString(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

export function readConnector(raw: unknown): Connector | null {
    const entry = (raw ?? {}) as {
        id?: string;
        title?: string | null;
        description?: string | null;
        icon?: string | null;
        is_active?: boolean;
        kinds?: unknown[];
    };
    const id = asString(entry.id);
    if (!id) return null;
    return {
        id,
        /* `title` is the display name. Falling back to the id is right — it is
           a readable slug — but it must be the fallback, not the only path. */
        title: asString(entry.title) || id,
        description: asString(entry.description),
        icon: asString(entry.icon),
        active: entry.is_active !== false,
        kinds: (Array.isArray(entry.kinds) ? entry.kinds : [])
            .filter((one): one is ConnectorKind =>
                Boolean(one) && typeof one === "object" && asString((one as { kind?: unknown }).kind) !== ""),
    };
}

/** Whether an account can actually be put behind a surface.
 *
 *  Both halves matter and they fail differently. A `REAUTH_REQUIRED` account
 *  exists and its token is dead. An account whose `install_state` is anything
 *  but `READY` has a live token that can reach nothing — the schema says so in
 *  as many words — and binding a surface to one produces a channel that exists
 *  and does not work, which is worse than refusing. */
export function accountUsable(status: string, installState: string): boolean {
    if (status.toUpperCase() !== "CONNECTED") return false;
    const install = installState.toUpperCase();
    return install === "" || install === "READY";
}

export function readAccount(raw: unknown): ConnectorAccount | null {
    const entry = (raw ?? {}) as {
        id?: string;
        connector_id?: string;
        auth_config_id?: string | null;
        display_name?: string | null;
        email?: string | null;
        provider_account_id?: string | null;
        status?: string;
        install_state?: string;
        is_default?: boolean;
        created_at?: string;
    };
    const id = asString(entry.id);
    if (!id) return null;
    const status = asString(entry.status);
    const installState = asString(entry.install_state);
    return {
        id,
        connectorId: asString(entry.connector_id),
        authConfigId: asString(entry.auth_config_id),
        label: asString(entry.display_name) || asString(entry.email),
        ref: asString(entry.provider_account_id),
        status,
        installState,
        usable: accountUsable(status, installState),
        isDefault: entry.is_default === true,
        createdAt: asString(entry.created_at),
    };
}

/** What to call an account on screen.
 *
 *  A provider id is never the answer: `U077S2UCG4S` is not a workspace anybody
 *  recognises, and putting it where a name belongs makes a finished screen read
 *  like a placeholder. Without a name, the connector is the honest description
 *  — there is usually only one, and the id is shown beside it when there is
 *  more than one to tell apart. */
export function accountName(account: ConnectorAccount, connectorTitle: string): string {
    if (account.label) return account.label;
    return connectorTitle ? "your " + connectorTitle + " account" : "a connected account";
}

/** What to tell somebody about an account that is not usable.
 *
 *  Four states rather than a boolean, because the unfinished ones need
 *  different things from the person — telling someone to reconnect when they
 *  are waiting on an organization owner is advice that cannot succeed. */
export function accountTrouble(account: ConnectorAccount): string {
    if (account.usable) return "";
    if (account.status.toUpperCase() === "REAUTH_REQUIRED") return "Needs signing in again";
    if (account.status.toUpperCase() === "DISCONNECTED") return "Disconnected";
    const install = account.installState.toUpperCase();
    if (install === "INSTALL_REQUIRED") return "Needs installing before it can reach anything";
    if (install === "CHOOSE_INSTALL") return "Waiting for you to pick an installation";
    if (install === "PENDING_APPROVAL") return "Waiting on an organization owner";
    return "Not usable yet";
}

/** The account a surface should be built on: a usable one, preferring the
 *  org's default, then the newest. */
export function bestAccount(accounts: ConnectorAccount[], connectorId: string): ConnectorAccount | null {
    const usable = accounts.filter((account) => account.connectorId === connectorId && account.usable);
    if (usable.length === 0) return null;
    return (
        usable.find((account) => account.isDefault) ??
        [...usable].sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0]
    );
}

/** The write-side refusal when a credential is already spoken for.
 *
 *  A connected account is claimable **once per organization**. The available-
 *  surfaces catalog publishes the *system* claim up front, but there is no
 *  equivalent for an account: this 409 is the first and only signal, and it
 *  names the surface holding it.
 *
 *  It is deliberately not treated as an error to report and stop at. The thing
 *  to do next is not "try again" — it is to give this teammate an identity of
 *  its own, because one Slack app is one bot user and two teammates behind one
 *  bot is not a thing anybody wants. */
export function isCredentialConflict(error: unknown): boolean {
    if (!error) return false;
    const text = error instanceof Error ? error.message : String(error);
    return /already used by another surface|AGENT_SURFACE_CREDENTIAL_CONFLICT/i.test(text);
}
