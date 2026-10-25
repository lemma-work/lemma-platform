import test, { after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import ts from "typescript";
import { JSDOM } from "jsdom";
import { act, createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
    askedLine, bannerLine, connectionsPlace, connectorCalled, connectorChanges, connectorCreateBody, connectorName, connectorPatchBody, coverageLine, failureOf, mayUseOnceConnected,
    missingForMe, modeBadge, myStatus, readAskOutcome, readCoverage, readPodConnectors, slugProblem, truncatedLine, waitingLine, wantsCoverage, type MyAccount,
} from "../src/data/pod-connectors.ts";
import {
    accountPatchBody, impactLines, ownedBy, readAccountUsage, readImpact, readPodAccounts, readShareRoles, shareAsBody, shareableFor, suggestedSlug,
    usageLine, visibilityWord,
} from "../src/data/pod-accounts.ts";
import { INSTALLATION_MANAGE, readOrgActions } from "../src/data/org-actions.ts";
import { CONNECTOR_USE, connectorGrantSlugs, grantsBody, withConnectorGrants } from "../src/data/agents.ts";
import { connectionLine, connectionTrouble, needsRebind, readConnection, rebindChoices, surfaceApp } from "../src/data/surface-settings.ts";
import { readAddress, writeAddress } from "../src/shell/address.ts";
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

test("an editor reads who a required connector is waiting on, by name and never by account", () => {
    const coverage = readCoverage(GMAIL_COVERAGE);
    assert.equal(coverageLine(coverage), "5/7 connected");
    assert.deepEqual(coverage.waitingOn.map((one) => [one.userId, one.name, one.standing]),
        [["bob", "Bob", "MISSING"], ["carol", "carol@acme.com", "REAUTH_REQUIRED"]], "a member with no name is said by their email");
    assert.equal(waitingLine(coverage), "Waiting on Bob, and carol@acme.com to reconnect");
    assert.equal(truncatedLine(coverage), "");
    assert.equal(coverageLine({ eligible: 3, connected: 3, waitingOn: [], truncated: false }), "Everyone connected");
    // Counted only part of the pod: never "Everyone", and said in words.
    const part = readCoverage({ ...GMAIL_COVERAGE, eligible: 3, connected: 3, waiting_on: [], truncated: true });
    assert.equal(coverageLine(part), "3/3 connected");
    assert.match(truncatedLine(part), /Only some of the pod’s members were counted/);
    assert.deepEqual(readCoverage(null), { eligible: 0, connected: 0, waitingOn: [], truncated: false });
});

test("coverage is asked for only by an editor, and only of a required per-member connector", () => {
    assert.equal(wantsCoverage(CONNECTORS[1], true), true, "gmail: required, each person's own");
    assert.equal(wantsCoverage(CONNECTORS[1], false), false, "it refuses anyone who may not manage connectors");
    assert.equal(wantsCoverage(CONNECTORS[0], true), false, "a shared one waits on nobody");
    assert.equal(wantsCoverage({ mode: "PER_MEMBER", required: false }, true), false, "nobody is asked to connect one not required");
    assert.ok(!("coverage" in CONNECTORS[1]), "a listing's member_coverage is not read");
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

test("a slug reads the same in a URL, a YAML key and a tool argument, and is free", () => {
    assert.equal(slugProblem("support-inbox", []), "");
    assert.match(slugProblem("Support Inbox", []), /Lowercase/);
    assert.match(slugProblem("gmail", ["gmail"]), /already has/);
    assert.match(slugProblem("", []), /name/);
});

test("declaring and changing a connector sends only what it means", () => {
    assert.deepEqual(connectorCreateBody({ slug: "gmail", authConfigId: "ac", mode: "PER_MEMBER", sharedPodAccountId: "pa", required: true, purpose: "  inbox " }),
        { slug: "gmail", auth_config_id: "ac", mode: "PER_MEMBER", required: true, purpose: "inbox" }, "no shared account on a per-member one");
    assert.deepEqual(connectorCreateBody({ slug: "s", authConfigId: "ac", mode: "SHARED", sharedPodAccountId: "pa", required: true, purpose: "" }),
        { slug: "s", auth_config_id: "ac", mode: "SHARED", shared_pod_account_id: "pa" }, "nobody is asked to connect a shared one");
    assert.deepEqual(connectorPatchBody({ sharedPodAccountId: "" }), { shared_pod_account_id: null });
    assert.deepEqual(connectorChanges(CONNECTORS[0], { mode: "SHARED", purpose: "The support mailbox", sharedPodAccountId: "pa-alice" }), {}, "nothing touched, nothing sent");
    assert.deepEqual(connectorChanges(CONNECTORS[0], { mode: "PER_MEMBER" }), { mode: "PER_MEMBER", sharedPodAccountId: "" });
});

test("pod accounts read their owner and visibility, and an editor picks only shared ones", () => {
    assert.equal(ACCOUNTS.length, 3, "a row with no id is not one");
    assert.equal(ACCOUNTS[1].label, "me@acme.com");
    assert.equal(ACCOUNTS[1].sandboxes, false);
    assert.equal(ACCOUNTS[0].sandboxes, null, "a server without the setting says nothing about it");
    assert.deepEqual(shareableFor(ACCOUNTS, "ac-gmail").map((one) => one.id), ["pa-alice", "pa-bob"], "never one still personal to its owner");
    assert.deepEqual(ownedBy(ACCOUNTS, "me").map((one) => one.id), ["pa-mine"]);
    assert.equal(visibilityWord("RESTRICTED"), "Shared with some people here");
    assert.deepEqual(accountPatchBody({ visibility: "RESTRICTED", shareWith: [{ type: "POD_MEMBER", id: "m1" }], sandboxes: true }),
        { visibility: "RESTRICTED", share_with: [{ grantee_type: "POD_MEMBER", grantee_id: "m1" }], allow_sandbox_use: true });
});

test("using an account for everyone sends one slug and purpose, and leaves the checks to the server", () => {
    assert.deepEqual(shareAsBody("team-gmail", "  The team inbox "), { slug: "team-gmail", purpose: "The team inbox" });
    assert.deepEqual(shareAsBody("team-gmail"), { slug: "team-gmail" }, "no purpose, none sent");
    assert.equal(suggestedSlug(ACCOUNTS[1], CONNECTORS), "gmail-shared");
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
    assert.equal(usageLine(usage[1], names), "A teammate you cannot open · your own calls");
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
        "A teammate you cannot open loses crm.",
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
    assert.deepEqual(impactLines(one, () => ""), ["1 teammate you cannot open loses a connector."]);
    const many = readImpact({ auth_config_id: "ac", account_count: 0, pod_account_count: 0,
        pod_connectors: [{ pod_id: "p1", slug: "crm", mode: "SHARED" }],
        pods: [{ pod_id: "p1", name: "Kit", pod_connectors: ["crm"] }],
        unnamed_pod_count: 2, unnamed_pod_connector_count: 3 });
    assert.deepEqual(impactLines(many, () => ""), ["Kit loses crm.", "2 teammates you cannot open lose 3 connectors."]);
    const unchecked = readImpact({ auth_config_id: "ac", account_count: 0, pod_account_count: 0, pod_connectors: [],
        unnamed_pod_count: 1, unnamed_pod_connector_count: 2, complete: false });
    assert.deepEqual(impactLines(unchecked, () => ""), ["1 more teammate loses 2 connectors.", "Couldn’t read everything this affects."],
        "an unfinished check says nothing about who can open what");
});

test("I may install as the server says, not as a copy of its role table says", () => {
    assert.deepEqual(readOrgActions({ organization_id: "o1", actions: ["org.read", INSTALLATION_MANAGE, 3] }), ["org.read", INSTALLATION_MANAGE]);
    assert.deepEqual(readOrgActions(null), [], "nothing readable holds nothing");
});

test("an owner can share with some of the pod's roles, not only some members", () => {
    const roles = readShareRoles({ items: [
        { id: "r-admin", name: "POD_ADMIN", is_system: true },
        { id: "r-user", name: "POD_USER", is_system: true },
        { id: "r-qa", name: "QA_REVIEWERS", is_system: false },
        { name: "NO_ID" },
    ] });
    assert.deepEqual(roles.map((one) => [one.id, one.label]), [["r-admin", "Admins"], ["r-user", "Users"], ["r-qa", "Qa reviewers"]]);
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
    assert.equal(legacyAddress(["pods", "p1", "connections"], new URLSearchParams({ connector: "gmail" })), "/t/p1/about/connections?connector=gmail");
    assert.equal(legacyAddress(["pods", "p1", "connections"], new URLSearchParams()), "/t/p1/about/connections");
    assert.equal(legacyAddress(["pod", "p1", "connectors"], new URLSearchParams()), "/t/p1/about/connections");
    assert.equal(connectionsPlace("p 1", "a b"), "/t/p%201/about/connections?connector=a%20b");
    const read = readAddress("/t/p1/about/connections");
    assert.equal(read.section, "connections");
    assert.equal(writeAddress(read), "/t/p1/about/connections");
    assert.equal(readAddress("/t/p1/about/connectors").section, "connections", "an old link to the part still opens it");
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
    const requests = await recorded(context, (path, method, body) => method === "DELETE"
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
        if (path.endsWith("/roles")) return { body: { items: [{ id: "r1", name: "POD_EDITOR", is_system: true }] } };
        return { body: { agent_id: "ag", agent_name: "researcher", grants: [{ resource_type: "connector", resource_name: "gmail", permission_ids: [CONNECTOR_USE] }] } };
    });
    assert.equal((await liveSource.accountUsage("acc"))[0].usedBy[0], "s");
    assert.equal((await liveSource.installationImpact("o1", "ac")).accountCount, 1);
    assert.ok(!(await liveSource.orgActions("o1")).includes(INSTALLATION_MANAGE), "a member may not change installations");
    const grants = await liveSource.setAgentGrants("p1", "researcher", withConnectorGrants([], ["gmail"]));
    assert.deepEqual(connectorGrantSlugs(grants), ["gmail"]);
    assert.equal((await liveSource.askMembersToConnect("p1", "gmail")).asked[0].name, "Bob");
    assert.equal((await liveSource.listShareRoles("p1"))[0].label, "Editors");
    assert.deepEqual(requests.map((one) => [one.method, one.path]), [
        ["GET", "/me/connectors/accounts/acc/pods"],
        ["GET", "/organizations/o1/installations/ac/impact"],
        ["GET", "/organizations/o1/permissions/me"],
        ["PUT", "/pods/p1/agents/researcher/permissions"],
        ["POST", "/pods/p1/connectors/gmail/ask-members"],
        ["GET", "/pods/p1/roles"],
    ]);
    assert.deepEqual(requests[3].body, { grants: [{ resource_type: "connector", resource_name: "gmail", permission_ids: [CONNECTOR_USE] }] });
}));

/* ── drawn ─────────────────────────────────────────────────────────── */

/* Every window and cache a drawing made, closed once all have run: an open
   one keeps the process alive past the last test. */
const opened: { close(): void }[] = [];
after(() => { for (const one of opened) one.close(); });

function dom() {
    const window = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { pretendToBeVisual: true, url: "https://app.example.invalid/t/p1/about/connections?connector=support-inbox" }).window;
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

function seeded(actions: string[], coverage = true) {
    const cache = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
    cache.setQueryData(["pod-connectors", "p1"], CONNECTORS);
    /* Each required per-member connector's coverage, as an editor's page
       asks for it: read, so no request leaves the test. */
    if (coverage) {
        cache.setQueryData(["pod-connectors", "p1", "coverage", "gmail"], readCoverage(GMAIL_COVERAGE));
        for (const slug of ["notion", "github"]) cache.setQueryData(["pod-connectors", "p1", "coverage", slug], readCoverage({ eligible: 2, connected: 2, waiting_on: [], truncated: slug === "github" }));
    }
    cache.setQueryData(["accounts", "o1"], MINE.map((one) => ({ ...one, ref: "", installState: "READY", createdAt: "" })));
    cache.setQueryData(["connectors"], [
        { id: "gmail", title: "Gmail", description: "", icon: "", active: true, kinds: [] },
        { id: "notion", title: "Notion", description: "", icon: "", active: true, kinds: [] },
    ]);
    cache.setQueryData(["installs", "o1"], [{ id: "ac-gmail", connector_id: "gmail", kind: "composio", name: "gmail", status: "ACTIVE" }]);
    cache.setQueryData(["pod-accounts", "p1"], ACCOUNTS);
    cache.setQueryData(["pod-actions", "p1"], { actions });
    cache.setQueryData(["share-roles", "p1"], [{ id: "r-editor", name: "POD_EDITOR", label: "Editors" }, { id: "r-qa", name: "QA_REVIEWERS", label: "Qa reviewers" }]);
    opened.push({ close: () => cache.clear() });
    return cache;
}

test("the Connections page draws each connector's badge and my standing, and editing only for editors", async (t) => {
    const window = dom();
    const { PodConnections } = await import("../src/connect/pod-connections.tsx");
    const { createRoot } = await import("react-dom/client");
    const draw = async (actions: string[], cache = seeded(actions)) => {
        const holder = window.document.createElement("div");
        window.document.body.append(holder);
        const root = createRoot(holder);
        // Unmounted however the test ends: a root left mounted after a failed
        // assertion kept the process alive, and the whole suite never exited.
        t.after(() => act(async () => root.unmount()));
        await act(async () => {
            root.render(createElement(QueryClientProvider, { client: cache }, createElement(PodConnections, { pod: POD, focus: "support-inbox" })));
        });
        return { holder, root };
    };
    const editor = await draw(["pod.read", "pod.connector.manage", "connector.use"]);
    const text = editor.holder.textContent ?? "";
    assert.match(text, /Shared · support@acme\.com · by Alice/);
    assert.match(text, /Your account/);
    assert.match(text, /Everyone connects/);
    assert.match(text, /Nobody has shared an account for it yet/);
    const buttons = [...editor.holder.querySelectorAll("button")].map((one) => one.textContent);
    assert.ok(buttons.includes("Connect"), "gmail: no account of mine");
    assert.ok(buttons.includes("Reconnect"), "notion: mine needs signing in");
    assert.ok(buttons.some((one) => one?.includes("Add a connector")));
    assert.ok(buttons.includes("Change"));
    assert.match(text, /5\/7 connected/);
    assert.match(text, /Waiting on Bob, and carol@acme\.com to reconnect/);
    assert.ok(buttons.includes("Ask members to connect"));
    assert.match(text, /Only some of the pod’s members were counted/, "github's count stopped short, and says so");
    assert.equal(editor.holder.querySelector("[data-focused] b")?.textContent ?? "", "support-inbox", "the linked connector is the one picked out");
    /* My accounts here: only mine, with the owner's acts. */
    assert.match(text, /Your accounts in Kit/);
    assert.match(text, /me@acme\.com/);
    assert.ok(buttons.includes("Share with everyone"));
    assert.ok(buttons.includes("Use for everyone as…"), "said in plain words, not \"Share as\"");
    assert.ok(!buttons.some((one) => one?.startsWith("Share as")));
    // The slug, and its app beside it only when the slug does not say it.
    const names = [...editor.holder.querySelectorAll(".podconn__name")].map((one) => one.textContent ?? "");
    assert.ok(names.some((one) => one.startsWith("support-inbox(Gmail)")), "support-inbox (Gmail)");
    assert.ok(names.some((one) => one.startsWith("gmailYour account")), "gmail, not gmail (Gmail)");
    assert.ok(!text.includes("acc-bob"));
    // Only a shared account can be lent to a sandbox (the server refuses it
    // on one only its owner uses), so a personal account offers no switch.
    assert.doesNotMatch(text, /Allow use inside sandboxes/, "my account here is personal");
    const some = [...editor.holder.querySelectorAll("button")].find((one) => one.textContent === "Share with some people");
    await act(async () => { some?.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); });
    const picker = editor.holder.querySelector(".podconn__people")?.textContent ?? "";
    assert.match(picker, /Alice/, "members");
    assert.match(picker, /Editors/, "and the pod's roles");
    assert.match(picker, /Qa reviewers/);

    /* Adding one: no slug is suggested until an installation is picked. */
    const add = [...editor.holder.querySelectorAll("button")].find((one) => one.textContent?.includes("Add a connector"));
    await act(async () => { add?.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); });
    const form = window.document.querySelector(".podconn__form")!;
    const [install] = [...form.querySelectorAll("select")];
    const slugInput = () => form.querySelector<HTMLInputElement>("input:not([type])")!;
    assert.equal(install.value, "", "nothing picked for them");
    assert.equal(slugInput().value, "");
    assert.equal(form.querySelector<HTMLButtonElement>("button[type=submit]")?.disabled, true);
    await act(async () => {
        install.value = "ac-gmail";
        install.dispatchEvent(new window.Event("change", { bubbles: true }));
    });
    assert.equal(slugInput().value, "gmail-2", "named for its app once there is one");

    // A member is never shown coverage, and never asks for it: it refuses them.
    const memberCache = seeded(["pod.read", "connector.use"], false);
    const member = await draw(["pod.read", "connector.use"], memberCache);
    const asked = memberCache.getQueryCache().findAll({ queryKey: ["pod-connectors", "p1", "coverage"] });
    assert.ok(asked.every((one) => one.state.fetchStatus === "idle" && one.state.data === undefined), "no coverage request for a member");
    assert.doesNotMatch(member.holder.textContent ?? "", /connected ·|Waiting on|Counting who/);
    const plain = [...member.holder.querySelectorAll("button")].map((one) => one.textContent);
    assert.ok(!plain.some((one) => one?.includes("Add a connector")), "a member cannot add one");
    assert.ok(!plain.includes("Change"));
    assert.ok(!plain.includes("Ask members to connect"));
    assert.ok(!plain.includes("Use for everyone as…"), "declaring a connector is an editor's");
    assert.ok(plain.includes("Connect"), "but still connects their own");

    const viewer = await draw(["pod.read"]);
    const seen = [...viewer.holder.querySelectorAll("button")].map((one) => one.textContent);
    assert.ok(!seen.includes("Connect") && !seen.includes("Reconnect"), "a viewer is offered no account nothing would use");
    assert.match(viewer.holder.textContent ?? "", /Not one you can use in Kit/);
});

test("a shared account of mine offers the sandbox switch, saying plainly who gets its token", async (t) => {
    const window = dom();
    const { PodConnections } = await import("../src/connect/pod-connections.tsx");
    const { createRoot } = await import("react-dom/client");
    const cache = seeded(["pod.read", "connector.use"]);
    cache.setQueryData(["pod-accounts", "p1"], ACCOUNTS.map((one) => (one.id === "pa-mine" ? { ...one, visibility: "POD" as const } : one)));
    const holder = window.document.createElement("div");
    window.document.body.append(holder);
    const root = createRoot(holder);
    t.after(() => act(async () => root.unmount()));
    await act(async () => { root.render(createElement(QueryClientProvider, { client: cache }, createElement(PodConnections, { pod: POD, focus: null }))); });
    const text = holder.textContent ?? "";
    assert.match(text, /Allow use inside sandboxes/);
    assert.match(text, /Agents of other members can then use a copy of this account’s token inside their sandboxes/);
    assert.match(text, /Gmail · gmail/, "my account row names its installation");
});

test("turning sandbox use off draws the account as saved, and the server's notice", async (t) => {
    const window = dom();
    const { PodConnections } = await import("../src/connect/pod-connections.tsx");
    const { createRoot } = await import("react-dom/client");
    const notice = "No sandbox is given this account's token from now on. A sandbox that already has a copy loses it on its next git command.";
    /* The source's own PATCH is pinned above, against the recorded API; here
       the page is handed its answer (the SDK sends by XHR under a window). */
    const sent: unknown[] = [];
    t.mock.method(liveSource, "changePodAccount", async (_podId: string, podAccountId: string, patch: { sandboxes?: boolean }) => {
        sent.push([podAccountId, patch]);
        return readPodAccounts({ items: [{ ...ACCOUNTS_RAW_MINE, visibility: "POD", allow_sandbox_use: patch.sandboxes, notice }] })[0];
    });
    const cache = seeded(["pod.read", "connector.use"]);
    cache.setQueryData(["pod-accounts", "p1"], ACCOUNTS.map((one) => (one.id === "pa-mine" ? { ...one, visibility: "POD" as const, sandboxes: true } : one)));
    const holder = window.document.createElement("div");
    window.document.body.append(holder);
    const root = createRoot(holder);
    t.after(() => act(async () => root.unmount()));
    await act(async () => { root.render(createElement(QueryClientProvider, { client: cache }, createElement(PodConnections, { pod: POD, focus: null }))); });
    const toggle = holder.querySelector<HTMLInputElement>("input[role=switch]")!;
    assert.equal(toggle.checked, true);
    await act(async () => {
        toggle.click();
        await new Promise((done) => setTimeout(done, 20));
    });
    assert.deepEqual(sent, [["pa-mine", { sandboxes: false }]]);
    assert.equal(holder.querySelector<HTMLInputElement>("input[role=switch]")?.checked, false, "the answer is drawn, not the old row");
    assert.match(holder.textContent ?? "", /loses it on its next git command/);
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
