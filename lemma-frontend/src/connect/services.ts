/** The organization's connectors, as the Connectors page lists them: one row
 *  per thing somebody connected, and what each one needs from a person.
 *
 *  A row is not a catalogue entry and not an install. Gmail is one row however
 *  many accounts and apps it signs in through, because a person thinks "our
 *  Gmail", not "auth config 3". A server, a database or an API the
 *  organization added is the other way round: the catalogue has one entry
 *  standing for every MCP server, so each one added is a row of its own,
 *  named as it was named, marked with its kind.
 *
 *  Pure, so the rules can be checked without a browser.
 */

import { accountTrouble, type Connector, type ConnectorAccount } from "@/data/accounts";
import type { PodConnector } from "@/data/pod-connectors";
import { connectRoute, installTarget, isBringYourOwn, kindFor, type Install } from "./install";

/** What a row can need from somebody, each with its own fix. */
export type NeedKind =
    /** Signed out at the provider: sign in again on the same install. */
    | "reconnect"
    /** A pasted credential that stopped working: paste a new one. */
    | "replace"
    /** Authorised and not installed — a GitHub App. */
    | "install"
    /** Installed in more than one place: pick which. */
    | "choose"
    /** Waiting on somebody else, an owner most often. Nothing to press here. */
    | "waiting"
    /** A server of the organization's own that signs in with OAuth, and
     *  nobody has yet. */
    | "sign-in";

export interface Need {
    kind: NeedKind;
    /** The account it is about. Null for an install nobody signed into. */
    account: ConnectorAccount | null;
    /** The install the fix runs against, when it is known. */
    install: Install | null;
    /** What is wrong, in a few words. */
    said: string;
}

export interface Service {
    /** Stable across reads: the catalogue id, or the install for one of the
     *  organization's own. */
    key: string;
    entry: Connector;
    /** One of the organization's own servers, databases or APIs: the install
     *  that is it. Null for everything else. */
    own: Install | null;
    /** Its kind — `mcp`, `http`, `sql` — for one of the organization's own. */
    kind: string | null;
    title: string;
    accounts: ConnectorAccount[];
    /** Every install of it this organization has, disabled ones included. */
    installs: Install[];
    needs: Need[];
}

const SIGNED_OUT: ReadonlySet<string> = new Set(["REAUTH_REQUIRED", "DISCONNECTED"]);

/** What one row needs, account by account. */
export function needsOf(entry: Connector, own: Install | null, accounts: ConnectorAccount[], installs: Install[]): Need[] {
    const needs: Need[] = [];
    for (const account of accounts) {
        if (account.usable) continue;
        const install = installs.find((one) => one.id === account.authConfigId) ?? null;
        const status = account.status.toUpperCase();
        const state = account.installState.toUpperCase();
        const said = accountTrouble(account);
        if (SIGNED_OUT.has(status)) {
            const credentialed = connectRoute(install, kindFor(entry, install)) === "credentials";
            needs.push({ kind: credentialed ? "replace" : "reconnect", account, install, said });
        } else if (status === "CONNECTED" && state === "INSTALL_REQUIRED") {
            needs.push({ kind: "install", account, install, said });
        } else if (status === "CONNECTED" && state === "CHOOSE_INSTALL") {
            needs.push({ kind: "choose", account, install, said });
        } else {
            needs.push({ kind: "waiting", account, install, said });
        }
    }
    /* A server that signs in with OAuth is added before anybody has signed
       in to it, and until somebody does it can reach nothing. */
    if (own && accounts.length === 0 && connectRoute(own, kindFor(entry, own)) === "redirect") {
        needs.push({ kind: "sign-in", account: null, install: own, said: "Nobody has signed in yet" });
    }
    return needs;
}

function row(entry: Connector, own: Install | null, accounts: ConnectorAccount[], installs: Install[]): Service {
    return {
        key: own ? "install:" + own.id : entry.id,
        entry,
        own,
        kind: own ? own.kind : null,
        title: own ? own.name || entry.title : entry.title,
        accounts,
        installs,
        needs: needsOf(entry, own, accounts, installs),
    };
}

/** Everything this organization has connected, by name.
 *
 *  Connected means an account, with one exception: a server this
 *  organization added *is* the connection, so an install of one counts on its
 *  own. Only for those — an install of Canva with nobody on it is a sign-in
 *  abandoned half-way, and counting it would fill the list with rows whose
 *  only button is Connect. */
export function servicesOf(catalog: Connector[], accounts: ConnectorAccount[], installs: Install[]): Service[] {
    const found: Service[] = [];
    for (const entry of catalog) {
        const its = installs.filter((one) => one.connector_id === entry.id);
        const on = accounts.filter((one) => one.connectorId === entry.id);
        if (isBringYourOwn(entry)) {
            for (const install of its) {
                if (install.status === "DISABLED") continue;
                found.push(row(entry, install, on.filter((one) => one.authConfigId === install.id), [install]));
            }
            /* Accounts whose install is not in the list — one read before it
               was made, most likely. Shown, not dropped: they are real and
               can be removed. */
            const loose = on.filter((one) => !its.some((install) => install.id === one.authConfigId));
            if (loose.length) found.push(row(entry, null, loose, []));
        } else if (on.length) {
            found.push(row(entry, null, on, its));
        }
    }
    return found.sort((a, b) => a.title.localeCompare(b.title));
}

/** Catalogue entries nobody here has connected yet, and that are picked
 *  rather than pointed somewhere — the servers, databases and APIs are added
 *  under "Your own". */
export function notYetConnected(catalog: Connector[], services: Service[]): Connector[] {
    const taken = new Set(services.filter((one) => !one.own).map((one) => one.entry.id));
    return catalog.filter((entry) => !isBringYourOwn(entry) && !taken.has(entry.id));
}

/** The install a new account goes on: the organization's default, else the
 *  first that is on. Null when there is none, and one is made on the way. */
export function connectAgainst(installs: readonly Install[]): Install | null {
    const on = installs.filter((one) => one.status !== "DISABLED");
    return on.find((one) => one.is_default) ?? on[0] ?? null;
}

/** Whether this person can connect an account at all: a member can, on an
 *  install that exists; making one is an owner's or an editor's. `null`
 *  leaves it to the server. */
export function mayConnect(installs: readonly Install[], mayInstall: boolean | null): boolean {
    return mayInstall !== false || installs.some((one) => one.status !== "DISABLED");
}

/** Whether a Lemma's connector runs through this row. */
export function servesThrough(service: Service, connector: PodConnector): boolean {
    if (service.own) return connector.authConfigId === service.own.id;
    if (connector.authConfigId && service.installs.some((one) => one.id === connector.authConfigId)) return true;
    return Boolean(connector.connectorId) && connector.connectorId === service.entry.id;
}

/** How a row is doing, as a dot and a word. */
export function statusOf(service: Service): { word: string; tone: "ok" | "warn" } {
    const first = service.needs[0];
    if (!first) return { word: "Working", tone: "ok" };
    return { word: NEED_WORDS[first.kind], tone: "warn" };
}

const NEED_WORDS: Record<NeedKind, string> = {
    reconnect: "Signed out",
    replace: "Needs a new credential",
    install: "Needs installing",
    choose: "Needs installing",
    waiting: "Waiting",
    "sign-in": "Not signed in",
};

/** The button that fixes it, or null where nothing here can. */
export function fixWord(need: Need): string | null {
    switch (need.kind) {
        case "reconnect": return "Reconnect";
        case "replace": return "Replace credential";
        case "install": return "Install";
        case "choose": return "Choose installation";
        case "sign-in": return "Sign in";
        default: return null;
    }
}

/** What goes under a row's name: its accounts, or where one of the
 *  organization's own points. */
export function subtitleOf(service: Service, accountName: (account: ConnectorAccount) => string): string {
    if (service.own) {
        const target = installTarget(service.own);
        if (target) return target;
    }
    const names = service.accounts.map(accountName);
    return names.join(", ");
}

/** Whether a row answers a search: its name, its catalogue id, where it
 *  points, or one of its accounts. */
export function matches(service: Service, term: string): boolean {
    if (!term) return true;
    const said = [
        service.title, service.entry.title, service.entry.id,
        ...(service.own ? [installTarget(service.own) ?? ""] : []),
        ...service.accounts.map((one) => one.label + " " + one.ref),
    ];
    return said.some((one) => one.toLowerCase().includes(term));
}

export function entryMatches(entry: Connector, term: string): boolean {
    if (!term) return true;
    return entry.title.toLowerCase().includes(term) || entry.id.toLowerCase().includes(term);
}
