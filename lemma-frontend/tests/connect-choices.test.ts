import test from "node:test";
import assert from "node:assert/strict";
import { addChoices, focusTarget } from "../src/connect/choices.ts";
import { consentLabel, consentNote, isComposioUrl } from "../src/connect/consent.ts";
import { connectorCreateBody, type PodConnector } from "../src/data/pod-connectors.ts";
import type { Connector } from "../src/data/accounts.ts";
import type { Install } from "../src/connect/install.ts";

const composio = { kind: "composio", auth_scheme: "OAUTH2" as const, system_default_available: true };
const catalogEntry = (id: string, title: string, kinds: Connector["kinds"] = [composio]): Connector =>
    ({ id, title, description: "", icon: "", active: true, kinds });
const CATALOG: Connector[] = [
    catalogEntry("gmail", "Gmail"),
    catalogEntry("slack", "Slack"),
    catalogEntry("airtable", "Airtable"),
    /* Needs the organization's own OAuth app: nothing Lemma can install alone. */
    catalogEntry("salesforce", "Salesforce", [{ kind: "http", auth_scheme: "OAUTH2", system_default_available: false }]),
    /* One entry for every MCP server: pointed somewhere, never picked. */
    catalogEntry("mcp", "MCP server", [{
        kind: "mcp", auth_scheme: "API_KEY", system_default_available: true,
        config_schema: { type: "object", required: ["server_url"], properties: { server_url: { type: "string" } } },
    }]),
    { ...catalogEntry("retired", "Retired"), active: false },
];
const install = (id: string, connectorId: string, name = connectorId, extra: Partial<Install> = {}): Install =>
    ({ id, connector_id: connectorId, kind: "composio", name, status: "ACTIVE", is_default: true, ...extra });
const titleOf = (id: string) => CATALOG.find((one) => one.id === id)?.title ?? id;
const podConnector = (slug: string, connectorId: string, authConfigId: string, installationName = connectorId): PodConnector => ({
    id: "pc-" + slug, slug, connectorId, authConfigId, installationName, mode: "PER_MEMBER", status: "READY", purpose: "", required: false,
    sharedAccount: "", sharedPodAccountId: "", sharedOwnerId: "", visibility: "POD", canUse: true,
});

test("a new organization can still add a connector: the catalog's sign-in-ready apps are choices", () => {
    const choices = addChoices([], CATALOG, titleOf);
    assert.deepEqual(choices.map((one) => one.key), ["catalog:airtable", "catalog:gmail", "catalog:slack"],
        "sorted by title; never one that needs an app, an address, or is retired");
    assert.deepEqual(choices[1], { key: "catalog:gmail", from: "catalog", installId: "", connectorId: "gmail", label: "Gmail" });
});

test("installations come first, and an installed app is not offered again from the catalog", () => {
    const choices = addChoices([install("ac-1", "gmail", "support-mail"), install("ac-2", "slack")], CATALOG, titleOf);
    assert.deepEqual(choices.map((one) => one.key), ["install:ac-1", "install:ac-2", "catalog:airtable"]);
    assert.equal(choices[0].label, "Gmail — support-mail", "an install named apart from its app says both");
    assert.equal(choices[1].label, "Slack");
    assert.equal(choices[0].installId, "ac-1");
});

test("adding by catalog id sends connector_id and lets the server name it", () => {
    assert.deepEqual(
        connectorCreateBody({ slug: "", authConfigId: "", connectorId: "gmail", mode: "PER_MEMBER", sharedPodAccountId: "", required: false, purpose: "" }),
        { connector_id: "gmail", mode: "PER_MEMBER" },
    );
    assert.deepEqual(
        connectorCreateBody({ slug: "inbox", authConfigId: "ac-1", connectorId: "gmail", mode: "PER_MEMBER", sharedPodAccountId: "", required: false, purpose: "" }),
        { slug: "inbox", auth_config_id: "ac-1", mode: "PER_MEMBER" },
        "an installation is exact, and wins",
    );
});

test("a link's ?connector= finds the pod connector by slug, installation or app", () => {
    const connectors = [podConnector("support-inbox", "gmail", "ac-1", "support-mail")];
    const installs = [install("ac-1", "gmail", "support-mail"), install("ac-9", "notion", "team-notion")];
    assert.deepEqual(focusTarget("support-inbox", connectors, installs, CATALOG), { kind: "connector", slug: "support-inbox" });
    assert.deepEqual(focusTarget("support-mail", connectors, installs, CATALOG), { kind: "connector", slug: "support-inbox" }, "by installation name");
    assert.deepEqual(focusTarget("gmail", connectors, installs, CATALOG), { kind: "connector", slug: "support-inbox" }, "by app");
});

test("a link naming an app or installation the pod has no connector for offers to connect it", () => {
    const installs = [install("ac-9", "notion", "team-notion")];
    assert.deepEqual(focusTarget("gmail", [], [], CATALOG), { kind: "connect", connectorId: "gmail", installId: "", title: "Gmail" });
    assert.deepEqual(focusTarget("team-notion", [], installs, CATALOG), { kind: "connect", connectorId: "notion", installId: "ac-9", title: "notion" });
    assert.deepEqual(focusTarget("nothing-like-it", [], installs, CATALOG), { kind: "unknown" });
    assert.deepEqual(focusTarget(null, [], installs, CATALOG), { kind: "none" });
    assert.deepEqual(focusTarget("  ", [], installs, CATALOG), { kind: "none" });
});

test("a Composio consent page is named before the person lands on it", () => {
    assert.equal(isComposioUrl("https://backend.composio.dev/api/v3/s/abc"), true);
    assert.equal(isComposioUrl("https://composio.dev/x"), true);
    assert.equal(isComposioUrl("https://notcomposio.dev/x"), false, "a lookalike domain is not Composio");
    assert.equal(isComposioUrl("https://composio.dev.evil.example/x"), false);
    assert.equal(isComposioUrl("not a url"), false);
    assert.equal(consentLabel("https://backend.composio.dev/api/v3/s/abc", "Gmail"), "Continue to Composio to authorize Gmail");
    assert.equal(consentLabel("https://accounts.google.com/o/oauth2/auth?x=1", "Gmail"), "Continue to sign in to Gmail");
    assert.equal(consentLabel("https://github.com/login/oauth/authorize", ""), "Continue to sign in to the app");
    assert.match(consentNote("https://backend.composio.dev/x", "Gmail"), /through Composio/);
    assert.equal(consentNote("https://github.com/login/oauth/authorize", "GitHub"), "");
});
