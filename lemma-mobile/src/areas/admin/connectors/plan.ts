import { accountTrouble, type ConnectorAccount } from "@web/data/accounts";
import {
    canBringOwnApp, canInstallWithDefaults, connectRoute, connectSchema, discoveryNote, freshInstallName, installSchema,
    isBringYourOwn, isStaleDefault, kindFor, kindNamed, needsOwnApp, ownAppKinds, registersLemmaRedirect,
    type CatalogEntry, type ConnectRoute, type ConnectorKind, type Install,
} from "@web/connect/install";
import { fields, payload, type Field, type Values } from "@web/connect/schema";
import type { MayManage } from "./model";
import type { ConnectorsSource } from "./source";

/** What each connect, add, edit and replace screen decides, and the writes
 *  each one is made of: the web's `connect/connect-dialog.tsx`,
 *  `add-connector.tsx`, `edit-install.tsx` and the account and install rows
 *  of `org/connectors.tsx`, without their drawing. Pure but for the writes,
 *  which go through whichever source is in use, so a test can run every
 *  flow against the sample. */

/** Which of the connect screen's forms is drawn.
 *
 *  - `app-form`: the organization's own OAuth app, or the details an
 *    install needs, has to come first.
 *  - `needs-manager`: it does, and this person may not make one.
 *  - `redirect`: a sign-in through the provider, nothing to ask first.
 *  - `redirect-form`: a sign-in that needs a question answered first.
 *  - `credentials`: a credential typed in and posted.
 *  - `nothing-to-ask`: a connector that takes no credential at all.
 *  - `undescribed`: a schema with no fields, which is not a form. */
export type ConnectMode = "app-form" | "needs-manager" | "redirect" | "redirect-form" | "credentials" | "nothing-to-ask" | "undescribed";

export interface ConnectPlan {
    kind: ConnectorKind | null;
    mode: ConnectMode;
    route: ConnectRoute;
    /** The form, when there is one. */
    list: Field[];
    /** The screen's heading. */
    title: string;
    /** What the screen says before its form. */
    lead: string | null;
    /** "Use your own app" beside Continue. */
    offerOwnApp: boolean;
    /** The redirect URI to copy: the organization's own app, signing in
     *  through Lemma's callback. */
    showRedirectUri: boolean;
    submit: string;
}

/** Connecting an account, by whichever route this connector is on. Three
 *  things can be in the way and they need different work from the person:
 *  the organization's own app first, a credential form, or a browser round
 *  trip (which can itself need a question answered first). */
export function planConnect({ entry, title, against, bringingApp, manage }: {
    entry: CatalogEntry;
    title: string;
    /** The install to connect against; null when it has none yet, and one is
     *  made on the way. */
    against: Install | null;
    /** The person asked for the organization's own app. */
    bringingApp: boolean;
    manage: MayManage;
}): ConnectPlan {
    const usual = kindFor(entry, against);
    /* The kinds an organization's own app can stand behind: not only the one
       an install would take by default. Where Composio is offered beside a
       native kind, Composio is the default and never takes an organization's
       app. */
    const appKinds = ownAppKinds(entry);
    const kind = bringingApp && !canBringOwnApp(usual) ? appKinds[0] ?? usual : usual;
    /* Nothing to connect against, and Lemma cannot make it alone. */
    const ownApp = (!against || isStaleDefault(against, kind)) && (needsOwnApp(kind) || !canInstallWithDefaults(kind));
    const showingApp = ownApp || bringingApp;
    const list = fields(showingApp ? installSchema(kind) : connectSchema(kind));
    const route = connectRoute(against, kind);
    const nothingToAsk = kind?.auth_scheme === "NOAUTH" || against?.auth_scheme === "NOAUTH";
    const signsIn = kind?.auth_scheme === "OAUTH2";

    const mode: ConnectMode = showingApp && list.length > 0 && manage.known && !manage.may ? "needs-manager"
        : !showingApp && route === "redirect" && list.length === 0 ? "redirect"
            : !showingApp && list.length === 0 && nothingToAsk ? "nothing-to-ask"
                : list.length === 0 ? "undescribed"
                    : showingApp ? "app-form"
                        : route === "redirect" ? "redirect-form" : "credentials";

    const lead = mode === "needs-manager"
        ? `Only an owner or editor of this organization can set up ${title}. Ask one of them; then anybody can connect an account.`
        : mode === "app-form"
            ? !signsIn
                ? `${title} needs a few details from your organization before anyone can connect it.`
                : ownApp
                    ? "Lemma holds no credentials for this one, so it connects through an app you register yourself."
                    : "Signing in will run through your app rather than Lemma’s."
            : mode === "redirect"
                ? `Signs in through ${title}, in your browser. When it says you are done, close the page to come back here.`
                : mode === "redirect-form"
                    ? `Signs in through ${title}, once it knows which account you mean.`
                    : mode === "nothing-to-ask"
                        ? "Nothing to fill in for this one."
                        : mode === "undescribed"
                            ? "This connector has not described what it needs, so it cannot be connected from here yet."
                            : null;

    return {
        kind,
        mode,
        route,
        list,
        title: showingApp ? (signsIn ? `Use your own ${title} app` : `Set up ${title}`) : `Connect ${title}`,
        lead,
        offerOwnApp: mode === "redirect" && appKinds.length > 0 && !ownApp && manage.may && against?.config_source !== "ORG_CUSTOM",
        showRedirectUri: showingApp && registersLemmaRedirect(kind),
        submit: mode === "app-form" ? (signsIn ? "Save and sign in" : "Save")
            : mode === "redirect" || mode === "redirect-form" ? "Continue" : "Connect",
    };
}

/** The install a fresh connect runs against: the organization's default,
 *  else the first that is on, else none (one is made on the way). */
export function connectTarget(installs: readonly Install[], named: string | null): Install | null {
    const on = installs.filter((one) => one.status !== "DISABLED");
    if (named) return installs.find((one) => one.id === named) ?? null;
    return on.find((one) => one.is_default) ?? on[0] ?? null;
}

/** What submitting the connect screen came to: a sign-in to open next, the
 *  next form (an app of the organization's own was made and now an account
 *  goes on it), or an account connected. */
export type ConnectStep =
    /* `made`: the organization's own app, just made, which a sign-in that
       fails is retried on rather than making another. */
    | { next: "sign-in"; installId: string | null; fields?: Record<string, unknown>; made?: Install }
    | { next: "form"; made: Install }
    | { next: "done"; accountId: string };

export async function submitConnect(source: ConnectorsSource, orgId: string, input: {
    plan: ConnectPlan;
    connectorId: string;
    against: Install | null;
    takenNames: readonly string[];
    values: Values;
}): Promise<ConnectStep> {
    const { plan, connectorId, against, takenNames, values } = input;
    const body = payload(plan.list, values);
    if (plan.mode === "app-form") {
        /* The organization's own app has to exist before anybody can sign in
           through it. Named apart from the install Lemma's own app lives on,
           which already holds the connector's name. */
        const created = await source.createInstall(orgId, {
            connectorId,
            kind: plan.kind?.kind,
            name: freshInstallName(connectorId, takenNames),
            config: body,
            own: true,
        });
        if (connectRoute(created, plan.kind) === "redirect" && fields(connectSchema(plan.kind)).length === 0) {
            return { next: "sign-in", installId: created.id, made: created };
        }
        return { next: "form", made: created };
    }
    if (plan.mode === "redirect" || plan.mode === "redirect-form") {
        return { next: "sign-in", installId: against?.id ?? null, ...(plan.mode === "redirect-form" ? { fields: body } : {}) };
    }
    /* A credential, posted. The account is authorised against an install
       and never against a connector: Lemma's own install is made now when
       there is none, and removed again if the account is refused, so a
       retry is not refused for the name it holds. */
    let installId = against?.id ?? null;
    let madeHere: Install | null = null;
    if (!installId) {
        madeHere = await source.createInstall(orgId, { connectorId, kind: plan.kind?.kind, name: freshInstallName(connectorId, takenNames), own: false });
        installId = madeHere.id;
    }
    try {
        return { next: "done", accountId: await source.createAccount(orgId, installId, body) };
    } catch (problem) {
        if (madeHere) await source.deleteInstall(orgId, madeHere).catch(() => undefined);
        throw problem;
    }
}

/** What an account's screen offers (the web's account row). Four states
 *  rather than a boolean: each unfinished one needs a different thing, and
 *  "sign in again" is advice that cannot succeed when an owner is awaited. */
export interface AccountActs {
    /** Signed out or disconnected. */
    dead: boolean;
    /** Connected and reaching nothing: install it, or pick an installation. */
    unfinished: boolean;
    choose: boolean;
    /** Sign in again on the same install: the server refreshes the account
     *  in place, so everything pinned to it stays. */
    reconnect: boolean;
    /** Replace the credential in place. Never for an account that signs in
     *  through the provider: that is re-authorised, not retyped. */
    rotate: boolean;
}

export function accountActs(account: ConnectorAccount, install: Install | null, entry: CatalogEntry | null): AccountActs {
    const status = account.status.toUpperCase();
    const state = account.installState.toUpperCase();
    const credentialed = Boolean(install && entry && connectRoute(install, kindFor(entry, install)) === "credentials");
    const dead = status === "REAUTH_REQUIRED" || status === "DISCONNECTED";
    return {
        dead,
        unfinished: status === "CONNECTED" && (state === "INSTALL_REQUIRED" || state === "CHOOSE_INSTALL"),
        choose: state === "CHOOSE_INSTALL",
        reconnect: dead && Boolean(install) && !credentialed,
        rotate: Boolean(install) && credentialed,
    };
}

/** What an install's screen offers (the web's install row). */
export interface InstallActs {
    /** Pointed somewhere by the organization: its operations are discovered
     *  per install, so re-reading them means something. */
    addressed: boolean;
    off: boolean;
    /** Signed into through a browser, with nobody signed in yet. */
    needsSignIn: boolean;
    /** "connect" opens the connect screen on this install; "sign-in" signs
     *  in straight away (an added server connects its one account when it is
     *  added, so signing in is all that is left); null offers neither. */
    connect: "connect" | "sign-in" | null;
    /** Only where the organization set it up itself: Lemma's own app has
     *  nothing in it for anybody here to change. */
    editable: boolean;
}

export function installActs(install: Install, entry: CatalogEntry, accounts: readonly ConnectorAccount[]): InstallActs {
    const addressed = isBringYourOwn(entry);
    const on = accounts.filter((account) => account.authConfigId === install.id);
    const credentialed = connectRoute(install, kindFor(entry, install)) === "credentials";
    const signIn = !credentialed && !on.some((account) => account.usable);
    const off = install.status === "DISABLED";
    return {
        addressed,
        off,
        needsSignIn: addressed && signIn,
        connect: off ? null : addressed ? (signIn ? "sign-in" : null) : "connect",
        editable: addressed || install.config_source === "ORG_CUSTOM",
    };
}

/** The credential form an account is replaced with: the schema of the
 *  install it actually belongs to, not whichever install is first. */
export function rotateFields(entry: CatalogEntry, install: Install | null): Field[] {
    return fields(connectSchema(kindNamed(entry, install?.kind)));
}

/** What adding a server, an API or a database came to. */
export interface Added {
    made: Install;
    /** Signs in through a browser: nobody can use it until someone does. */
    signIn: boolean;
    note: string;
    bad: boolean;
}

/** Point the organization at a server of its own: the install (its
 *  address) and the account (its token) asked in one sitting, then its
 *  operations read. Creating the install is what asks an MCP server how it
 *  wants to be authorised, so only then is it known whether it signs in
 *  through a browser; one that does gets no empty credential posted, which
 *  would look connected and be refused on every call. */
export async function addOwn(source: ConnectorsSource, orgId: string, input: {
    entry: CatalogEntry;
    kind: string;
    name: string;
    config: Values;
    credentials: Values;
}): Promise<Added> {
    const spec = kindNamed(input.entry, input.kind);
    const made = await source.createInstall(orgId, { connectorId: input.entry.id, kind: input.kind, name: input.name, config: input.config, own: true });
    if (connectRoute(made, spec) === "redirect") {
        return { made, signIn: true, note: "Added. Sign in to finish connecting it.", bad: false };
    }
    /* Always an account, even with nothing in it: every run resolves one,
       and without it the install could never be used. */
    try {
        await source.createAccount(orgId, made.id, input.credentials);
    } catch (problem) {
        await source.deleteInstall(orgId, made).catch(() => undefined);
        throw problem;
    }
    /* Discovery can fail on its own after a perfectly good install: a server
       that is up, reachable and refuses to list. Said, not folded in. */
    try {
        const found = await source.refreshOperations(orgId, made);
        return { made, signIn: false, note: discoveryNote(found.status, found.count, null), bad: found.status === "failed" };
    } catch {
        return { made, signIn: false, note: "Connected, but its operations could not be read just now.", bad: true };
    }
}

/** What saving an install's changes came to: nothing to say (the screen
 *  just closes), or a note about re-discovery and accounts asked to sign in
 *  again. */
export type Saved = { said: false } | { said: true; note: string; bad: boolean; reauth: number };

export async function saveInstall(source: ConnectorsSource, orgId: string, input: {
    install: Install;
    name: string;
    list: Field[];
    values: Values;
}): Promise<Saved> {
    const wanted = input.name.trim();
    const renamed = wanted !== (input.install.name || "");
    const answer = await source.updateInstall(orgId, input.install, {
        ...(renamed ? { name: wanted } : {}),
        /* Masks sent back, not left out: the server validates what it is
           sent before merging it, and swaps each mask for what it holds. */
        ...(input.list.length > 0 ? { config: payload(input.list, input.values, { editing: true }) } : {}),
    });
    const status = answer.discovery?.status ?? null;
    /* Re-discovery only runs when the change moved where operations come
       from. Without it, and with nobody asked to sign in again, there is
       nothing to say beyond the list redrawing. */
    const rediscovered = Boolean(status && status !== "not_applicable");
    if (!rediscovered && answer.reauth === 0) return { said: false };
    return {
        said: true,
        note: rediscovered ? discoveryNote(status, answer.discovery?.count, null) : "Saved.",
        bad: status === "failed",
        reauth: answer.reauth,
    };
}

/** Why a name will not do, before anything is sent. */
export function nameProblem(name: string, current: string, taken: readonly string[]): string | null {
    const wanted = name.trim();
    if (!wanted) return "Give it a name you will recognise later.";
    if (wanted !== current && taken.includes(wanted)) return "Another connection here is already called that.";
    return null;
}

/** "One account was connected to the old one, and has to sign in again." */
export function reauthWords(n: number): string {
    return `${n === 1 ? "One account was" : `${n} accounts were`} connected to the old one, and ${n === 1 ? "has" : "have"} to sign in again. ${n === 1 ? "It is" : "They are"} kept, and marked in the list.`;
}

/** What the edit screen warns before its form. */
export function editLead(kind: ConnectorKind | null): string {
    return kind?.auth_scheme === "OAUTH2"
        ? "Changing the app’s client asks everyone signed in through it to sign in again."
        : "Accounts stay connected. Pointing it somewhere else asks the people using it to sign in again.";
}

/** An account's trouble, re-exported for the screens that say it. */
export { accountTrouble };
