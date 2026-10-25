/** The accounts in use in a pod, where one of mine is used, and what removing
 *  an installation would stop.
 *
 *  A pod account records that one organization account is in use in one pod.
 *  It belongs to the account's owner, and only they can widen who it serves:
 *  PERSONAL (their own calls), POD (everyone here) or RESTRICTED (some members
 *  or roles). A pod editor can point a SHARED connector at an account its
 *  owner shared, and can take any account out of the pod, but can never share
 *  one. So everything here that shares is offered to the owner alone.
 *
 *  Pure, like `pod-connectors.ts`: every field is a wire field.
 */

import type { PodConnector, PodConnectorMode } from "./pod-connectors";
import { freshSlug } from "./pod-connectors";
import { POD_ROLES } from "./pod-roles";

export type PodAccountVisibility = "PERSONAL" | "POD" | "RESTRICTED";

export interface Grantee {
    type: "ROLE" | "POD_MEMBER";
    id: string;
}

export interface PodAccount {
    id: string;
    accountId: string;
    authConfigId: string;
    connectorId: string;
    ownerId: string;
    visibility: PodAccountVisibility;
    /** The account the owner's own calls use here, among theirs on one
     *  installation. */
    isMemberDefault: boolean;
    /** How it got here: AUTO, MANUAL, IMPORT or MIGRATED. */
    source: string;
    lastUsedAt: string;
    /** The account's name at the provider, or its email. */
    label: string;
    /** Slugs of the SHARED connectors that run as it. */
    usedBy: string[];
    /** The owner allowed it inside sandboxes. Null when this server does not
     *  say, which is a server from before the setting. */
    sandboxes: boolean | null;
    /** Something a change did not do at once, for the owner to read — after
     *  turning sandbox use off, that sandboxes already given the token keep
     *  a copy until it is swept. Only on the answer to a change; empty on a
     *  listing. */
    notice: string;
}

/** The wire name of the sandbox setting. One place, so it follows the
 *  server's spelling. */
export const SANDBOX_FIELD = "allow_sandbox_use";

const VISIBILITIES: PodAccountVisibility[] = ["PERSONAL", "POD", "RESTRICTED"];

function record(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function text(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

function items(raw: unknown): unknown[] {
    const list = Array.isArray(raw) ? raw : record(raw)["items"];
    return Array.isArray(list) ? list : [];
}

function visibilityOf(value: unknown): PodAccountVisibility {
    const said = text(value).toUpperCase() as PodAccountVisibility;
    /* Unknown is the narrowest, never the widest. */
    return VISIBILITIES.includes(said) ? said : "PERSONAL";
}

/** The owner's "allow use inside sandboxes". Absent is a server from before
 *  the setting, which offers no toggle rather than a false one. */
function sandboxesOf(row: Record<string, unknown>): boolean | null {
    const said = row[SANDBOX_FIELD];
    return typeof said === "boolean" ? said : null;
}

export function readPodAccount(raw: unknown): PodAccount | null {
    const row = record(raw);
    const id = text(row["id"]);
    if (!id) return null;
    return {
        id,
        accountId: text(row["account_id"]),
        authConfigId: text(row["auth_config_id"]),
        connectorId: text(row["connector_id"]),
        ownerId: text(row["owner_user_id"]),
        visibility: visibilityOf(row["visibility"]),
        isMemberDefault: row["is_member_default"] === true,
        source: text(row["source"]).toUpperCase(),
        lastUsedAt: text(row["last_used_at"]),
        label: text(row["display_name"]) || text(row["email"]),
        usedBy: (Array.isArray(row["used_by"]) ? row["used_by"] : []).map(text).filter(Boolean),
        sandboxes: sandboxesOf(row),
        notice: text(row["notice"]),
    };
}

export function readPodAccounts(raw: unknown): PodAccount[] {
    return items(raw).map(readPodAccount).filter((one): one is PodAccount => one !== null);
}

/** Who an account serves here, in a person's words. */
export function visibilityWord(visibility: PodAccountVisibility): string {
    if (visibility === "POD") return "Shared with everyone here";
    if (visibility === "RESTRICTED") return "Shared with some people here";
    return "Only your own calls";
}

/** The accounts a SHARED connector on this installation may run as: ones
 *  their owners already shared. An editor picks among these and nothing else
 *  — putting somebody's account in front of the pod is theirs to do. */
export function shareableFor(accounts: PodAccount[], authConfigId: string): PodAccount[] {
    return accounts.filter((one) => one.authConfigId === authConfigId && one.visibility !== "PERSONAL");
}

/** Mine, in this pod. */
export function ownedBy(accounts: PodAccount[], me: string): PodAccount[] {
    return me ? accounts.filter((one) => one.ownerId === me) : [];
}

/** "Use this account for everyone in the pod as <slug>" is one request now,
 *  `POST /pods/{pod}/connector-accounts/{id}/share-as`: the server checks
 *  everything — that I own it and may manage connectors, that `slug` is free
 *  or a SHARED connector on the same installation — before it widens the
 *  account or points or declares the connector, and does both in one
 *  transaction. So a refusal leaves my account as it was, and this client
 *  holds no copy of those rules to drift from the server's. */
export function shareAsBody(slug: string, purpose = ""): { slug: string; purpose?: string } {
    return { slug, ...(purpose.trim() ? { purpose: purpose.trim() } : {}) };
}

/** The slug offered for sharing an account: a SHARED connector on its
 *  installation that has none yet, else a fresh one named for the connector. */
export function suggestedSlug(account: PodAccount, connectors: PodConnector[]): string {
    const waiting = connectors.find((one) => one.mode === "SHARED" && one.authConfigId === account.authConfigId && !one.sharedPodAccountId);
    if (waiting) return waiting.slug;
    return freshSlug((account.connectorId || "account") + "-shared", connectors.map((one) => one.slug));
}

/* ── where one of my accounts is used ──────────────────────────────── */

export interface AccountUsage {
    podId: string;
    podAccountId: string;
    visibility: PodAccountVisibility;
    source: string;
    isMemberDefault: boolean;
    lastUsedAt: string;
    usedBy: string[];
}

export function readAccountUsage(raw: unknown): AccountUsage[] {
    return items(raw).map((entry) => {
        const row = record(entry);
        return {
            podId: text(row["pod_id"]),
            podAccountId: text(row["pod_account_id"]),
            visibility: visibilityOf(row["visibility"]),
            source: text(row["source"]).toUpperCase(),
            isMemberDefault: row["is_member_default"] === true,
            lastUsedAt: text(row["last_used_at"]),
            usedBy: (Array.isArray(row["used_by"]) ? row["used_by"] : []).map(text).filter(Boolean),
        };
    }).filter((one) => one.podId);
}

/** One line per pod an account is in: "Kit · shared with everyone · runs
 *  support-inbox". `podName` names a pod id; an unknown one is said as such. */
export function usageLine(usage: AccountUsage, podName: (podId: string) => string): string {
    const name = podName(usage.podId) || "A teammate you cannot open";
    const who = usage.visibility === "PERSONAL" ? "your own calls" : usage.visibility === "POD" ? "shared with everyone" : "shared with some people";
    const runs = usage.usedBy.length ? " · runs " + usage.usedBy.join(", ") : "";
    return name + " · " + who + runs;
}

/* ── what removing an installation stops ───────────────────────────── */

/** One pod I can open, and what in it stops. */
export interface ImpactedPod {
    podId: string;
    name: string;
    podConnectors: string[];
    surfaceCount: number;
    scheduleCount: number;
    eventSubscriptionCount: number;
}

export interface InstallationImpact {
    authConfigId: string;
    accountCount: number;
    podAccountCount: number;
    podConnectors: { podId: string; slug: string; mode: PodConnectorMode }[];
    /** The organization's, across every pod: whoever removes it removes it
     *  for everyone. */
    surfaceCount: number;
    scheduleCount: number;
    eventSubscriptionCount: number;
    /** Only the pods I can open, named by the server. */
    pods: ImpactedPod[];
    /** Pods that lose a connector and that the server did not name — ones I
     *  cannot open, or every one whose check did not finish — and how many
     *  connectors they lose. Counted, never listed: a slug says what a pod is
     *  for as plainly as its name. */
    unnamedPodCount: number;
    unnamedPodConnectorCount: number;
    /** False when the server could not read everything it stops: the
     *  counts are then a floor, not the whole. */
    complete: boolean;
}

export function readImpact(raw: unknown): InstallationImpact {
    const row = record(raw);
    const count = (value: unknown) => (typeof value === "number" && Number.isFinite(value) ? value : 0);
    return {
        authConfigId: text(row["auth_config_id"]),
        accountCount: count(row["account_count"]),
        podAccountCount: count(row["pod_account_count"]),
        surfaceCount: count(row["surface_count"]),
        scheduleCount: count(row["schedule_count"]),
        eventSubscriptionCount: count(row["event_subscription_count"]),
        unnamedPodCount: count(row["unnamed_pod_count"]),
        unnamedPodConnectorCount: count(row["unnamed_pod_connector_count"]),
        complete: row["complete"] !== false,
        pods: (Array.isArray(row["pods"]) ? row["pods"] : []).map((entry) => {
            const one = record(entry);
            return {
                podId: text(one["pod_id"]),
                name: text(one["name"]),
                podConnectors: (Array.isArray(one["pod_connectors"]) ? one["pod_connectors"] : []).map(text).filter(Boolean),
                surfaceCount: count(one["surface_count"]),
                scheduleCount: count(one["schedule_count"]),
                eventSubscriptionCount: count(one["event_subscription_count"]),
            };
        }).filter((one) => one.podId),
        podConnectors: (Array.isArray(row["pod_connectors"]) ? row["pod_connectors"] : []).map((entry) => {
            const one = record(entry);
            return {
                podId: text(one["pod_id"]),
                slug: text(one["slug"]),
                mode: (text(one["mode"]).toUpperCase() === "SHARED" ? "SHARED" : "PER_MEMBER") as PodConnectorMode,
            };
        }).filter((one) => one.slug),
    };
}

function plural(n: number, one: string, many: string): string {
    return n + " " + (n === 1 ? one : many);
}

function andList(parts: string[]): string {
    return parts.length < 2 ? parts.join("") : parts.slice(0, -1).join(", ") + " and " + parts[parts.length - 1];
}

/** What the confirmation says before an installation goes: the accounts that
 *  go with it, and each teammate's connectors that stop. Empty lines left out;
 *  nothing at all is said as such, so "Remove" is never a leap in the dark --
 *  but only when the server read everything: an unfinished read says so
 *  rather than "nobody loses anything". */
export function impactLines(impact: InstallationImpact, podName: (podId: string) => string): string[] {
    const lines: string[] = [];
    if (impact.accountCount) lines.push(plural(impact.accountCount, "connected account is", "connected accounts are") + " removed with it.");
    const stops = [
        ...(impact.surfaceCount ? [plural(impact.surfaceCount, "surface stops", "surfaces stop") + " answering"] : []),
        ...(impact.scheduleCount ? [plural(impact.scheduleCount, "schedule stops", "schedules stop") + " firing"] : []),
        ...(impact.eventSubscriptionCount ? [plural(impact.eventSubscriptionCount, "MCP event subscription ends", "MCP event subscriptions end")] : []),
    ];
    if (stops.length) lines.push(andList(stops) + ".");
    const seen = new Map(impact.pods.map((one) => [one.podId, one]));
    const byPod = new Map<string, string[]>();
    for (const one of impact.podConnectors) byPod.set(one.podId, [...(byPod.get(one.podId) ?? []), one.slug]);
    for (const [podId, slugs] of byPod) {
        const pod = seen.get(podId);
        const what = pod ? [
            ...(pod.surfaceCount ? [plural(pod.surfaceCount, "surface", "surfaces")] : []),
            ...(pod.scheduleCount ? [plural(pod.scheduleCount, "schedule", "schedules")] : []),
            ...(pod.eventSubscriptionCount ? [plural(pod.eventSubscriptionCount, "subscription", "subscriptions")] : []),
        ] : [];
        lines.push((pod?.name || podName(podId) || "A teammate you cannot open") + " loses " + slugs.join(", ") + (what.length ? ": " + what.join(", ") : "") + ".");
    }
    if (impact.unnamedPodCount) {
        const connectors = impact.unnamedPodConnectorCount === 1 ? "a connector" : plural(impact.unnamedPodConnectorCount, "connector", "connectors");
        /* Unfinished, the server never learned which of them I may open. */
        const who = impact.complete
            ? plural(impact.unnamedPodCount, "teammate you cannot open loses", "teammates you cannot open lose")
            : plural(impact.unnamedPodCount, "more teammate loses", "more teammates lose");
        lines.push(who + " " + connectors + ".");
    }
    if (impact.podAccountCount && !impact.podConnectors.length && !impact.unnamedPodCount) {
        lines.push(plural(impact.podAccountCount, "teammate's use", "teammates' uses") + " of these accounts stop.");
    }
    if (!impact.complete) lines.push("Couldn’t read everything this affects.");
    else if (lines.length === 0) lines.push("Nothing uses it yet. Nobody loses anything.");
    return lines;
}

/** A change the owner (or, removing it, an editor) makes to a pod account. */
export interface PodAccountPatch {
    visibility?: PodAccountVisibility;
    /** RESTRICTED: replaces who may use it. */
    shareWith?: Grantee[];
    isMemberDefault?: boolean;
    /** Allow it inside sandboxes. Sent only to a server that says it has the
     *  setting. */
    sandboxes?: boolean;
}

export function accountPatchBody(patch: PodAccountPatch): Record<string, unknown> {
    const body: Record<string, unknown> = {};
    if (patch.visibility) body.visibility = patch.visibility;
    if (patch.shareWith) body.share_with = patch.shareWith.map((one) => ({ grantee_type: one.type, grantee_id: one.id }));
    if (patch.isMemberDefault !== undefined) body.is_member_default = patch.isMemberDefault;
    if (patch.sandboxes !== undefined) body[SANDBOX_FIELD] = patch.sandboxes;
    return body;
}

/* ── sharing with some of the pod's roles ──────────────────────────── */

/** A pod role an owner can share a RESTRICTED account with: everyone who
 *  holds it may use the account. */
export interface ShareRole {
    id: string;
    /** The role's wire name: `POD_EDITOR`, or a custom one. */
    name: string;
    /** What the picker calls everyone holding it: "Editors". */
    label: string;
}

function roleLabel(name: string): string {
    const known = POD_ROLES.find((one) => one.value === name);
    if (known) return known.role + "s";
    const tidy = name.replace(/^POD_/, "").replaceAll("_", " ").toLowerCase();
    return tidy ? tidy.charAt(0).toUpperCase() + tidy.slice(1) : name;
}

/** The pod's roles, off `GET /pods/{pod}/roles`. A grant names a role by its
 *  id, so a row without one is not offered. */
export function readShareRoles(raw: unknown): ShareRole[] {
    return items(raw).map((entry) => {
        const row = record(entry);
        const name = text(row["name"]);
        return { id: text(row["id"]), name, label: roleLabel(name) };
    }).filter((one) => one.id && one.name);
}
