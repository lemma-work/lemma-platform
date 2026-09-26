import test from "node:test";
import assert from "node:assert/strict";
import { REDACTED, blank, fields, isSecretName, payload, problems, unchangedSecret } from "../src/connect/schema.ts";
import {
    canBringOwnApp, canInstallWithDefaults, connectRoute, connectSchema, discoveryNote, freshInstallName,
    connectorProblem, installSchema, isBringYourOwn, isTenantConfigured, kindFor, kindNamed, needsOwnApp, oauthAppMissing, primaryKind, urlRefusal,
    type CatalogEntry, type ConnectorKind,
} from "../src/connect/install.ts";

const mcpSchema = {
    type: "object",
    required: ["server_url"],
    properties: {
        server_url: { type: "string", description: "Where the MCP server is.", examples: ["https://mcp.example.com/sse"] },
        auth_token: { type: "string", format: "password" },
        extra_headers: { type: "object", additionalProperties: { type: "string" } },
        verify_tls: { type: "boolean", default: true },
        transport: { type: "string", enum: ["sse", "streamable_http"] },
    },
};

test("a schema becomes the form it describes, in the order it describes it", () => {
    const list = fields(mcpSchema);

    assert.deepEqual(list.map((one) => one.name), ["server_url", "auth_token", "extra_headers", "verify_tls", "transport"]);
    assert.deepEqual(list.map((one) => one.kind), ["text", "secret", "headers", "boolean", "choice"]);
    assert.equal(list[0].required, true);
    assert.equal(list[1].required, false);
    assert.equal(list[0].placeholder, "https://mcp.example.com/sse");
    assert.deepEqual(list[4].options, ["sse", "streamable_http"]);
    // A name with no title becomes words, not a snake_case identifier.
    assert.equal(list[0].label, "Server url");
});

test("a schema with nothing in it is a form with no fields, not a crash", () => {
    assert.deepEqual(fields(null), []);
    assert.deepEqual(fields({}), []);
    assert.deepEqual(fields({ type: "object" }), []);
    assert.deepEqual(fields("nonsense"), []);
});

test("a location is not a credential", () => {
    // Mirrors the API's own rule. Without it every `authorization_endpoint`
    // and `token_endpoint` — which an operator needs to read, and which show
    // up in the authorize URL anyway — would be drawn as a password box.
    assert.equal(isSecretName("client_secret"), true);
    assert.equal(isSecretName("api_key"), true);
    assert.equal(isSecretName("auth_token"), true);
    assert.equal(isSecretName("authorization_endpoint"), false);
    assert.equal(isSecretName("token_endpoint"), false);
    assert.equal(isSecretName("token_uri"), false);
    assert.equal(isSecretName("server_url"), false);
});

test("a masked secret is a field nobody edited", () => {
    // The whole reason this file exists. Reads come back masked, so a form
    // that renders what it fetched and submits what it rendered writes
    // `********` into the credential — and the only sign is that the
    // connector stops working later, against a value nobody can read back.
    const list = fields(mcpSchema);
    const held = blank(list, { server_url: "https://mcp.example.com/sse", auth_token: REDACTED });

    assert.equal(unchangedSecret(held.auth_token), true);
    const sent = payload(list, held);
    assert.equal("auth_token" in sent, false);
    assert.equal(sent.server_url, "https://mcp.example.com/sse");
});

test("a secret actually typed over is sent", () => {
    const list = fields(mcpSchema);
    const held = blank(list, { server_url: "https://mcp.example.com/sse", auth_token: "sk-live-new" });

    assert.equal(payload(list, held).auth_token, "sk-live-new");
});

test("an optional field left blank is not a field set to empty", () => {
    // Writing "" is a different configuration from not having one — for a
    // spec URL or a header it is the difference between unset and cleared.
    const sent = payload(fields(mcpSchema), blank(fields(mcpSchema), { server_url: "https://x.test/sse" }));

    assert.deepEqual(Object.keys(sent).sort(), ["server_url", "verify_tls"]);
    // A boolean with a schema default keeps it rather than becoming absent.
    assert.equal(sent.verify_tls, true);
});

test("header maps keep the keys the tenant chose, and drop masked values", () => {
    const list = fields(mcpSchema);
    const sent = payload(list, blank(list, {
        server_url: "https://x.test/sse",
        extra_headers: { "X-Signature-Key": "abc", " X-Trim ": "yes", "X-Old": REDACTED, "": "dropped" },
    }));

    assert.deepEqual(sent.extra_headers, { "X-Signature-Key": "abc", "X-Trim": "yes" });
});

test("only what is required is checked here", () => {
    const list = fields(mcpSchema);

    assert.deepEqual(Object.keys(problems(list, blank(list))), ["server_url"]);
    assert.deepEqual(problems(list, blank(list, { server_url: "https://x.test" })), {});
    // Whitespace is not an answer.
    assert.deepEqual(Object.keys(problems(list, blank(list, { server_url: "   " }))), ["server_url"]);
});

/* ── which form, and which flow ─────────────────────────────────────── */

const composioManaged: ConnectorKind = {
    kind: "composio", auth_scheme: "OAUTH2", system_default_available: true,
    supports_org_custom_oauth: true, config_schema: { type: "object", properties: { username: { type: "string" } } },
    install_config_schema: { type: "object", properties: { client_id: { type: "string" }, client_secret: { type: "string" } } },
};
const mcpKind: ConnectorKind = { kind: "mcp", auth_scheme: "API_KEY", config_schema: mcpSchema, discovery: "mcp" };

test("two schemas wear one name, and the wrong one empties the dialog", () => {
    // On a Composio toolkit `config_schema` is the *end user's* credential
    // form and `install_config_schema` is the *organization's* OAuth app.
    // They are two forms for two different people.
    assert.deepEqual(installSchema(composioManaged), composioManaged.install_config_schema);
    assert.deepEqual(connectSchema(composioManaged), composioManaged.config_schema);
    // On every other kind, `config_schema` is the install and there is no
    // second form at all: the credential arrives with the install.
    assert.deepEqual(installSchema(mcpKind), mcpSchema);
    assert.equal(connectSchema(mcpKind), null);
    assert.equal(installSchema(null), null);
});

test("the install decides how to connect, not its catalogue entry", () => {
    // `mcp` is one entry standing for every server a tenant may point at. The
    // entry says API_KEY; an install whose server described its own
    // authorization answers OAUTH2, and that is the one to believe.
    assert.equal(connectRoute(null, mcpKind), "credentials");
    assert.equal(connectRoute({ id: "a", connector_id: "mcp", kind: "mcp", name: "Sentry", auth_scheme: "OAUTH2" }, mcpKind), "redirect");
    assert.equal(connectRoute({ id: "b", connector_id: "gmail", kind: "composio", name: "Gmail", auth_scheme: "API_KEY" }, composioManaged), "credentials");
    // Nothing said anywhere: the browser round trip is the older default.
    assert.equal(connectRoute(null, null), "redirect");
    // No credential is still a credential post. The backend refuses a connect
    // request for anything but OAuth, so a redirect here could never finish.
    assert.equal(connectRoute(null, { kind: "http", auth_scheme: "NOAUTH" }), "credentials");
});

test("a toolkit Composio holds no credentials for needs the org's own app first", () => {
    // This was pinned true once, which is how an unconnectable toolkit came to
    // advertise a Connect button.
    const unmanaged: ConnectorKind = { ...composioManaged, system_default_available: false };

    assert.equal(needsOwnApp(unmanaged), true);
    assert.equal(needsOwnApp(composioManaged), false);
    assert.equal(needsOwnApp(mcpKind), false);
    // Only where the org's app is the way in. A managed toolkit runs on
    // Lemma's Composio account, and the backend refuses an org-supplied one.
    assert.equal(canBringOwnApp(unmanaged), true);
    assert.equal(canBringOwnApp(composioManaged), false);
    assert.equal(canBringOwnApp(mcpKind), false);
    // A native OAuth app needs endpoints from the catalogue as well as the flag.
    const native: ConnectorKind = { kind: "http", auth_scheme: "OAUTH2", supports_org_custom_oauth: true };
    assert.equal(canBringOwnApp(native), false);
    assert.equal(canBringOwnApp({ ...native, oauth2_defaults: { authorization_endpoint: "https://x" } }), true);
});

test("what an organization points at itself is told apart by kind, not by id", () => {
    const gmail: CatalogEntry = { id: "gmail", title: "Gmail", kinds: [composioManaged] };
    const mcp: CatalogEntry = { id: "mcp", title: "MCP server", kinds: [mcpKind] };

    assert.equal(isBringYourOwn(gmail), false);
    assert.equal(isBringYourOwn(mcp), true);
    assert.equal(isBringYourOwn({ id: "x", title: "X" }), false);
});

test("http is not always an address somebody supplies", () => {
    // GitHub is `http` with Lemma's own OAuth client; WhatsApp is `http` with a
    // bot token and an empty install schema. "Not Composio" filed both as a
    // server to add, and the add form asked each for an address it has not got.
    const github: ConnectorKind = { kind: "http", auth_scheme: "OAUTH2", system_default_available: true, config_schema: { type: "object", properties: { client_id: { type: "string" } } } };
    const whatsapp: ConnectorKind = { kind: "http", auth_scheme: "API_KEY", system_default_available: true, config_schema: { type: "object", properties: {} } };
    const openapi: ConnectorKind = { kind: "http", auth_scheme: "API_KEY", config_schema: { type: "object", properties: { spec_url: { type: "string" } } } };

    assert.equal(isTenantConfigured(github), false);
    assert.equal(isTenantConfigured(whatsapp), false);
    assert.equal(isTenantConfigured(openapi), true);
    assert.equal(isBringYourOwn({ id: "github", title: "GitHub", kinds: [github] }), false);
});

test("an install is made for whoever connects first, where Lemma can make one", () => {
    // The backend never creates an install on a connect request — it only
    // looks one up — so the client has to know when it may create Lemma's own.
    const unmanaged: ConnectorKind = { ...composioManaged, system_default_available: false };
    const bot: ConnectorKind = { kind: "http", auth_scheme: "API_KEY", system_default_available: true, config_schema: { type: "object", properties: {} } };
    const database: ConnectorKind = { kind: "sql", auth_scheme: "API_KEY", system_default_available: true, config_schema: { type: "object", required: ["host"], properties: { host: { type: "string" } } } };

    assert.equal(canInstallWithDefaults(composioManaged), true);
    assert.equal(canInstallWithDefaults(bot), true);
    assert.equal(canInstallWithDefaults(unmanaged), false);
    // "System default available" is set from "not OAuth" and says nothing
    // about a database that still needs a host.
    assert.equal(canInstallWithDefaults(database), false);
    assert.equal(canInstallWithDefaults(null), false);
});

test("with no install yet, the kind is Composio where it is offered", () => {
    const two: CatalogEntry = { id: "both", title: "Both", kinds: [mcpKind, composioManaged] };

    assert.equal(primaryKind(two)?.kind, "composio");
    assert.equal(kindFor(two, null)?.kind, "composio");
    // An install names its own kind, and that is the one it runs under.
    assert.equal(kindFor(two, { id: "i", connector_id: "both", kind: "mcp", name: "x" })?.kind, "mcp");
});

test("a second install of a connector is not given the first one's name", () => {
    // An unnamed install is named after its connector, and names are unique
    // per organization — so the org's own app beside Lemma's was refused.
    assert.equal(freshInstallName("gmail", []), "gmail");
    assert.equal(freshInstallName("gmail", ["gmail"]), "gmail-2");
    assert.equal(freshInstallName("gmail", ["gmail", "gmail-2"]), "gmail-3");
});

test("a kind is only chosen when the choice is unambiguous", () => {
    const one: CatalogEntry = { id: "mcp", title: "MCP", kinds: [mcpKind] };
    const two: CatalogEntry = { id: "both", title: "Both", kinds: [mcpKind, composioManaged] };

    assert.equal(kindNamed(one, null)?.kind, "mcp");
    assert.equal(kindNamed(two, null), null, "a client picking the first of several installs something nobody asked for");
    assert.equal(kindNamed(two, "composio")?.kind, "composio");
    assert.equal(kindNamed(two, "sql"), null);
});

test("zero operations means three different things", () => {
    // A connector with nothing to advertise, a kind whose operations are
    // fixed, and a server that refused the listing all report zero, and they
    // need different things from the reader.
    assert.match(discoveryNote("ok", 12), /12 operations/);
    assert.match(discoveryNote("ok", 1), /1 operation\./);
    assert.match(discoveryNote("ok", 0), /advertised no operations/);
    assert.match(discoveryNote("not_applicable", 0), /fixed/);
    assert.match(discoveryNote("failed", 0), /refused/);
    assert.match(discoveryNote("failed", 0, "502 from upstream"), /502 from upstream/);
});

test("a refused address says which rule it broke", () => {
    assert.match(urlRefusal("Unsafe URL: host resolves to a private address") ?? "", /reachable from the internet/);
    assert.equal(urlRefusal("Something else entirely went wrong"), null);
});

/* ── coming back from the provider ──────────────────────────────────── */

test("the provider's tab comes back to the completion page, carrying where it started", async () => {
    const { completionPath, outcomeNote } = await import("../src/connect/round-trip.ts");
    // A rooted path: the API refuses a `return_to` with a host in it, and
    // without one the callback lands on the app root in the provider's tab.
    const path = completionPath("/t?settings=connectors");
    assert.equal(path, "/oauth/complete?from=%2Ft%3Fsettings%3Dconnectors");
    assert.equal(new URLSearchParams(path.split("?")[1]).get("from"), "/t?settings=connectors");

    // Only `error` is a failure; an install still to do is a working credential.
    assert.equal(outcomeNote({ connect: "connected", connector: "gmail", account: "a", reason: null }).bad, false);
    assert.equal(outcomeNote({ connect: "install_required", connector: "github", account: "a", reason: null }).bad, false);
    const failed = outcomeNote({ connect: "error", connector: "gmail", account: null, reason: "access_denied" });
    assert.equal(failed.bad, true);
    assert.equal(failed.text, "access_denied");
});

test("the role refusal is said as the role it is", () => {
    // Making an install needs an owner or editor; the backend tells anyone
    // else "no connectors in organization <uuid>", which nobody can act on.
    const refused = Object.assign(new Error("No connectors are available in organization '019d'. You may not be a member of it."), { code: "ORGANIZATION_CONNECTORS_NOT_FOUND" });
    assert.match(connectorProblem(refused, "x"), /owner or editor/);
    assert.match(connectorProblem(new Error("No connectors are available in organization 'x'."), "x"), /owner or editor/);
    // Everything else passes through, and a blank failure gets the fallback.
    assert.equal(connectorProblem(new Error("Invalid bot token"), "x"), "Invalid bot token");
    assert.equal(connectorProblem(null, "Could not connect."), "Could not connect.");
});

test("a missing OAuth app is recognised from the backend's words", () => {
    assert.equal(oauthAppMissing("GitHub needs an OAuth app before anyone can sign in to it."), true);
    assert.equal(oauthAppMissing("Invalid bot token"), false);
    assert.equal(oauthAppMissing(null), false);
});
