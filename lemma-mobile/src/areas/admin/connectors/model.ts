import type { AboutLine } from "../types";
import { accountName, accountTrouble, type Connector, type ConnectorAccount } from "@web/data/accounts";
import { installTarget, isBringYourOwn, ownAppKinds, ownKinds, tenantKind, type CatalogEntry, type Install, type OwnKind } from "@web/connect/install";
import { count, firstFew } from "../words";
import { lookOf } from "./looks";

/** The catalog, as the connectors screen reads it (the web's
 *  `ConnectorsSection` in `org/connectors.tsx`): which connectors are
 *  connected, which need attention, which are only available, and what each
 *  row says. Pure, so `tests/` can hold every rule. */

/** Which slice of the catalog is shown. One list with a stated filter, as on
 *  the web: two unlabelled lists read as one list behaving at random. */
export type Slice = "connected" | "available" | "trouble";

/** Who may make, change or remove installs: owners and editors. `known` is
 *  whether that has been read; nothing is offered or refused on a guess. */
export interface MayManage {
    may: boolean;
    known: boolean;
}

/** A connector's accounts and installs, found once for the whole list. */
export interface Holdings {
    accounts: ReadonlyMap<string, ConnectorAccount[]>;
    installs: ReadonlyMap<string, Install[]>;
}

export function holdings(accounts: readonly ConnectorAccount[], installs: readonly Install[]): Holdings {
    const byAccount = new Map<string, ConnectorAccount[]>();
    for (const account of accounts) byAccount.set(account.connectorId, [...(byAccount.get(account.connectorId) ?? []), account]);
    const byInstall = new Map<string, Install[]>();
    for (const install of installs) byInstall.set(install.connector_id, [...(byInstall.get(install.connector_id) ?? []), install]);
    return { accounts: byAccount, installs: byInstall };
}

export function activeInstalls(installs: readonly Install[]): Install[] {
    return installs.filter((one) => one.status !== "DISABLED");
}

/** Connected means an account, with one exception: a server this
 *  organization added *is* the connection, so for the entries pointed
 *  somewhere an install counts on its own. Only for those: an install of
 *  Gmail with nobody on it is an app that was enabled (a sign-in abandoned
 *  half-way leaves exactly that), not one anybody connected. */
export function isConnected(connector: CatalogEntry, held: Holdings): boolean {
    return (held.accounts.get(connector.id)?.length ?? 0) > 0
        || (isBringYourOwn(connector) && activeInstalls(held.installs.get(connector.id) ?? []).length > 0);
}

export interface Sliced {
    connected: Connector[];
    available: Connector[];
    /** Connectors with an account that cannot be used. */
    ailing: Connector[];
    /** How many accounts cannot be used, across every connector. */
    trouble: number;
}

export function sliceCatalog(connectors: readonly Connector[], accounts: readonly ConnectorAccount[], held: Holdings): Sliced {
    return {
        connected: connectors.filter((one) => isConnected(one, held)),
        available: connectors.filter((one) => !isConnected(one, held)),
        ailing: connectors.filter((one) => (held.accounts.get(one.id) ?? []).some((account) => !account.usable)),
        trouble: accounts.filter((account) => !account.usable).length,
    };
}

/** What is shown before anybody picks: an organization with nothing
 *  connected opens on what it could connect. A chosen "needs attention"
 *  that has emptied falls back to what is connected. */
export function shownSlice(chosen: Slice | null, sliced: Sliced): Slice {
    if (chosen === "trouble" && sliced.trouble === 0) return "connected";
    return chosen ?? (sliced.connected.length ? "connected" : "available");
}

export function poolOf(slice: Slice, sliced: Sliced): Connector[] {
    return slice === "connected" ? sliced.connected : slice === "trouble" ? sliced.ailing : sliced.available;
}

/** By title or id, as the web searches. */
export function searchConnectors<T extends { id: string; title: string }>(pool: readonly T[], query: string): T[] {
    const term = query.trim().toLowerCase();
    if (!term) return [...pool];
    return pool.filter((one) => one.title.toLowerCase().includes(term) || one.id.toLowerCase().includes(term));
}

/** What an empty list says, by why it is empty. Zero of zero is not "all of
 *  them": an empty catalog means its import has not run, or failed. */
export function emptyWords(query: string, slice: Slice, total: number): string {
    if (query.trim()) return `Nothing matches “${query.trim()}”.`;
    if (slice === "connected") return "Nothing connected yet.";
    if (slice === "trouble") return "Everything connected is working.";
    return total > 0 ? "Every connector is already connected." : "No connectors are in the catalog yet. It may still be loading.";
}

/** The line under the heading: "4 accounts · 2 need attention". */
export function metaWords(accounts: readonly ConnectorAccount[]): string {
    const trouble = accounts.filter((account) => !account.usable).length;
    const head = accounts.length ? count(accounts.length, "account", "accounts") : "No accounts yet";
    return trouble ? `${head} · ${count(trouble, "needs", "need")} attention` : head;
}

/** What a connector's row says under its name, and whether somebody has to
 *  act (the violet dot). Four answers, as the web's card has them: what is
 *  wrong first, then what is connected, then what is set up with nobody on
 *  it, then what it is for. */
export function rowHint(connector: Connector, held: Holdings, manage: MayManage): { hint: string; needs: boolean } {
    const accounts = held.accounts.get(connector.id) ?? [];
    const installs = activeInstalls(held.installs.get(connector.id) ?? []);
    const ailing = accounts.filter((account) => !account.usable);
    if (ailing.length === 1 && accounts.length === 1) return { hint: accountTrouble(ailing[0]), needs: true };
    if (ailing.length) return { hint: `${count(ailing.length, "account needs", "accounts need")} attention`, needs: true };

    const addressed = isBringYourOwn(connector);
    if (addressed && installs.length) {
        const waiting = installs.filter((install) => install.auth_scheme === "OAUTH2"
            && !accounts.some((account) => account.authConfigId === install.id && account.usable));
        if (waiting.length) return { hint: `Nobody has signed in to ${firstFew(waiting.map((one) => one.name || one.id), 1)} yet`, needs: true };
        return { hint: firstFew(installs.map((one) => one.name || one.id), 2), needs: false };
    }
    if (accounts.length === 1) {
        const said = accounts[0].label;
        return { hint: said ? `Connected · ${said}` : "Connected", needs: false };
    }
    if (accounts.length > 1) return { hint: `${count(accounts.length, "account", "accounts")} connected`, needs: false };
    if (installs.length) return { hint: "Set up, with no account yet", needs: false };
    if (manage.known && !manage.may) return { hint: "An owner or editor has to set this up", needs: false };
    return { hint: connector.description || "Not set up", needs: false };
}

/** The one-line state a connector's own screen opens with. */
export function stateWords(connector: Connector, held: Holdings): string {
    const accounts = held.accounts.get(connector.id) ?? [];
    const ailing = accounts.filter((account) => !account.usable).length;
    if (ailing) return `${count(ailing, "account needs", "accounts need")} attention`;
    if (isConnected(connector, held)) return "Connected";
    if (activeInstalls(held.installs.get(connector.id) ?? []).length) return "Set up, with no account yet";
    return "Not set up";
}

/** The row a Lemma's Settings tab shows for connectors (the web's
 *  `space/about-lines.ts`): who it reaches, or how many accounts need
 *  attention, or nothing connected yet. */
export function connectorsLine(connectors: readonly Connector[], accounts: readonly ConnectorAccount[]): AboutLine {
    const titles = new Map(connectors.map((one) => [one.id, one.title]));
    const reaches = [...new Set(accounts.map((one) => titles.get(one.connectorId) ?? one.connectorId))];
    const ailing = accounts.filter((one) => !one.usable).length;
    return {
        text: reaches.length
            ? ailing ? `${count(ailing, "account needs", "accounts need")} attention` : firstFew(reaches, 3)
            : "Nothing connected yet",
        needs: ailing > 0,
    };
}

/** What may be added from scratch here: each kind of thing an organization
 *  points somewhere itself, and whether any connector takes an OAuth app of
 *  its own. Both are catalog data: a deployment without them offers neither. */
export function bringYourOwn(connectors: readonly CatalogEntry[]): { kinds: OwnKind[]; ownApp: boolean } {
    return { kinds: ownKinds([...connectors]), ownApp: connectors.some((one) => ownAppKinds(one).length > 0) };
}

/** "An MCP server, a REST API, a database or your own OAuth app". */
export function bringYourOwnWords(own: { kinds: OwnKind[]; ownApp: boolean }): string {
    const names = own.kinds.map((one) => (lookOf(one.kind)?.title ?? one.entry.title).replace(/^A /, "a ").replace(/^An /, "an "));
    if (own.ownApp) names.push("your own OAuth app");
    if (names.length === 0) return "";
    const said = names.length === 1 ? names[0] : `${names.slice(0, -1).join(", ")} or ${names[names.length - 1]}`;
    return said.charAt(0).toUpperCase() + said.slice(1);
}

/** The connectors an organization's own OAuth app can stand behind. */
export function ownAppConnectors<T extends CatalogEntry>(connectors: readonly T[]): T[] {
    return connectors.filter((one) => ownAppKinds(one).length > 0);
}

/** Where a connector's logo is: an address as given, a rooted path on the
 *  web app's origin, or null for anything else (a bare file name means
 *  nothing on a phone, and a letter is drawn instead). */
export function iconAddress(icon: string, web: string): string | null {
    const said = icon.trim();
    if (/^https?:\/\//i.test(said)) return said;
    if (said.startsWith("/") && !said.startsWith("//")) return web.replace(/\/+$/, "") + said;
    return null;
}

/** Whether a connector's installs are worth naming on its screen: several
 *  of them, or ones the organization set up itself (a server it pointed at,
 *  or an app of its own, whose row is where Edit lives). One Gmail install
 *  behind one Gmail account is noise. */
export function installsShown(connector: CatalogEntry, installs: readonly Install[]): boolean {
    return isBringYourOwn(connector) || installs.length > 1 || installs.some((one) => one.config_source === "ORG_CUSTOM");
}

/** What a connector screen calls its installs: "Servers" for MCP, "APIs",
 *  "Databases", else "Installs". */
export function installsLabel(connector: CatalogEntry): string {
    return lookOf(tenantKind(connector)?.kind)?.group ?? "Installs";
}

/** What an install is, beside its name: where it points, for the ones the
 *  organization pointed (two MCP servers named in a hurry are told apart by
 *  their address), "your own app", or Lemma's. */
export function installWhere(install: Install, connector: CatalogEntry): string {
    const addressed = isBringYourOwn(connector);
    if (install.config_source === "ORG_CUSTOM" && !addressed) return "Your own app";
    if (addressed) return installTarget(install) ?? lookOf(install.kind)?.short ?? install.kind;
    return "Lemma’s app";
}

/** An account's row on a connector's screen: what is wrong, else which
 *  install it is on when the installs are named, else its provider id. */
export function accountHint(account: ConnectorAccount, install: Install | null, named: boolean): string {
    const parts = [
        account.usable ? null : accountTrouble(account),
        named && install ? `On ${install.name || install.id}` : null,
        account.isDefault ? "Default" : null,
        /* The provider's id tells two accounts apart; it is never a name. */
        account.ref || null,
    ].filter((one): one is string => Boolean(one));
    return parts.join(" · ");
}

/** What to call an account: its name as the provider gave it (an address
 *  or a handle keeps its own case), else "Your Slack account". */
export function accountTitle(account: ConnectorAccount, connector: { title: string } | null): string {
    if (account.label) return account.label;
    const said = accountName(account, connector?.title ?? "");
    return said.charAt(0).toUpperCase() + said.slice(1);
}

/** An account's status, in a word or two. */
export function statusWords(account: ConnectorAccount): string {
    if (account.usable) return "Connected";
    const status = account.status.toUpperCase();
    if (status === "REAUTH_REQUIRED") return "Signed out";
    if (status === "DISCONNECTED") return "Disconnected";
    return "Not ready";
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "14 Aug 2026", or null for a date that will not read. */
export function dayWords(iso: string): string | null {
    const at = new Date(iso);
    if (!iso || Number.isNaN(at.getTime())) return null;
    return `${at.getDate()} ${MONTHS[at.getMonth()]} ${at.getFullYear()}`;
}
