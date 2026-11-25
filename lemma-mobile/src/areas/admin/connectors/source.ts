import type { ConnectOutcome } from "../connect/outcome";
import type { Connector, ConnectorAccount } from "@web/data/accounts";
import type { CatalogEntry, Install } from "@web/connect/install";

export type { Connector, ConnectorAccount } from "@web/data/accounts";
export type { CatalogEntry, Install } from "@web/connect/install";

/** One catalog entry read on its own: its kinds with their schemas (the list
 *  carries kinds, not reliably the heavy schemas), and the redirect URI an
 *  organization's own OAuth app must allow. */
export interface ConnectorDetail {
    entry: CatalogEntry;
    /** The deployment's OAuth callback, as the server publishes it; null from
     *  an older server. Never built here (`redirect.ts`). */
    redirectUri: string | null;
}

/** What a new install is made of: a connector, which of its kinds, a name
 *  unique in the organization, and its settings. `own` when the organization
 *  supplies the credentials (its own OAuth app, or a server it points at). */
export interface NewInstall {
    connectorId: string;
    kind?: string;
    name?: string;
    config?: Record<string, unknown>;
    own: boolean;
}

/** What re-reading an install's operations did: `ok`, `not_applicable` or
 *  `failed`, with the count and the server's reason (`discoveryNote` says it). */
export interface Discovery {
    status: string | null;
    count: number | null;
    reason: string | null;
}

/** What changing an install did beyond saving it. */
export interface InstallUpdate {
    install: Install;
    /** Null when the change did not move where operations come from. */
    discovery: Discovery | null;
    /** Accounts now asked to sign in again: the change pointed the install
     *  somewhere their credential is not for. Kept, never deleted. */
    reauth: number;
}

/** An account as it stood before a sign-in, to tell afterwards what the
 *  sign-in changed: a new account, or one brought back to health. */
export interface AccountBefore {
    id: string;
    usable: boolean;
    status: string;
    installState: string;
}

/** A sign-in that came back with nothing new yet, kept so it can be looked
 *  for again ("Check again"). */
export interface PendingSignIn {
    connectorId: string;
    authConfigId: string;
    before: AccountBefore[];
}

/** How a sign-in through the provider ended, read off the accounts.
 *
 *  - `connected`: an account usable now that was not before.
 *  - `unfinished`: a new account that cannot reach anything yet (a GitHub
 *    App authorised and not installed); `trouble` says what it needs.
 *  - `nothing`: the page was closed before it finished, or the account is
 *    still on its way; `pending` is what to look for again.
 *  - `refused`: the provider or the server said no; `reason` in its words.
 *  - `no-page`: the server offered no page to sign in on. */
export type SignInResult =
    | { kind: "connected"; accountId: string }
    | { kind: "unfinished"; accountId: string; trouble: string }
    | { kind: "nothing"; pending: PendingSignIn }
    | { kind: "refused"; reason: string }
    | { kind: "no-page" };

/** One GitHub App installation an account could speak for. */
export interface InstallationChoice {
    installationId: string;
    login: string | null;
    /** "Organization" or "User". */
    type: string | null;
    /** "all" or "selected" repositories. */
    repositories: string | null;
}

/** What finishing an authorised-but-not-installed account came to:
 *  installed (already, or now), a choice between installations, not yet
 *  (the provider's page was closed first), or no page offered. */
export type InstallStep =
    | { kind: "installed" }
    | { kind: "choices"; choices: InstallationChoice[] }
    | { kind: "not-yet" }
    | { kind: "no-page" };

/** The organization's connectors: the catalog, accounts, installs and their
 *  credentials (PARITY.md, F; the web's `org/connectors.tsx` and `connect/*`).
 *
 *  Everything this part reads or changes. The live side is `live.ts`, the
 *  sample side `sample.ts`; screens reach it through `hooks.ts`, never the
 *  SDK directly, so sample data and a live account are the same screens.
 *
 *  Accounts are the signed-in person's own in the organization: the server
 *  lists, changes and removes only those. Installs are the organization's;
 *  making, changing or removing one needs an owner or an editor. */
export interface ConnectorsSource {
    /** Every connector this deployment offers, by title. */
    catalog(): Promise<Connector[]>;
    /** One connector with its kinds' schemas and the OAuth redirect URI. */
    detail(connectorId: string): Promise<ConnectorDetail>;
    /** The signed-in person's accounts in the organization, every connector. */
    accounts(orgId: string): Promise<ConnectorAccount[]>;
    /** The organization's installs (auth configs), every connector. */
    installs(orgId: string): Promise<Install[]>;
    /** One install read fresh, for the form that edits it: its config comes
     *  back with every secret masked. */
    install(orgId: string, install: Install): Promise<Install>;

    /** Sign an account in through the provider: settle the install (the one
     *  named, else the organization's default, else Lemma's own, made now),
     *  open the provider's page in the phone's browser, and read the accounts
     *  when the person comes back. `fields` are what the sign-in needs first
     *  (Shopify's store). The sample stands in for the provider. */
    signIn(orgId: string, input: { connectorId: string; installId: string | null; fields?: Record<string, unknown> }): Promise<SignInResult>;
    /** Look again for what a sign-in made, after "nothing yet". */
    checkSignIn(orgId: string, pending: PendingSignIn): Promise<SignInResult>;
    /** Connect an account by handing over a credential; answers its id. */
    createAccount(orgId: string, installId: string, credentials: Record<string, unknown>): Promise<string>;
    /** Remove an account. Everything pinned to it stops. */
    disconnect(orgId: string, accountId: string): Promise<void>;
    /** Replace a credential-managed account's credential, keeping its id. */
    rotate(orgId: string, accountId: string, credentials: Record<string, unknown>): Promise<void>;
    /** Finish an account that is authorised and not installed: ask the
     *  provider first, then open its install page when there is one to open. */
    finishInstall(orgId: string, accountId: string): Promise<InstallStep>;
    /** Settle which installation an account speaks for. */
    bindInstallation(orgId: string, accountId: string, installationId: string): Promise<void>;

    createInstall(orgId: string, input: NewInstall): Promise<Install>;
    /** Change an install in place: its name, its settings. Masked secrets
     *  sent back are kept by the server. */
    updateInstall(orgId: string, install: Install, change: { name?: string; config?: Record<string, unknown> }): Promise<InstallUpdate>;
    /** Make an install the one a bare connector resolves to. */
    makeDefault(orgId: string, install: Install): Promise<void>;
    /** Re-read an install's operations (an MCP server's tools, an API's spec). */
    refreshOperations(orgId: string, install: Install): Promise<Discovery>;
    /** Remove an install, and every account on it with it. */
    deleteInstall(orgId: string, install: Install): Promise<void>;
}

/** The outcome a provider's page reported, when the round trip ended on the
 *  app's own address with one (`connect/outcome.ts`). */
export type { ConnectOutcome };
