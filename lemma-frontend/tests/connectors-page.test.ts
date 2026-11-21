import { test } from "node:test";
import assert from "node:assert/strict";
import {
    connectAgainst, mayConnect, needsOf, notYetConnected, servesThrough, servicesOf, statusOf,
} from "../src/connect/services.ts";
import type { Connector, ConnectorAccount } from "../src/data/accounts.ts";
import type { Install } from "../src/connect/install.ts";
import type { PodConnector } from "../src/data/pod-connectors.ts";

/** The Connectors page's rows: one per service somebody connected, one per
 *  server, database or API the organization added, and what each needs. */

const GMAIL: Connector = {
    id: "gmail", title: "Gmail", description: "Reads, drafts and sends mail", icon: "", active: true,
    kinds: [{ kind: "composio", auth_scheme: "OAUTH2" }],
};
const MCP: Connector = {
    id: "mcp", title: "MCP", description: "", icon: "", active: true,
    kinds: [{ kind: "mcp", auth_scheme: "API_KEY", config_schema: { properties: { server_url: { type: "string" } } } }],
};
const NOTION: Connector = {
    id: "notion", title: "Notion", description: "", icon: "", active: true,
    kinds: [{ kind: "composio", auth_scheme: "OAUTH2" }],
};

function account(over: Partial<ConnectorAccount>): ConnectorAccount {
    return {
        id: "a1", connectorId: "gmail", authConfigId: "ac-gmail", label: "me@example.com", ref: "", status: "CONNECTED",
        installState: "READY", usable: true, isDefault: true, createdAt: "", ...over,
    };
}

const GMAIL_INSTALL: Install = { id: "ac-gmail", connector_id: "gmail", kind: "composio", name: "gmail", status: "ACTIVE", is_default: true };
const SERVER_A: Install = { id: "ac-a", connector_id: "mcp", kind: "mcp", name: "Docs server", status: "ACTIVE", auth_scheme: "API_KEY", config: { server_url: "https://docs.example.com/mcp" } };
const SERVER_B: Install = { id: "ac-b", connector_id: "mcp", kind: "mcp", name: "Search", status: "ACTIVE", auth_scheme: "OAUTH2" };

test("a service is one row however many accounts it has, and each added server is its own", () => {
    const rows = servicesOf([GMAIL, MCP, NOTION], [account({}), account({ id: "a2", label: "sales@example.com", isDefault: false })], [GMAIL_INSTALL, SERVER_A, SERVER_B]);
    assert.deepEqual(rows.map((one) => one.title), ["Docs server", "Gmail", "Search"]);
    assert.equal(rows.find((one) => one.title === "Gmail")?.accounts.length, 2);
    assert.equal(rows.find((one) => one.title === "Search")?.kind, "mcp", "an added server is marked with its kind");
    assert.deepEqual(notYetConnected([GMAIL, MCP, NOTION], rows).map((one) => one.id), ["notion"],
        "the catalogue entry for every MCP server is under Your own, not offered as one more card");
});

test("each kind of trouble has its own fix, and waiting on somebody has none", () => {
    const needs = needsOf(GMAIL, null, [
        account({ id: "out", status: "REAUTH_REQUIRED", usable: false }),
        account({ id: "gh", installState: "INSTALL_REQUIRED", usable: false }),
        account({ id: "owner", installState: "PENDING_APPROVAL", usable: false }),
    ], [GMAIL_INSTALL]);
    assert.deepEqual(needs.map((one) => one.kind), ["reconnect", "install", "waiting"]);

    const typed = needsOf(MCP, SERVER_A, [account({ connectorId: "mcp", authConfigId: "ac-a", status: "REAUTH_REQUIRED", usable: false })], [SERVER_A]);
    assert.deepEqual(typed.map((one) => one.kind), ["replace"], "a pasted credential is replaced, not signed into");

    const unsigned = needsOf(MCP, SERVER_B, [], [SERVER_B]);
    assert.deepEqual(unsigned.map((one) => one.kind), ["sign-in"], "a server that signs in with OAuth waits for somebody to");
    assert.deepEqual(statusOf({ ...servicesOf([MCP], [], [SERVER_B])[0] }), { word: "Not signed in", tone: "warn" });
});

test("a Lemma's connector runs through the service by its install, or by its app when the install is not readable", () => {
    const [server, gmail] = servicesOf([GMAIL, MCP], [account({})], [GMAIL_INSTALL, SERVER_A]);
    const declared = (over: Partial<PodConnector>): PodConnector => ({
        id: "c", slug: "gmail", connectorId: "", authConfigId: "", installationName: "", mode: "PER_MEMBER", status: "READY",
        purpose: "", required: false, sharedAccount: "", sharedPodAccountId: "", sharedOwnerId: "", visibility: "POD", canUse: true, ...over,
    });
    assert.equal(servesThrough(gmail, declared({ authConfigId: "ac-gmail" })), true);
    assert.equal(servesThrough(gmail, declared({ connectorId: "gmail" })), true);
    assert.equal(servesThrough(server, declared({ connectorId: "mcp", authConfigId: "ac-other" })), false,
        "another MCP server is not this one");
});

test("a member connects on an install that exists, and only an editor makes one", () => {
    assert.equal(connectAgainst([{ ...GMAIL_INSTALL, is_default: false }, { ...GMAIL_INSTALL, id: "own", is_default: true }])?.id, "own");
    assert.equal(mayConnect([], false), false);
    assert.equal(mayConnect([GMAIL_INSTALL], false), true);
    assert.equal(mayConnect([], null), true, "unknown is left to the server");
});
