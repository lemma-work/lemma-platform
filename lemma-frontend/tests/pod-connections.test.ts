import test, { after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import ts from "typescript";
import { JSDOM } from "jsdom";
import { act, createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
    askedLine, bannerLine, connectorCalled, connectorCreateBody, connectorName, connectorPatchBody, failureOf, mayUseOnceConnected,
    missingForMe, modeBadge, myStatus, readAskOutcome, readCoverage, readPodConnector, readPodConnectors, type MyAccount,
} from "../src/data/pod-connectors.ts";
import {
    accountPatchBody, impactLines, readAccountUsage, readImpact, readPodAccounts, shareAsBody, usageLine,
} from "../src/data/pod-accounts.ts";
import {
    botLogins, botReach, botSlug, connectedCount, connectionSaid, missingLine, readPodConnections, someoneMissing, twiceOver,
} from "../src/data/pod-connections.ts";
import { INSTALLATION_MANAGE, readOrgActions } from "../src/data/org-actions.ts";
import { CONNECTOR_USE, connectorGrantSlugs, grantsBody, withConnectorGrants } from "../src/data/agents.ts";
import { connectionLine, connectionTrouble, needsRebind, readConnection, rebindChoices, surfaceApp } from "../src/data/surface-settings.ts";
import { connectionsPlace, readAddress, writeAddress } from "../src/shell/address.ts";
import { legacyAddress } from "../src/site/legacy-address.ts";
import { liveSource } from "../src/data/live.ts";
import { disconnect } from "../src/session/client.ts";

/** A teammate's Connections: the pod's own connectors and whose account each
 *  runs as, my standing on each, my accounts in it, and every place that
 *  used to pick an account by id now picking a connector by slug. */

registerHooks({ load(url, context, nextLoad) {
    /* The page reads who is signed in from the session; here it is "me". */
    if (url.endsWith("/src/session/use-me.ts")) {
        return { format: "module", shortCircuit: true, source: 'export const useMe = () => "me";' };
    }
    if (url.endsWith(".css")) return { format: "module", shortCircuit: true, source: "" };
    if (!url.endsWith(".tsx")) return nextLoad(url, context);
    return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), "utf8"), {
        compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
    }).outputText };
} });

const CONNECTORS = readPodConnectors({ items: [
    { id: "c1", slug: "support-inbox", connector_id: "gmail", auth_config_id: "ac-gmail", mode: "SHARED", status: "READY", purpose: "The support mailbox",
        shared_pod_account_id: "pa-alice", shared_account: { display_name: "support@acme.com", owner_user_id: "alice", pod_account_id: "pa-alice" } },
    /* A listing never carries coverage now: an editor asks for it per connector. */
    { id: "c2", slug: "gmail", connector_id: "gmail", auth_config_id: "ac-gmail", mode: "PER_MEMBER", status: "READY", required: true, purpose: "Each person's own inbox", member_coverage: null },
    { id: "c3", slug: "notion", connector_id: "notion", auth_config_id: "ac-notion", mode: "PER_MEMBER", status: "READY", required: true },
    { id: "c4", slug: "teams-ops", connector_id: "teams", auth_config_id: "ac-teams", mode: "SHARED", status: "NEEDS_ACCOUNT" },
    { id: "c5", slug: "github", connector_id: "github", auth_config_id: "ac-github", mode: "PER_MEMBER", status: "UNAVAILABLE", required: true },
] });

/** What `GET /pods/p1/connectors/gmail/coverage` answers an editor. */
const GMAIL_COVERAGE = { eligible: 7, connected: 5, truncated: false, waiting_on: [
    { user_id: "bob", pod_member_id: "m-bob", name: "Bob", email: "bob@acme.com", standing: "MISSING" },
    { user_id: "carol", pod_member_id: "m-carol", name: null, email: "carol@acme.com", standing: "REAUTH_REQUIRED" },
] };

const MINE: MyAccount[] = [
    { id: "a-notion", authConfigId: "ac-notion", connectorId: "notion", label: "me@notion", status: "REAUTH_REQUIRED", usable: false, isDefault: true },
    { id: "a-slack", authConfigId: "ac-slack", connectorId: "slack", label: "", status: "CONNECTED", usable: true, isDefault: true },
];

const ACCOUNTS_RAW_MINE = { id: "pa-mine", account_id: "acc-mine", auth_config_id: "ac-gmail", connector_id: "gmail", owner_user_id: "me", visibility: "PERSONAL", is_member_default: true, source: "AUTO", email: "me@acme.com", allow_sandbox_use: false };

const ACCOUNTS = readPodAccounts({ items: [
    { id: "pa-alice", account_id: "acc-alice", auth_config_id: "ac-gmail", connector_id: "gmail", owner_user_id: "alice", visibility: "POD", is_member_default: true, source: "MANUAL", display_name: "support@acme.com", used_by: ["support-inbox"] },
    ACCOUNTS_RAW_MINE,
    { id: "pa-bob", account_id: "acc-bob", auth_config_id: "ac-gmail", connector_id: "gmail", owner_user_id: "bob", visibility: "RESTRICTED", is_member_default: false, source: "MANUAL" },
    { id: "", account_id: "x" },
] });

test("a pod connector reads its installation, its account and whether everyone is asked", () => {
    assert.equal(CONNECTORS.length, 5);
    assert.equal(CONNECTORS[0].authConfigId, "ac-gmail");
    assert.equal(CONNECTORS[0].sharedPodAccountId, "pa-alice");
    assert.equal(CONNECTORS[0].sharedOwnerId, "alice");
    assert.equal(CONNECTORS[1].required, true);
    assert.equal(CONNECTORS[0].visibility, "POD");
});

test("an editor reads who a per-member connector is waiting on, by name and never by account", () => {
    const coverage = readCoverage(GMAIL_COVERAGE);
    assert.equal(coverage.connected, 5);
    assert.deepEqual(coverage.waitingOn.map((one) => [one.userId, one.name, one.standing]),
        [["bob", "Bob", "MISSING"], ["carol", "carol@acme.com", "REAUTH_REQUIRED"]], "a member with no name is said by their email");
    assert.equal(readCoverage({ ...GMAIL_COVERAGE, truncated: true }).truncated, true);
    assert.deepEqual(readCoverage(null), { eligible: 0, connected: 0, waitingOn: [], truncated: false });
});

test("asking members to connect says who was asked and who was asked already today", () => {
    const said = readAskOutcome({ connect_url: "https://app/pods/p1/connections?connector=gmail",
        asked: [{ user_id: "bob", pod_member_id: "m-bob", name: "Bob", standing: "MISSING" }],
        asked_recently: [{ user_id: "carol", pod_member_id: "m-carol", email: "carol@acme.com", standing: "REAUTH_REQUIRED" }] });
    assert.deepEqual(said.asked.map((one) => one.name), ["Bob"]);
    assert.equal(askedLine(said), "Asked Bob. carol@acme.com was asked in the last day.");
    assert.equal(askedLine(readAskOutcome({ asked: [], asked_recently: [] })), "Nobody to ask.");
    // The ledger could not be read: nobody was asked, and the line says so
    // rather than claiming they were asked already.
    const unreached = readAskOutcome({ asked: [], asked_recently: [], not_asked: [{ user_id: "u3", name: "Dan" }] });
    assert.equal(askedLine(unreached), "Couldn’t ask Dan just now — try again shortly.");
});

test("the badge says whose account a call runs as", () => {
    assert.equal(modeBadge(CONNECTORS[1]), "Your account");
    assert.equal(modeBadge(CONNECTORS[0], "Alice"), "Shared · support@acme.com · by Alice");
    assert.equal(modeBadge(CONNECTORS[0]), "Shared · support@acme.com");
    assert.equal(modeBadge(CONNECTORS[3]), "Shared · no account yet");
});

test("my standing on each connector comes from my own accounts on its installation", () => {
    assert.equal(myStatus(CONNECTORS[1], MINE, "me").standing, "connect", "no gmail account of mine");
    assert.equal(myStatus(CONNECTORS[2], MINE, "me").standing, "reconnect", "my notion account needs signing in");
    assert.equal(myStatus(CONNECTORS[0], MINE, "me").standing, "connected", "a shared one runs whoever I am");
    assert.equal(myStatus(CONNECTORS[3], MINE, "me").standing, "waiting");
    assert.equal(myStatus(CONNECTORS[4], MINE, "me").standing, "off");
    const connected = myStatus(CONNECTORS[1], [...MINE, { id: "a-g", authConfigId: "ac-gmail", connectorId: "gmail", label: "me@acme.com", status: "CONNECTED", usable: true, isDefault: false }], "me");
    assert.equal(connected.standing, "connected");
    assert.equal(connected.note, "As me@acme.com");
    const owner = myStatus(CONNECTORS[0], [{ id: "acc-alice", authConfigId: "ac-gmail", connectorId: "gmail", label: "", status: "REAUTH_REQUIRED", usable: false, isDefault: true }], "alice");
    assert.equal(owner.standing, "reconnect", "the shared account's owner is the one asked to reconnect it");
});

test("the banner asks only for required per-member connectors I have no account for", () => {
    const missing = missingForMe(CONNECTORS, MINE, "me", true);
    assert.deepEqual(missing.map((one) => one.connector.slug), ["gmail", "notion"], "not the turned-off github, not a shared one");
    const names = (id: string) => ({ gmail: "Gmail", notion: "Notion" } as Record<string, string>)[id] ?? "";
    assert.equal(bannerLine(missing, names, "Kit"), "Kit uses Gmail and Notion — connect yours");
    assert.equal(bannerLine(missing.slice(1), names, "Kit"), "Kit uses Notion — sign in again");
    assert.equal(bannerLine([], names, "Kit"), "");
    // Three connectors on one REST API are three things; the app's name says
    // none of them, so a connector named apart from its app goes by its name.
    const crm = { ...CONNECTORS[2], slug: "crm", connectorId: "openapi" };
    assert.equal(connectorCalled(crm, "OpenAPI (custom API)"), "crm");
    assert.equal(connectorCalled(CONNECTORS[1], "Gmail"), "Gmail");
    assert.equal(connectorCalled({ ...CONNECTORS[1], slug: "gmail-team" }, "Gmail"), "Gmail");
});

test("the banner asks only people who could use the connector once connected", () => {
    // The server's own can_use wins whatever the pod-wide answer.
    assert.equal(mayUseOnceConnected({ canUse: false, visibility: "POD" }, true), false, "a RESTRICTED connector that leaves me out");
    assert.equal(mayUseOnceConnected({ canUse: true, visibility: "RESTRICTED" }, false), true);
    // A server that does not say is answered from connector.use across the
    // pod, and never for a RESTRICTED connector, which may leave me out.
    assert.equal(mayUseOnceConnected({ canUse: null, visibility: "POD" }, true), true);
    assert.equal(mayUseOnceConnected({ canUse: null, visibility: "POD" }, false), false, "a viewer holds no connector.use");
    assert.equal(mayUseOnceConnected({ canUse: null, visibility: "POD" }, null), false, "not asked yet is not yes");
    assert.equal(mayUseOnceConnected({ canUse: null, visibility: "RESTRICTED" }, true), false);
    assert.deepEqual(missingForMe(CONNECTORS, MINE, "me", false), [], "a viewer is asked for nothing");
    const said = readPodConnectors({ items: [
        { slug: "gmail", connector_id: "gmail", auth_config_id: "ac-gmail", mode: "PER_MEMBER", status: "READY", required: true, can_use: false },
        { slug: "notion", connector_id: "notion", auth_config_id: "ac-notion", mode: "PER_MEMBER", status: "READY", required: true, visibility: "RESTRICTED", can_use: true },
    ] });
    assert.deepEqual(missingForMe(said, MINE, "me", false).map((one) => one.connector.slug), ["notion"], "can_use decides, per connector");
});

test("a connector's name is its slug, with its app beside it when they differ", () => {
    assert.equal(connectorName("support-inbox", "Gmail"), "support-inbox (Gmail)");
    assert.equal(connectorName("gmail", "Gmail"), "gmail");
    assert.equal(connectorName("google-drive", "Google Drive"), "google-drive");
    assert.equal(connectorName("crm", ""), "crm");
});

test("declaring and changing a connector sends only what it means", () => {
    assert.deepEqual(connectorCreateBody({ slug: "gmail", authConfigId: "ac", mode: "PER_MEMBER", sharedPodAccountId: "pa", required: true, purpose: "  inbox " }),
        { slug: "gmail", auth_config_id: "ac", mode: "PER_MEMBER", required: true, purpose: "inbox" }, "no shared account on a per-member one");
    assert.deepEqual(connectorCreateBody({ slug: "s", authConfigId: "ac", mode: "SHARED", sharedPodAccountId: "pa", required: true, purpose: "" }),
        { slug: "s", auth_config_id: "ac", mode: "SHARED", shared_pod_account_id: "pa" }, "nobody is asked to connect a shared one");
    assert.deepEqual(connectorPatchBody({ sharedPodAccountId: "" }), { shared_pod_account_id: null });
    assert.deepEqual(connectorCreateBody({ slug: "github-bot", authConfigId: "", connectorId: "github", mode: "APP", sharedPodAccountId: "", required: false, purpose: "" }),
        { slug: "github-bot", connector_id: "github", mode: "APP" }, "the bot: no installation named, no account");
    assert.deepEqual(connectorPatchBody({ mode: "APP", sharedPodAccountId: "" }), { mode: "APP", shared_pod_account_id: null });
    const bot = readPodConnector({ slug: "github-bot", connector_id: "github", mode: "APP", status: "NEEDS_INSTALLATION" });
    assert.equal(bot?.mode, "APP");
    assert.equal(bot?.status, "NEEDS_INSTALLATION");
    assert.equal(myStatus(bot!, MINE, "me").standing, "waiting", "a bot nobody installed runs for nobody");
});

test("pod accounts read their owner, visibility and sandbox setting", () => {
    assert.equal(ACCOUNTS.length, 3, "a row with no id is not one");
    assert.equal(ACCOUNTS[1].label, "me@acme.com");
    assert.equal(ACCOUNTS[1].sandboxes, false);
    assert.equal(ACCOUNTS[0].sandboxes, null, "a server without the setting says nothing about it");
    assert.equal(ACCOUNTS[1].ownerId, "me");
    assert.deepEqual(accountPatchBody({ visibility: "RESTRICTED", shareWith: [{ type: "POD_MEMBER", id: "m1" }], sandboxes: true }),
        { visibility: "RESTRICTED", share_with: [{ grantee_type: "POD_MEMBER", grantee_id: "m1" }], allow_sandbox_use: true });
});

test("using an account for everyone sends one slug and purpose, and leaves the checks to the server", () => {
    assert.deepEqual(shareAsBody("team-gmail", "  The team inbox "), { slug: "team-gmail", purpose: "The team inbox" });
    assert.deepEqual(shareAsBody("team-gmail"), { slug: "team-gmail" }, "no purpose, none sent");
});

test("a sandbox change reads the server's notice off its answer, and a listing carries none", () => {
    const [saved] = readPodAccounts({ items: [{ ...ACCOUNTS_RAW_MINE, visibility: "POD", allow_sandbox_use: false,
        notice: "A sandbox that already has a copy loses it on its next git command, or when it is swept as stale." }] });
    assert.equal(saved.sandboxes, false);
    assert.match(saved.notice, /swept as stale/);
    assert.equal(ACCOUNTS[1].notice, "");
});

test("where an account is used, and what removing an installation stops, are said by pod name", () => {
    const usage = readAccountUsage({ account_id: "a", items: [
        { pod_id: "p1", pod_account_id: "pa1", visibility: "POD", source: "MANUAL", is_member_default: true, used_by: ["support-inbox"] },
        { pod_id: "p2", pod_account_id: "pa2", visibility: "PERSONAL", source: "AUTO", is_member_default: true },
    ] });
    const names = (id: string) => ({ p1: "Kit" } as Record<string, string>)[id] ?? "";
    assert.equal(usageLine(usage[0], names), "Kit · shared with everyone · runs support-inbox");
    assert.equal(usageLine(usage[1], names), "A Lemma you cannot open · your own calls");
    const impact = readImpact({ auth_config_id: "ac", account_count: 3, pod_account_count: 2,
        pod_connectors: [{ pod_id: "p1", slug: "gmail", mode: "PER_MEMBER" }, { pod_id: "p1", slug: "support-inbox", mode: "SHARED" }] });
    assert.deepEqual(impactLines(impact, names), ["3 connected accounts are removed with it.", "Kit loses gmail, support-inbox."]);
    assert.deepEqual(impactLines(readImpact({}), names), ["Nothing uses it yet. Nobody loses anything."]);
});

test("the impact preview names the surfaces, schedules and subscriptions that stop, by pod where I can open it", () => {
    const impact = readImpact({ auth_config_id: "ac", account_count: 1, pod_account_count: 1,
        pod_connectors: [{ pod_id: "p1", slug: "crm", mode: "SHARED" }, { pod_id: "p9", slug: "crm", mode: "PER_MEMBER" }],
        surface_count: 1, schedule_count: 3, event_subscription_count: 1,
        pods: [{ pod_id: "p1", name: "Kit", pod_connectors: ["crm"], surface_count: 1, schedule_count: 2, event_subscription_count: 1 }] });
    assert.equal(impact.surfaceCount, 1);
    assert.equal(impact.pods[0].name, "Kit");
    assert.deepEqual(impactLines(impact, () => ""), [
        "1 connected account is removed with it.",
        "1 surface stops answering, 3 schedules stop firing and 1 MCP event subscription ends.",
        "Kit loses crm: 1 surface, 2 schedules, 1 subscription.",
        "A Lemma you cannot open loses crm.",
    ], "the pod's name comes with the preview; one the reader cannot open stays unnamed");
});

test("a preview that could not read everything never says nobody loses anything", () => {
    const empty = readImpact({ auth_config_id: "ac", account_count: 0, pod_account_count: 0, pod_connectors: [], complete: false });
    assert.equal(empty.complete, false);
    assert.deepEqual(impactLines(empty, () => ""), ["Couldn’t read everything this affects."]);
    const some = readImpact({ auth_config_id: "ac", account_count: 2, pod_account_count: 0, pod_connectors: [], complete: false });
    assert.deepEqual(impactLines(some, () => ""), ["2 connected accounts are removed with it.", "Couldn’t read everything this affects."]);
    assert.equal(readImpact({}).complete, true, "an older server that says nothing was complete");
});

test("pods I cannot open are counted, not named, in the impact preview", () => {
    const one = readImpact({ auth_config_id: "ac", account_count: 0, pod_account_count: 1, pod_connectors: [],
        unnamed_pod_count: 1, unnamed_pod_connector_count: 1 });
    assert.deepEqual(impactLines(one, () => ""), ["1 Lemma you cannot open loses a connector."]);
    const many = readImpact({ auth_config_id: "ac", account_count: 0, pod_account_count: 0,
        pod_connectors: [{ pod_id: "p1", slug: "crm", mode: "SHARED" }],
        pods: [{ pod_id: "p1", name: "Kit", pod_connectors: ["crm"] }],
        unnamed_pod_count: 2, unnamed_pod_connector_count: 3 });
    assert.deepEqual(impactLines(many, () => ""), ["Kit loses crm.", "2 Lemmas you cannot open lose 3 connectors."]);
    const unchecked = readImpact({ auth_config_id: "ac", account_count: 0, pod_account_count: 0, pod_connectors: [],
        unnamed_pod_count: 1, unnamed_pod_connector_count: 2, complete: false });
    assert.deepEqual(impactLines(unchecked, () => ""), ["1 more Lemma loses 2 connectors.", "Couldn’t read everything this affects."],
        "an unfinished check says nothing about who can open what");
});

test("I may install as the server says, not as a copy of its role table says", () => {
    assert.deepEqual(readOrgActions({ organization_id: "o1", actions: ["org.read", INSTALLATION_MANAGE, 3] }), ["org.read", INSTALLATION_MANAGE]);
    assert.deepEqual(readOrgActions(null), [], "nothing readable holds nothing");
});

test("an account's grantees go out as roles and members", () => {
    assert.deepEqual(accountPatchBody({ visibility: "RESTRICTED", shareWith: [{ type: "ROLE", id: "r-qa" }, { type: "POD_MEMBER", id: "m2" }] }).share_with,
        [{ grantee_type: "ROLE", grantee_id: "r-qa" }, { grantee_type: "POD_MEMBER", grantee_id: "m2" }]);
});

test("an agent is granted pod connectors by slug, and every other grant goes back untouched", () => {
    const grants = [
        { resource: "DATASTORE_TABLE", name: "campaigns", permissions: ["record.read"] },
        { resource: "connector", name: "old", permissions: [CONNECTOR_USE] },
    ];
    assert.deepEqual(connectorGrantSlugs(grants), ["old"]);
    const next = withConnectorGrants(grants, ["support-inbox", "gmail", "gmail"]);
    assert.deepEqual(next.map((one) => one.name), ["campaigns", "support-inbox", "gmail"]);
    assert.deepEqual(grantsBody(next).grants[0], { resource_type: "datastore_table", resource_name: "campaigns", permission_ids: ["record.read"] });
    assert.deepEqual(grantsBody(next).grants[1], { resource_type: "connector", resource_name: "support-inbox", permission_ids: [CONNECTOR_USE] });
});

test("a surface says which connector it runs as, and offers another shared one when its own runs as nobody", () => {
    const view = readConnection({ pod_connector: "support-inbox", connection: {
        pod_connector: "support-inbox", connector_id: "gmail", display_name: "support@acme.com", status: "MISSING" as never,
        connected_by: { user_id: "alice", name: "Alice", is_you: false, is_pod_member: true },
    } })!;
    assert.equal(connectionLine(view), "Runs as support-inbox · support@acme.com · by Alice");
    const list = [
        { slug: "support-inbox", connectorId: "gmail", mode: "SHARED", status: "NEEDS_ACCOUNT" },
        { slug: "support-2", connectorId: "gmail", mode: "SHARED", status: "READY" },
        { slug: "gmail", connectorId: "gmail", mode: "PER_MEMBER", status: "READY" },
        { slug: "slack", connectorId: "slack", mode: "SHARED", status: "READY" },
    ];
    assert.ok(needsRebind(view, list[0]));
    assert.match(connectionTrouble(view, list[0]), /no account shared/);
    assert.deepEqual(rebindChoices(view, list, "gmail").map((one) => one.slug), ["support-2"]);
    // Only connectors for the surface's own app: one whose app is unknown is
    // not offered, and with no app known nothing is.
    assert.deepEqual(rebindChoices(view, [...list, { slug: "mystery", connectorId: "", mode: "SHARED", status: "READY" }], "gmail").map((one) => one.slug), ["support-2"]);
    assert.deepEqual(rebindChoices({ ...view, connectorId: "" }, list, ""), []);
    assert.equal(surfaceApp({ ...view, connectorId: "" }, { connectorId: "slack" }, "telegram"), "slack");
    assert.equal(surfaceApp({ ...view, connectorId: "" }, undefined, "slack"), "slack", "else the platform's connector");
    assert.equal(surfaceApp(view, { connectorId: "slack" }, "telegram"), "gmail", "the connection's own app first");
    assert.ok(needsRebind({ ...view, status: "CONNECTED" }, undefined), "a connector that is gone is repaired too");
    assert.ok(!needsRebind({ ...view, status: "CONNECTED" }, { status: "READY" }));
    assert.equal(readConnection({ pod_connector: null, connection: null }), null);
});

test("the server's connect_url lands on the teammate's Connections, opened on the connector", () => {
    assert.equal(legacyAddress(["pods", "p1", "connections"], new URLSearchParams({ connector: "gmail" })), "/l/p1/settings/connections?connector=gmail");
    assert.equal(legacyAddress(["pods", "p1", "connections"], new URLSearchParams()), "/l/p1/settings/connections");
    assert.equal(legacyAddress(["pod", "p1", "connectors"], new URLSearchParams()), "/l/p1/about/connections");
    assert.equal(connectionsPlace("p 1", "a b"), "/l/p%201/about/connections?connector=a%20b");
    const read = readAddress("/t/p1/about/connections");
    assert.equal(read.section, "connections");
    assert.equal(writeAddress(read), "/l/p1/about/connections");
    assert.equal(readAddress("/t/p1/about/connectors").section, "connections", "an old link to the part still opens it");
});

test("Open connections from the classic workspace stays in it", () => {
    /* Rather than moving the reader into the app at /t halfway through
       connecting a platform. */
    const was = Object.getOwnPropertyDescriptor(globalThis, "window");
    Object.defineProperty(globalThis, "window", { value: { location: { pathname: "/l/p1/apps" } }, configurable: true, writable: true });
    try {
        assert.equal(connectionsPlace("p 1", "a b"), "/l/p%201/about/connections?connector=a%20b");
    } finally {
        if (was) Object.defineProperty(globalThis, "window", was);
        else delete (globalThis as { window?: unknown }).window;
    }
});

/* ── the live source, against a recorded API ───────────────────────── */

async function recorded(
    context: { mock: { method: (target: object, name: string, impl: (...args: never[]) => unknown) => unknown } },
    answer: (path: string, method: string, body: Record<string, unknown> | null) => { status?: number; body: unknown },
) {
    process.env.NEXT_PUBLIC_API_URL = "https://pod-connections.example.invalid";
    disconnect();
    const requests: { path: string; method: string; search: string; body: Record<string, unknown> | null }[] = [];
    context.mock.method(globalThis, "fetch", async (input: string | URL | Request, init?: RequestInit) => {
        const url = new URL(input instanceof Request ? input.url : String(input));
        const method = init?.method ?? (input instanceof Request ? input.method : "GET");
        const raw = typeof init?.body === "string" ? init.body : input instanceof Request ? await input.text() : "";
        const body = raw ? JSON.parse(raw) as Record<string, unknown> : null;
        requests.push({ path: url.pathname, method, search: url.search, body });
        const said = answer(url.pathname, method, body);
        return new Response(said.status === 204 ? null : JSON.stringify(said.body), { status: said.status ?? 200, headers: { "content-type": "application/json" } });
    });
    return requests;
}

async function withApi(run: () => Promise<void>) {
    const original = process.env.NEXT_PUBLIC_API_URL;
    try { await run(); } finally {
        if (original === undefined) delete process.env.NEXT_PUBLIC_API_URL;
        else process.env.NEXT_PUBLIC_API_URL = original;
        disconnect();
    }
}

test("using an account for everyone is one request to share-as, and nothing else", (context) => withApi(async () => {
    const requests = await recorded(context, (_path, _method, body) => ({ body: {
        id: "x", pod_id: "p1", slug: body?.slug, auth_config_id: "ac-gmail", connector_id: "gmail", mode: "SHARED", status: "READY", required: false, visibility: "POD",
        shared_pod_account_id: "pa-mine", shared_account: { pod_account_id: "pa-mine", owner_user_id: "me", email: "me@acme.com" }, created_at: "", updated_at: "",
    } }));
    const made = await liveSource.shareAccountAs("p1", "pa-mine", "team-gmail", "The team inbox");
    assert.equal(made.slug, "team-gmail");
    assert.equal(made.sharedPodAccountId, "pa-mine");
    assert.deepEqual(requests.map((one) => [one.method, one.path, one.body]), [
        ["POST", "/pods/p1/connector-accounts/pa-mine/share-as", { slug: "team-gmail", purpose: "The team inbox" }],
    ], "no listing first, no declare, widen and point: the server checks and does it all");
}));

test("a refused share-as arrives typed, with its connect link when the server sends one", (context) => withApi(async () => {
    await recorded(context, () => ({ status: 400, body: {
        code: "POD_CONNECTOR_INVALID", message: "gmail uses each person's own account, so it cannot run as one shared account.",
        details: { reason: "not_shared_mode" },
    } }));
    const refused = await liveSource.shareAccountAs("p1", "pa-mine", "gmail").then(() => null, (error: unknown) => error);
    const said = failureOf(refused, "It could not be shared.");
    assert.match(said.message, /each person's own account/);
    assert.equal(said.code, "POD_CONNECTOR_INVALID");
    assert.equal(said.connectUrl, "");
    const link = "https://app.example.invalid/pods/p1/connections?connector=gmail";
    assert.equal(failureOf(Object.assign(new Error("Reconnect it first."), { code: "POD_ACCOUNT_REAUTH", details: { connect_url: link } }), "x").connectUrl, link,
        "any typed refusal's link is offered, not only the connection codes'");
}));

test("an editor's coverage is read per connector from its own route", (context) => withApi(async () => {
    const requests = await recorded(context, () => ({ body: { ...GMAIL_COVERAGE, truncated: true } }));
    const coverage = await liveSource.podConnectorCoverage("p1", "gmail");
    assert.equal(coverage.connected, 5);
    assert.equal(coverage.truncated, true);
    assert.deepEqual(requests.map((one) => [one.method, one.path]), [["GET", "/pods/p1/connectors/gmail/coverage"]]);
}));

test("turning sandbox use off returns the account as saved, with the server's notice", (context) => withApi(async () => {
    await recorded(context, (_path, _method, body) => ({ body: { ...ACCOUNTS_RAW_MINE, visibility: "POD", ...body, created_at: "",
        notice: "No sandbox is given this account's token from now on." } }));
    const saved = await liveSource.changePodAccount("p1", "pa-mine", { sandboxes: false });
    assert.equal(saved.sandboxes, false);
    assert.equal(saved.notice, "No sandbox is given this account's token from now on.");
}));

test("editors declare, change and remove connectors; owners share and withdraw accounts", (context) => withApi(async () => {
    const requests = await recorded(context, (_path, method, body) => method === "DELETE"
        ? { status: 204, body: null }
        : { body: { ...body, id: "x", pod_id: "p1", slug: "gmail", mode: "PER_MEMBER", status: "READY", required: true, visibility: "POD", created_at: "", updated_at: "",
            account_id: "acc", auth_config_id: "ac", owner_user_id: "me", is_member_default: true, source: "MANUAL" } });
    const made = await liveSource.addPodConnector("p1", { slug: "gmail", authConfigId: "ac", mode: "PER_MEMBER", sharedPodAccountId: "", required: true, purpose: "" });
    assert.equal(made.slug, "gmail");
    await liveSource.changePodConnector("p1", "gmail", { required: false });
    await liveSource.removePodConnector("p1", "gmail");
    await liveSource.changePodAccount("p1", "pa1", { visibility: "RESTRICTED", shareWith: [{ type: "POD_MEMBER", id: "m2" }] });
    await liveSource.removePodAccount("p1", "pa1");
    assert.deepEqual(requests.map((one) => [one.method, one.path, one.body]), [
        ["POST", "/pods/p1/connectors", { slug: "gmail", auth_config_id: "ac", mode: "PER_MEMBER", required: true }],
        ["PATCH", "/pods/p1/connectors/gmail", { required: false }],
        ["DELETE", "/pods/p1/connectors/gmail", null],
        ["PATCH", "/pods/p1/connector-accounts/pa1", { visibility: "RESTRICTED", share_with: [{ grantee_type: "POD_MEMBER", grantee_id: "m2" }] }],
        ["DELETE", "/pods/p1/connector-accounts/pa1", null],
    ]);
}));

test("where-used, the impact preview, my org permissions and agent grants each ask their own route", (context) => withApi(async () => {
    const requests = await recorded(context, (path) => {
        if (path.endsWith("/pods")) return { body: { account_id: "acc", items: [{ pod_id: "p1", pod_account_id: "pa1", visibility: "POD", source: "MANUAL", is_member_default: true, used_by: ["s"] }] } };
        if (path.endsWith("/impact")) return { body: { auth_config_id: "ac", account_count: 1, pod_account_count: 0, pod_connectors: [] } };
        if (path.endsWith("/permissions/me")) return { body: { organization_id: "o1", actions: ["org.read"] } };
        if (path.endsWith("/ask-members")) return { body: { asked: [{ user_id: "bob", pod_member_id: "m-bob", name: "Bob", standing: "MISSING" }], asked_recently: [], connect_url: "u" } };
        return { body: { agent_id: "ag", agent_name: "researcher", grants: [{ resource_type: "connector", resource_name: "gmail", permission_ids: [CONNECTOR_USE] }] } };
    });
    assert.equal((await liveSource.accountUsage("acc"))[0].usedBy[0], "s");
    assert.equal((await liveSource.installationImpact("o1", "ac")).accountCount, 1);
    assert.ok(!(await liveSource.orgActions("o1")).includes(INSTALLATION_MANAGE), "a member may not change installations");
    const grants = await liveSource.setAgentGrants("p1", "researcher", withConnectorGrants([], ["gmail"]));
    assert.deepEqual(connectorGrantSlugs(grants), ["gmail"]);
    assert.equal((await liveSource.askMembersToConnect("p1", "gmail")).asked[0].name, "Bob");
    assert.deepEqual(requests.map((one) => [one.method, one.path]), [
        ["GET", "/me/connectors/accounts/acc/pods"],
        ["GET", "/organizations/o1/installations/ac/impact"],
        ["GET", "/organizations/o1/permissions/me"],
        ["PUT", "/pods/p1/agents/researcher/permissions"],
        ["POST", "/pods/p1/connectors/gmail/ask-members"],
    ]);
    assert.deepEqual(requests[3].body, { grants: [{ resource_type: "connector", resource_name: "gmail", permission_ids: [CONNECTOR_USE] }] });
}));

/* ── the page ──────────────────────────────────────────────────────── */

/** `GET /pods/p1/connections`, one row for each way a row can stand. */
const CONNECTIONS_RAW = {
    may_manage: true,
    github_bot: { installations: [{ login: "acme", repository_selection: "selected" }] },
    items: [
        { slug: "github", connector_id: "github", title: "GitHub", icon: "", mode: "PER_MEMBER", purpose: "", required: false, status: "READY", auth_config_id: "ac-github",
            runs_as: { kind: "me", label: "me-gh" }, me: { standing: "ready", account: { id: "acc-gh", label: "me-gh", pod_account_id: "pa-gh" }, existing: null },
            coverage: { connected: 1, total: 2 } },
        { slug: "linear", connector_id: "linear", title: "Linear", mode: "PER_MEMBER", status: "READY", auth_config_id: "ac-linear",
            runs_as: { kind: "nobody" }, me: { standing: "use_existing", account: null, existing: { account_id: "acc-lin", label: "me@linear" } }, coverage: { connected: 2, total: 2 } },
        { slug: "notion", connector_id: "notion", title: "Notion", mode: "PER_MEMBER", status: "READY", auth_config_id: "ac-notion",
            runs_as: { kind: "me", label: "me@notion" }, me: { standing: "reconnect", account: { id: "a-notion", label: "me@notion", pod_account_id: "pa-notion" } }, coverage: { connected: 1, total: 2 } },
        { slug: "jira", connector_id: "jira", title: "Jira", mode: "PER_MEMBER", status: "READY", purpose: "Tracks the bugs", auth_config_id: "ac-jira",
            runs_as: { kind: "nobody" }, me: { standing: "connect" }, coverage: { connected: 1, total: 2 } },
        { slug: "support-inbox", connector_id: "gmail", title: "Gmail", mode: "SHARED", status: "READY", auth_config_id: "ac-gmail",
            runs_as: { kind: "person", label: "support@acme.com", owner_user_id: "alice", owner_name: "Alice" }, me: { standing: "ready" }, coverage: null },
        { slug: "teams-ops", connector_id: "teams", title: "Microsoft Teams", mode: "SHARED", status: "NEEDS_ACCOUNT", auth_config_id: "ac-teams",
            runs_as: { kind: "nobody", owner_user_id: "alice", owner_name: "Alice" }, me: { standing: "waiting", existing: { account_id: "acc-teams", label: "me@teams" } }, coverage: null },
        { slug: "github-bot", connector_id: "github", title: "GitHub", mode: "APP", status: "READY", runs_as: { kind: "app", label: "acme" }, me: { standing: "ready" }, coverage: null },
        { slug: "hubspot", connector_id: "hubspot", title: "HubSpot", mode: "PER_MEMBER", status: "READY", runs_as: { kind: "nobody" }, me: { standing: "not_allowed" }, coverage: null },
        { slug: "gmail", connector_id: "gmail", title: "Gmail", mode: "PER_MEMBER", status: "UNAVAILABLE", runs_as: { kind: "nobody" }, me: { standing: "waiting" }, coverage: null },
    ],
};
const CONNECTIONS = readPodConnections(CONNECTIONS_RAW);

test("the connections read says, per app, whose account it runs as and where I stand, and nothing it doesn't know reads as working", () => {
    assert.equal(CONNECTIONS.mayManage, true);
    assert.equal(CONNECTIONS.items.length, 9);
    assert.equal(botLogins(CONNECTIONS.githubBot), "acme");
    assert.equal(botReach(CONNECTIONS.githubBot), "Reaches the repositories the App was given.");
    const [github, linear] = CONNECTIONS.items;
    assert.equal(github.me.account?.podAccountId, "pa-gh");
    assert.equal(github.authConfigId, "ac-github");
    assert.deepEqual(linear.me.existing, { accountId: "acc-lin", label: "me@linear" });
    const odd = readPodConnections({ items: [
        { slug: "odd", mode: "LATER", status: "SOMETHING", me: { standing: "maybe" } },
        { slug: "said-ready", mode: "APP", status: "NEEDS_INSTALLATION", me: { standing: "ready" } },
        { slug: "" },
        "nonsense",
    ], github_bot: { installations: [] } });
    assert.equal(odd.items.length, 2, "a row with no slug is not one");
    assert.equal(odd.items[0].mode, "PER_MEMBER");
    assert.equal(odd.items[0].status, "UNAVAILABLE", "an unknown status is not ready");
    assert.equal(odd.items[0].me.standing, "unknown");
    assert.equal(odd.items[0].runsAs.kind, "nobody");
    assert.equal(odd.items[1].me.standing, "waiting", "ready on a row that can't run believes the row");
    assert.equal(odd.githubBot, null, "a bot with no installation is no bot");
    assert.equal(odd.mayManage, false);
    assert.deepEqual(readPodConnections(null), { mayManage: false, githubBot: null, items: [] });
});

test("each row says whose account it runs as, or why it can't run and who can fix it", () => {
    const said = (slug: string) => connectionSaid(CONNECTIONS.items.find((one) => one.slug === slug)!, "Kit", "me");
    assert.deepEqual(said("github"), { line: "Runs as you · me-gh", status: { word: "Connected", tone: "ok" } });
    assert.match(said("linear").line, /Yours is me@linear, no sign-in needed/);
    assert.deepEqual(said("notion").status, { word: "Signed out", tone: "bad" });
    assert.equal(said("notion").line, "Notion signed you out. Kit can't use it for you until you sign in again.");
    assert.equal(said("jira").line, "Runs as each person's own account. Tracks the bugs.");
    assert.equal(said("support-inbox").line, "Alice's account, for everyone · support@acme.com");
    assert.deepEqual(said("teams-ops").status, { word: "Waiting on Alice", tone: "warn" });
    assert.match(said("teams-ops").line, /Ran as Alice's account, which isn't shared any more/);
    assert.equal(said("github-bot").line, "Runs as Kit's bot · acme's GitHub App. Nobody needs to connect.");
    assert.deepEqual(said("hubspot").status, { word: "Not for you", tone: "off" });
    assert.deepEqual(said("gmail").status, { word: "Switched off", tone: "off" });
    const mine = { ...CONNECTIONS.items[4], runsAs: { ...CONNECTIONS.items[4].runsAs, ownerUserId: "me" } };
    assert.equal(connectionSaid(mine, "Kit", "me").line, "Your account, for everyone · support@acme.com");
    const uninstalled = readPodConnections({ items: [{ slug: "gh", connector_id: "github", title: "GitHub", mode: "APP", status: "NEEDS_INSTALLATION", me: { standing: "waiting" } }] }).items[0];
    assert.deepEqual(connectionSaid(uninstalled, "Kit", "me").status, { word: "App not installed", tone: "bad" });
});

test("slugs show only where a Lemma has two of one app, and the band names who hasn't connected", () => {
    assert.deepEqual([...twiceOver(CONNECTIONS.items)].sort(), ["github", "gmail"]);
    assert.equal(connectedCount(CONNECTIONS.items[0].coverage), "1 of 2 connected");
    assert.equal(connectedCount(null), "");
    assert.equal(someoneMissing(CONNECTIONS.items[0]), true);
    assert.equal(someoneMissing(CONNECTIONS.items[1]), false);
    assert.equal(missingLine(["Alice"], "GitHub", "Kit"), "Alice hasn't connected GitHub, so Kit can't use it for Alice.");
    assert.equal(missingLine(["Alice", "Bob"], "GitHub", "Kit"), "Alice and Bob haven't connected GitHub, so Kit can't use it for them.");
    assert.equal(botSlug([]), "github");
    assert.equal(botSlug(["github"]), "github-bot");
    assert.equal(botSlug(["github", "github-bot"]), "github-bot-2");
});

test("the connections read and a connect started from a Lemma each ask their own route", (context) => withApi(async () => {
    const requests = await recorded(context, (path) => {
        if (path.endsWith("/connections")) return { body: CONNECTIONS_RAW };
        if (path.endsWith("/connect-requests")) return { body: { authorization_url: "https://github.example.invalid/authorize", auth_config_id: "ac-github" } };
        return { body: { items: [] } };
    });
    assert.equal((await liveSource.podConnections("p1")).items.length, 9);
    const started = await liveSource.startAccount("o1", "github", "ac-github", "/l/p1/settings/connections", undefined, undefined, "p1");
    assert.equal(started.authorizeUrl, "https://github.example.invalid/authorize");
    assert.equal(requests[0].path, "/pods/p1/connections");
    const asked = requests.find((one) => one.path.endsWith("/connect-requests"));
    assert.deepEqual(asked?.body, { auth_config_id: "ac-github", return_to: "/l/p1/settings/connections", pod_id: "p1" }, "the Lemma rides along, so finishing adds the app there");
    const elsewhere = await recorded(context, (path) => path.endsWith("/connect-requests") ? { body: { authorization_url: "u" } } : { body: { items: [] } });
    await liveSource.startAccount("o1", "github", "ac-github", "/connectors");
    assert.ok(!("pod_id" in (elsewhere.find((one) => one.path.endsWith("/connect-requests"))?.body ?? {})), "from the organization's page, no Lemma");
}));

/* ── drawn ─────────────────────────────────────────────────────────── */

/* Every window and cache a drawing made, closed once all have run: an open
   one keeps the process alive past the last test. */
const opened: { close(): void }[] = [];
after(() => { for (const one of opened) one.close(); });

function dom() {
    const window = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { pretendToBeVisual: true, url: "https://app.example.invalid/l/p1/settings/connections" }).window;
    for (const [key, value] of Object.entries({
        window, document: window.document, navigator: window.navigator, HTMLElement: window.HTMLElement, Node: window.Node,
        IS_REACT_ACT_ENVIRONMENT: true, localStorage: window.localStorage,
    })) Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
    window.HTMLElement.prototype.scrollIntoView = () => {};
    opened.push(window);
    return window;
}

const POD = {
    id: "p1", orgId: "o1", name: "Kit", iconUrl: null, teammate: { name: "Kit", initials: "K", iconUrl: null }, subtitle: "", waiting: "",
    members: [
        { id: "m-me", userId: "me", name: "Me", initials: "ME", kind: "person" as const, role: "Editor", can: "" },
        { id: "m-alice", userId: "alice", name: "Alice", initials: "AL", kind: "person" as const, role: "Member", can: "" },
    ],
};

/** What the banner reads (the pod's connectors) and what the page reads
 *  (its connections), seeded so no request leaves a test. */
function seeded(actions: string[], connections: unknown = CONNECTIONS_RAW) {
    const cache = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
    cache.setQueryData(["pod-connectors", "p1"], CONNECTORS);
    cache.setQueryData(["pod-connectors", "p1", "connections"], readPodConnections(connections));
    for (const slug of ["github", "notion", "jira"]) {
        cache.setQueryData(["pod-connectors", "p1", "coverage", slug], readCoverage({ eligible: 2, connected: 1, truncated: false,
            waiting_on: slug === "github" ? [{ user_id: "alice", pod_member_id: "m-alice", name: "Alice", standing: "MISSING" }]
                : [{ user_id: "me", pod_member_id: "m-me", name: "Me", standing: "MISSING" }] }));
    }
    cache.setQueryData(["accounts", "o1"], MINE.map((one) => ({ ...one, ref: "", installState: "READY", createdAt: "" })));
    cache.setQueryData(["connectors"], [
        { id: "gmail", title: "Gmail", description: "", icon: "", active: true, kinds: [] },
        { id: "github", title: "GitHub", description: "", icon: "", active: true, kinds: [] },
        { id: "notion", title: "Notion", description: "", icon: "", active: true, kinds: [] },
        { id: "asana", title: "Asana", description: "", icon: "", active: true, kinds: [{ kind: "composio", auth_scheme: "OAUTH2", system_default_available: true }] },
    ]);
    cache.setQueryData(["installs", "o1"], [
        { id: "ac-gmail", connector_id: "gmail", kind: "composio", name: "gmail", status: "ACTIVE" },
        { id: "ac-github", connector_id: "github", kind: "composio", name: "github", status: "ACTIVE" },
    ]);
    cache.setQueryData(["pod-accounts", "p1"], ACCOUNTS);
    cache.setQueryData(["pod-actions", "p1"], { actions });
    opened.push({ close: () => cache.clear() });
    return cache;
}

async function drawPage(t: { after: (fn: () => unknown) => void }, window: ReturnType<typeof dom>, cache: QueryClient, focus: string | null = null) {
    const { PodConnections } = await import("../src/connect/pod-connections.tsx");
    const { createRoot } = await import("react-dom/client");
    const holder = window.document.createElement("div");
    window.document.body.append(holder);
    const root = createRoot(holder);
    // Unmounted however the test ends: a root left mounted after a failed
    // assertion kept the process alive, and the whole suite never exited.
    t.after(() => act(async () => root.unmount()));
    await act(async () => {
        root.render(createElement(QueryClientProvider, { client: cache }, createElement(PodConnections, { pod: POD, focus })));
    });
    return holder;
}

const click = async (window: ReturnType<typeof dom>, element: Element | null | undefined) => {
    assert.ok(element, "there is something to click");
    await act(async () => { element!.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); });
};
const buttonNamed = (within: ParentNode, text: string) => [...within.querySelectorAll("button")].find((one) => one.textContent?.trim() === text);

test("the Connections page draws one row per app with the one thing to do, and editing only for editors", async (t) => {
    const window = dom();
    const editor = await drawPage(t, window, seeded(["pod.read", "pod.connector.manage", "connector.use"]));
    const text = editor.textContent ?? "";
    assert.match(text, /Runs as you · me-gh/);
    assert.match(text, /You’ve connected Linear already\./);
    assert.match(text, /Alice's account, for everyone · support@acme\.com/);
    assert.match(text, /Runs as Kit's bot · acme's GitHub App\. Nobody needs to connect\./);
    assert.match(text, /Not for you/);
    assert.match(text, /Switched off/);
    assert.match(text, /1 of 2 connected/);
    const buttons = [...editor.querySelectorAll("button")].map((one) => one.textContent?.trim());
    for (const want of ["Use it here", "Reconnect", "Connect", "Use mine", "Add an app"]) assert.ok(buttons.includes(want), want);
    /* Slugs only where they tell two apart. */
    const slugs = [...editor.querySelectorAll(".kpc-slug")].map((one) => one.textContent);
    assert.deepEqual(slugs.sort(), ["github", "github-bot", "gmail", "support-inbox"]);
    /* The band: who else hasn't connected, never me (my own row asks me). */
    const band = editor.querySelector(".kneed")?.textContent ?? "";
    assert.match(band, /Alice hasn't connected GitHub, so Kit can't use it for Alice\./);
    assert.ok(buttonNamed(editor, "Ask Alice"));
    assert.doesNotMatch(band, /Me hasn't/);
    assert.equal(editor.querySelector(".podconn__offer"), null, "no banner from the old page");
    assert.doesNotMatch(text, /Your accounts in Kit|Bring another of yours in|Use for everyone as…/, "one list, not two");

    const member = await drawPage(t, window, seeded(["pod.read", "connector.use"], { ...CONNECTIONS_RAW, may_manage: false }));
    const plain = [...member.querySelectorAll("button")].map((one) => one.textContent?.trim());
    assert.ok(!plain.includes("Add an app"), "a member cannot add one");
    assert.ok(!plain.includes("Use mine"), "pointing an app at an account is an editor's");
    assert.ok(plain.includes("Connect"), "but still connects their own");
    assert.equal(member.querySelector(".kneed"), null, "nor counts who else has");
    assert.doesNotMatch(member.textContent ?? "", /of 2 connected/);
});

test("a link picks out its app's row, offers to add an app the Lemma doesn't use, and says when a name matches nothing", async (t) => {
    const window = dom();
    const linked = await drawPage(t, window, seeded(["pod.read", "pod.connector.manage", "connector.use"]), "support-inbox");
    assert.match(linked.querySelector("[data-focused]")?.textContent ?? "", /Alice's account, for everyone/);
    const offered = await drawPage(t, window, seeded(["pod.read", "connector.use"]), "asana");
    const row = offered.querySelector("[data-focused]");
    assert.match(row?.textContent ?? "", /Kit doesn’t use Asana yet\. Connect your account and it will/);
    assert.ok(buttonNamed(row!, "Connect"));
    const gone = await drawPage(t, window, seeded(["pod.read", "connector.use"]), "nothing-like-it");
    assert.match(gone.textContent ?? "", /Kit doesn’t use anything called nothing-like-it\./);
});

test("an app's sheet says whose account it runs as, who has connected, and how to take it out", async (t) => {
    const window = dom();
    t.mock.method(liveSource, "podConnections", async () => CONNECTIONS);
    t.mock.method(liveSource, "podConnectorCoverage", async () => readCoverage({ eligible: 2, connected: 1, waiting_on: [{ user_id: "alice", name: "Alice", standing: "MISSING" }] }));
    const sent: unknown[] = [];
    t.mock.method(liveSource, "changePodAccount", async (...args: unknown[]) => { sent.push(["changePodAccount", ...args]); return ACCOUNTS[1]; });
    t.mock.method(liveSource, "changePodConnector", async (...args: unknown[]) => { sent.push(["changePodConnector", ...args]); return CONNECTORS[1]; });
    t.mock.method(liveSource, "shareAccountAs", async (...args: unknown[]) => { sent.push(["shareAccountAs", ...args]); return CONNECTORS[1]; });
    const holder = await drawPage(t, window, seeded(["pod.read", "pod.connector.manage", "connector.use"]));
    await click(window, buttonNamed(holder, "GitHub"));
    const sheet = window.document.querySelector(".ksheet");
    const said = sheet?.textContent ?? "";
    assert.match(said, /GitHub in Kit/);
    assert.match(said, /Each person’s own account/);
    assert.match(said, /Mine, for everyone/);
    assert.match(said, /Kit’s bot/, "the organization's App is offered where it's installed");
    assert.match(said, /Who’s connected/);
    assert.ok(buttonNamed(sheet!, "Ask"), "Alice, asked by name");
    assert.ok(buttonNamed(sheet!, "Disconnect my account"));
    assert.ok(buttonNamed(sheet!, "Remove GitHub from Kit"));
    assert.doesNotMatch(said, /Allow it inside sandboxes/, "only once it is shared");

    /* Mine, for everyone: one confirmation, then one request that shares my
       account and switches the app to it together. */
    const mine = [...sheet!.querySelectorAll("label.kopt")].find((one) => one.textContent?.startsWith("Mine, for everyone"))?.querySelector("input");
    await act(async () => { mine!.click(); });
    const dialog = window.document.querySelector(".modal");
    assert.match(dialog?.textContent ?? "", /Run GitHub for everyone as you\?/);
    assert.match(dialog?.textContent ?? "", /Alice stops needing their own GitHub to use Kit\./);
    await act(async () => {
        buttonNamed(dialog!, "Run it as me")!.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
        await new Promise((done) => setTimeout(done, 20));
    });
    assert.deepEqual(sent, [["shareAccountAs", "p1", "pa-gh", "github"]]);
    await act(async () => { await new Promise((done) => setTimeout(done, 20)); });
});

test("adding GitHub where the organization's App is installed offers the bot first", async (t) => {
    const window = dom();
    const added: unknown[] = [];
    t.mock.method(liveSource, "addPodConnector", async (_podId: string, draft: unknown) => {
        added.push(draft);
        return readPodConnector({ slug: "github-bot", connector_id: "github", mode: "APP", status: "READY" })!;
    });
    t.mock.method(liveSource, "podConnections", async () => CONNECTIONS);
    t.mock.method(liveSource, "podConnectorCoverage", async () => readCoverage({}));
    const withoutBot = { ...CONNECTIONS_RAW, items: CONNECTIONS_RAW.items.filter((one) => one.mode !== "APP") };
    const holder = await drawPage(t, window, seeded(["pod.read", "pod.connector.manage", "connector.use"], withoutBot));
    await click(window, buttonNamed(holder, "Add an app"));
    const picker = window.document.querySelector(".modal");
    assert.match(picker?.textContent ?? "", /Add an app to Kit/);
    assert.match(picker?.textContent ?? "", /Already used in your organization/);
    assert.match(picker?.textContent ?? "", /More apps/);
    const tiles = [...picker!.querySelectorAll(".kpc-tile")];
    /* A tile's words, without the initial its mark stands in with. */
    const named = (name: string) => tiles.find((one) => one.querySelector(":scope > span:last-child")?.textContent?.startsWith(name));
    const gmail = named("Gmail");
    assert.match(gmail?.textContent ?? "", /In Kit already/);
    assert.equal((gmail as HTMLButtonElement).disabled, true);
    assert.ok(named("Asana"), "the catalog's apps nobody installed");
    await click(window, named("GitHub"));
    const step = window.document.querySelector(".modal");
    assert.match(step?.textContent ?? "", /Kit can read and post as acme’s GitHub App/);
    await act(async () => {
        buttonNamed(step!, "Use the bot")!.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
        await new Promise((done) => setTimeout(done, 20));
    });
    assert.deepEqual(added, [{ slug: "github-bot", authConfigId: "", connectorId: "github", mode: "APP", sharedPodAccountId: "", required: false, purpose: "" }],
        "no slug asked for: github is taken, so github-bot");
    await act(async () => { await new Promise((done) => setTimeout(done, 20)); });
});

test("the banner asks for the required connectors I have not connected, and nothing when I have", async (t) => {
    const window = dom();
    const { ConnectBanner } = await import("../src/connect/pod-connect.tsx");
    const { createRoot } = await import("react-dom/client");
    const draw = async (cache: QueryClient) => {
        const holder = window.document.createElement("div");
        window.document.body.append(holder);
        const root = createRoot(holder);
        t.after(() => act(async () => root.unmount()));
        await act(async () => { root.render(createElement(QueryClientProvider, { client: cache }, createElement(ConnectBanner, { pod: POD }))); });
        return holder;
    };
    const cache = seeded(["pod.read", "connector.use"]);
    const holder = await draw(cache);
    assert.match(holder.textContent ?? "", /Kit uses Gmail and Notion — connect yours/);
    assert.deepEqual([...holder.querySelectorAll(".connectbanner__acts button")].map((one) => one.textContent), ["Connect Gmail", "Reconnect Notion"]);
    await act(async () => {
        cache.setQueryData(["pod-connectors", "p1"], CONNECTORS.filter((one) => !one.required));
        /* The cache tells its observers on the next tick. */
        await new Promise((done) => setTimeout(done, 20));
    });
    assert.equal(holder.querySelector(".connectbanner") === null, true, "nothing required is missing, so nothing is asked");

    // A viewer holds no connector.use: an account they connected would serve
    // nothing, so they are not asked for one.
    assert.equal((await draw(seeded(["pod.read"]))).querySelector(".connectbanner"), null);
    // Nor somebody a connector leaves out, whatever the pod-wide answer.
    const excluded = seeded(["pod.read", "connector.use"]);
    excluded.setQueryData(["pod-connectors", "p1"], CONNECTORS.map((one) => ({ ...one, canUse: false })));
    assert.equal((await draw(excluded)).querySelector(".connectbanner"), null);
});

test("the banner reads from the cache on a pod open, and asks the server again only on focus or after a connect", async (t) => {
    const window = dom();
    const { ConnectBanner } = await import("../src/connect/pod-connect.tsx");
    const { createRoot } = await import("react-dom/client");
    const cache = seeded(["pod.read", "connector.use"]);
    /* Read an hour ago: old, but a pod being opened is not a reason to read
       it again. */
    const hourAgo = Date.now() - 60 * 60_000;
    cache.setQueryData(["pod-connectors", "p1"], CONNECTORS, { updatedAt: hourAgo });
    cache.setQueryData(["accounts", "o1"], cache.getQueryData(["accounts", "o1"]), { updatedAt: hourAgo });
    const holder = window.document.createElement("div");
    window.document.body.append(holder);
    const root = createRoot(holder);
    t.after(() => act(async () => root.unmount()));
    await act(async () => { root.render(createElement(QueryClientProvider, { client: cache }, createElement(ConnectBanner, { pod: POD }))); });
    assert.match(holder.textContent ?? "", /connect yours/);
    for (const key of [["pod-connectors", "p1"], ["accounts", "o1"]]) {
        const state = cache.getQueryState(key)!;
        assert.equal(state.dataUpdatedAt, hourAgo, key[0] + " is not read again on mount");
        assert.equal(state.fetchStatus, "idle");
    }
    const observer = cache.getQueryCache().find({ queryKey: ["pod-connectors", "p1"] })!.observers[0];
    assert.equal(observer.options.refetchOnWindowFocus, "always", "coming back to the window reads it again");
});
