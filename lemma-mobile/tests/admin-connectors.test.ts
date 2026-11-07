import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
    accountHint, bringYourOwn, bringYourOwnWords, connectorsLine, dayWords, emptyWords, holdings, iconAddress, installWhere,
    installsLabel, installsShown, metaWords, poolOf, rowHint, searchConnectors, shownSlice, sliceCatalog, statusWords,
} from "../src/areas/admin/connectors/model";
import { accountActs, connectTarget, installActs, nameProblem, planConnect, reauthWords, rotateFields } from "../src/areas/admin/connectors/plan";
import { redirectUriOf } from "../src/areas/admin/connectors/redirect";
import { connectorsRoutes } from "../src/areas/admin/connectors/routes";
import { PHONE_RETURN, readSignIn, signInWords, snapshot } from "../src/areas/admin/connectors/sign-in";
import type { ConnectorsSource } from "../src/areas/admin/connectors/source";
import { readAccount, type Connector, type ConnectorAccount } from "@web/data/accounts";
import type { CatalogEntry, Install } from "@web/connect/install";

/** The organization's connectors (PARITY.md, F): what the catalog says
 *  about each connector, what each connect screen offers, how a sign-in is
 *  read back, and every flow run against the sample. */

/* The sample waits as a server would; the clock is run by hand here. */
async function run<T>(promise: Promise<T>): Promise<T> {
    let settled = false;
    const outcome = promise.then(
        (value) => ({ ok: true as const, value }),
        (error: unknown) => ({ ok: false as const, error }),
    );
    void outcome.then(() => { settled = true; });
    while (!settled) await vi.advanceTimersByTimeAsync(250);
    const got = await outcome;
    if (!got.ok) throw got.error;
    return got.value;
}

async function freshSample(): Promise<ConnectorsSource> {
    vi.resetModules();
    const { connectorsSample } = await import("../src/areas/admin/connectors/sample");
    return connectorsSample();
}

const account = (raw: Record<string, unknown>): ConnectorAccount => readAccount({ status: "CONNECTED", install_state: "READY", created_at: "2026-08-14T10:00:00Z", ...raw })!;

const MANAGER = { may: true, known: true };
const MEMBER = { may: false, known: true };
const UNKNOWN = { may: false, known: false };

describe("the sample, read as the screens read it", () => {
    beforeEach(() => { vi.useFakeTimers(); });
    afterEach(() => { vi.useRealTimers(); });

    it("slices the catalog: connected first, the rest available, and what needs attention", async () => {
        const source = await freshSample();
        const [catalog, accounts, installs] = await run(Promise.all([source.catalog(), source.accounts("sample-acme"), source.installs("sample-acme")]));
        const held = holdings(accounts, installs);
        const sliced = sliceCatalog(catalog, accounts, held);
        const ids = (list: Connector[]) => list.map((one) => one.id).sort();
        /* An MCP server and a database count as connected by their install;
           Linear, enabled with nobody on it, does not. */
        expect(ids(sliced.connected)).toEqual(["github", "gmail", "mcp", "notion", "slack", "sql", "stripe", "telegram"]);
        expect(sliced.available.map((one) => one.id)).toContain("linear");
        expect(ids(sliced.ailing)).toEqual(["github", "notion"]);
        expect(sliced.trouble).toBe(3);
        expect(shownSlice(null, sliced)).toBe("connected");
        expect(poolOf("trouble", sliced)).toHaveLength(2);
        expect(metaWords(accounts)).toBe("10 accounts · 3 need attention");
        expect(connectorsLine(catalog, accounts)).toEqual({ text: "3 accounts need attention", needs: true });
    });

    it("says each row's state", async () => {
        const source = await freshSample();
        const [catalog, accounts, installs] = await run(Promise.all([source.catalog(), source.accounts("sample-acme"), source.installs("sample-acme")]));
        const held = holdings(accounts, installs);
        const row = (id: string, manage = MANAGER) => rowHint(catalog.find((one) => one.id === id)!, held, manage);
        expect(row("notion")).toEqual({ hint: "Needs signing in again", needs: true });
        expect(row("github")).toEqual({ hint: "2 accounts need attention", needs: true });
        expect(row("slack")).toEqual({ hint: "Connected", needs: false });
        expect(row("telegram")).toEqual({ hint: "Connected · Acme help desk", needs: false });
        expect(row("gmail")).toEqual({ hint: "2 accounts connected", needs: false });
        expect(row("mcp")).toEqual({ hint: "Nobody has signed in to Support tools yet", needs: true });
        expect(row("sql")).toEqual({ hint: "Orders (staging)", needs: false });
        expect(row("linear")).toEqual({ hint: "Set up, with no account yet", needs: false });
        expect(row("asana")).toEqual({ hint: "Tasks and projects.", needs: false });
        expect(row("asana", MEMBER)).toEqual({ hint: "An owner or editor has to set this up", needs: false });
        expect(row("asana", UNKNOWN).hint).toBe("Tasks and projects.");
    });

    it("offers adding the organization's own only where the catalog does", async () => {
        const source = await freshSample();
        const catalog = await run(source.catalog());
        const own = bringYourOwn(catalog);
        expect(own.kinds.map((one) => [one.kind, one.entry.id])).toEqual([["mcp", "mcp"], ["http", "openapi"], ["sql", "sql"]]);
        expect(own.ownApp).toBe(true);
        expect(bringYourOwnWords(own)).toBe("An MCP server, a REST API, a database or your own OAuth app");
        expect(bringYourOwnWords({ kinds: [], ownApp: false })).toBe("");
    });

    it("connects a credential, and undoes the install it made when the credential is refused", async () => {
        const source = await freshSample();
        const { submitConnect } = await import("../src/areas/admin/connectors/plan");
        const detail = await run(source.detail("whatsapp"));
        const plan = planConnect({ entry: detail.entry, title: "WhatsApp Business", against: null, bringingApp: false, manage: MANAGER });
        expect(plan.mode).toBe("credentials");

        const refused = submitConnect(source, "sample-acme", { plan, connectorId: "whatsapp", against: null, takenNames: [], values: { access_token: "wrong-sample", phone_number_id: "100" } });
        await expect(run(refused)).rejects.toThrow(/refused/);
        expect((await run(source.installs("sample-acme"))).some((one) => one.connector_id === "whatsapp")).toBe(false);

        const step = await run(submitConnect(source, "sample-acme", { plan, connectorId: "whatsapp", against: null, takenNames: [], values: { access_token: "sample-not-real", phone_number_id: "100" } }));
        expect(step.next).toBe("done");
        const accounts = await run(source.accounts("sample-acme"));
        expect(accounts.filter((one) => one.connectorId === "whatsapp")).toHaveLength(1);
    });

    it("brings the organization's own app first where Lemma holds none, then signs in through it", async () => {
        const source = await freshSample();
        const { submitConnect } = await import("../src/areas/admin/connectors/plan");
        const detail = await run(source.detail("hubspot"));
        const plan = planConnect({ entry: detail.entry, title: "HubSpot", against: null, bringingApp: false, manage: MANAGER });
        expect(plan.mode).toBe("app-form");
        expect(plan.title).toBe("Use your own HubSpot app");
        /* Composio runs its own round trip: its redirect URL is a field of
           the form, and Lemma's is not shown beside it. */
        expect(plan.showRedirectUri).toBe(false);
        expect(planConnect({ entry: detail.entry, title: "HubSpot", against: null, bringingApp: false, manage: MEMBER }).mode).toBe("needs-manager");

        const installs = await run(source.installs("sample-acme"));
        const step = await run(submitConnect(source, "sample-acme", {
            plan, connectorId: "hubspot", against: null, takenNames: installs.map((one) => one.name),
            values: { client_id: "sample-client", client_secret: "sample-secret", oauth_redirect_uri: "https://auth.example.invalid/cb" },
        }));
        expect(step.next).toBe("sign-in");
        if (step.next !== "sign-in") return;
        const result = await run(source.signIn("sample-acme", { connectorId: "hubspot", installId: step.installId }));
        expect(result.kind).toBe("connected");
        const made = (await run(source.installs("sample-acme"))).find((one) => one.id === step.installId)!;
        expect(made.config_source).toBe("ORG_CUSTOM");
        expect(made.config?.client_secret).toBe("********");
    });

    it("signs a signed-out account in again in place, and leaves a GitHub App to install", async () => {
        const source = await freshSample();
        const again = await run(source.signIn("sample-acme", { connectorId: "notion", installId: "ac-notion" }));
        expect(again).toEqual({ kind: "connected", accountId: "acc-notion" });

        const github = await run(source.signIn("sample-acme", { connectorId: "github", installId: null }));
        expect(github.kind).toBe("unfinished");
        if (github.kind !== "unfinished") return;
        expect(github.trouble).toBe("GitHub needs reconnecting: its triggers and actions reach nothing until it is installed");
        expect(await run(source.finishInstall("sample-acme", github.accountId))).toEqual({ kind: "installed" });

        const choose = await run(source.finishInstall("sample-acme", "acc-github-2"));
        expect(choose.kind).toBe("choices");
        if (choose.kind !== "choices") return;
        await run(source.bindInstallation("sample-acme", "acc-github-2", choose.choices[0].installationId));
        const after = (await run(source.accounts("sample-acme"))).find((one) => one.id === "acc-github-2")!;
        expect(after.usable).toBe(true);
    });

    it("adds a server and reads its operations, or signs in when it wants a browser", async () => {
        const source = await freshSample();
        const { addOwn } = await import("../src/areas/admin/connectors/plan");
        const entry = (await run(source.detail("mcp"))).entry;
        const plain = await run(addOwn(source, "sample-acme", { entry, kind: "mcp", name: "Wiki tools", config: { server_url: "https://wiki.acme.example/mcp" }, credentials: { bearer_token: "sample-token" } }));
        expect(plain).toMatchObject({ signIn: false, note: "Found 14 operations.", bad: false });

        const browser = await run(addOwn(source, "sample-acme", { entry, kind: "mcp", name: "Calendar tools", config: { server_url: "https://cal.acme.example/oauth/mcp" }, credentials: {} }));
        expect(browser).toMatchObject({ signIn: true, note: "Added. Sign in to finish connecting it." });

        const down = await run(addOwn(source, "sample-acme", { entry, kind: "mcp", name: "Flaky", config: { server_url: "https://down.acme.example/mcp" }, credentials: {} }));
        expect(down).toMatchObject({ bad: true, note: "The server refused to list its operations." });

        await expect(run(addOwn(source, "sample-acme", { entry, kind: "mcp", name: "Local", config: { server_url: "http://localhost:9000/mcp" }, credentials: {} }))).rejects.toThrow(/private or loopback/);
        await expect(run(addOwn(source, "sample-acme", { entry, kind: "mcp", name: "Wiki tools", config: { server_url: "https://x.acme.example/mcp" }, credentials: {} }))).rejects.toThrow(/already here/);
    });

    it("saves an install's changes, keeping masked secrets and asking moved accounts to sign in again", async () => {
        const source = await freshSample();
        const { saveInstall } = await import("../src/areas/admin/connectors/plan");
        const { fields, installSchema } = { fields: (await import("@web/connect/schema")).fields, installSchema: (await import("@web/connect/install")).installSchema };
        const entry = (await run(source.detail("mcp"))).entry;
        const install = (await run(source.installs("sample-acme"))).find((one) => one.id === "ac-mcp-docs")!;
        const list = fields(installSchema(entry.kinds![0]));
        const current = await run(source.install("sample-acme", install));

        const renamed = await run(saveInstall(source, "sample-acme", { install, name: "Docs", list, values: { ...current.config } }));
        expect(renamed).toEqual({ said: false });

        const moved = await run(saveInstall(source, "sample-acme", { install: { ...install, name: "Docs" }, name: "Docs", list, values: { ...current.config, server_url: "https://mcp.acme.example/v2/mcp" } }));
        expect(moved).toEqual({ said: true, note: "Found 14 operations.", bad: false, reauth: 1 });
        const held = (await run(source.installs("sample-acme"))).find((one) => one.id === "ac-mcp-docs")!;
        expect(held.config?.extra_headers).toEqual({ "X-Team": "********" });
        const docs = (await run(source.accounts("sample-acme"))).find((one) => one.id === "acc-mcp-docs")!;
        expect(docs.status).toBe("REAUTH_REQUIRED");
    });

    it("removes an install with its accounts", async () => {
        const source = await freshSample();
        const install = (await run(source.installs("sample-acme"))).find((one) => one.id === "ac-sql")!;
        await run(source.deleteInstall("sample-acme", install));
        expect((await run(source.accounts("sample-acme"))).some((one) => one.connectorId === "sql")).toBe(false);
    });
});

describe("what each connect screen offers", () => {
    const oauth = (extra: Record<string, unknown> = {}): CatalogEntry => ({
        id: "slack", title: "Slack",
        kinds: [{ kind: "http", auth_scheme: "OAUTH2", system_default_available: true, supports_org_custom_oauth: true, oauth2_defaults: { authorization_url: "x" },
            config_schema: { type: "object", properties: { client_id: { type: "string" }, client_secret: { type: "string" } }, required: ["client_id", "client_secret"] }, ...extra }],
    });
    const lemmaApp: Install = { id: "a1", connector_id: "slack", kind: "http", name: "slack", status: "ACTIVE", is_default: true, config_source: "SYSTEM_DEFAULT", auth_scheme: "OAUTH2" };

    it("signs in straight away on Lemma's app, offering the organization's own beside it to those who may", () => {
        const plan = planConnect({ entry: oauth(), title: "Slack", against: lemmaApp, bringingApp: false, manage: MANAGER });
        expect(plan).toMatchObject({ mode: "redirect", submit: "Continue", offerOwnApp: true, title: "Connect Slack" });
        expect(planConnect({ entry: oauth(), title: "Slack", against: lemmaApp, bringingApp: false, manage: MEMBER }).offerOwnApp).toBe(false);
        expect(planConnect({ entry: oauth(), title: "Slack", against: { ...lemmaApp, config_source: "ORG_CUSTOM" }, bringingApp: false, manage: MANAGER }).offerOwnApp).toBe(false);
    });

    it("asks for the organization's own app with Lemma's redirect URL to register", () => {
        const plan = planConnect({ entry: oauth(), title: "Slack", against: lemmaApp, bringingApp: true, manage: MANAGER });
        expect(plan).toMatchObject({ mode: "app-form", title: "Use your own Slack app", showRedirectUri: true, submit: "Save and sign in" });
        expect(plan.list.map((field) => [field.name, field.kind])).toEqual([["client_id", "text"], ["client_secret", "secret"]]);
    });

    it("says what cannot be connected from here", () => {
        const empty: CatalogEntry = { id: "odd", title: "Odd", kinds: [{ kind: "http", auth_scheme: "API_KEY", system_default_available: true, config_schema: { type: "object", properties: {} } }] };
        expect(planConnect({ entry: empty, title: "Odd", against: null, bringingApp: false, manage: MANAGER }).mode).toBe("undescribed");
        const open: CatalogEntry = { id: "open", title: "Open", kinds: [{ kind: "http", auth_scheme: "NOAUTH", system_default_available: true, config_schema: { type: "object", properties: {} } }] };
        expect(planConnect({ entry: open, title: "Open", against: null, bringingApp: false, manage: MANAGER }).mode).toBe("nothing-to-ask");
    });

    it("connects against the default install, else the first that is on", () => {
        const off = { ...lemmaApp, id: "off", status: "DISABLED", is_default: false };
        const second = { ...lemmaApp, id: "b", is_default: false };
        expect(connectTarget([off, second, lemmaApp], null)?.id).toBe("a1");
        expect(connectTarget([off, second], null)?.id).toBe("b");
        expect(connectTarget([off, second], "off")?.id).toBe("off");
        expect(connectTarget([], null)).toBeNull();
    });

    it("offers each account the one thing that fixes it", () => {
        const telegram: CatalogEntry = { id: "telegram", title: "Telegram", kinds: [{ kind: "http", auth_scheme: "API_KEY", system_default_available: true, credential_schema: { type: "object", properties: { bot_token: { type: "string" } } } }] };
        const tokenInstall: Install = { ...lemmaApp, id: "t", connector_id: "telegram", auth_scheme: "API_KEY" };
        expect(accountActs(account({ id: "1", connector_id: "slack", status: "REAUTH_REQUIRED" }), lemmaApp, oauth())).toMatchObject({ dead: true, reconnect: true, rotate: false });
        expect(accountActs(account({ id: "2", connector_id: "telegram" }), tokenInstall, telegram)).toMatchObject({ dead: false, reconnect: false, rotate: true });
        expect(accountActs(account({ id: "3", connector_id: "github", install_state: "CHOOSE_INSTALL" }), lemmaApp, oauth())).toMatchObject({ unfinished: true, choose: true });
        /* Waiting on an owner: nothing here can fix it. */
        expect(accountActs(account({ id: "4", connector_id: "github", install_state: "PENDING_APPROVAL" }), lemmaApp, oauth())).toMatchObject({ unfinished: false, reconnect: false, rotate: false });
        expect(rotateFields(telegram, tokenInstall).map((one) => one.name)).toEqual(["bot_token"]);
    });

    it("offers each install what it can take", () => {
        const mcp: CatalogEntry = { id: "mcp", title: "MCP Server", kinds: [{ kind: "mcp", auth_scheme: "API_KEY", config_schema: { type: "object", properties: { server_url: { type: "string" } }, required: ["server_url"] } }] };
        const server: Install = { id: "s", connector_id: "mcp", kind: "mcp", name: "Tools", status: "ACTIVE", config_source: "ORG_CUSTOM", auth_scheme: "OAUTH2", config: { server_url: "https://tools.acme.example/mcp" } };
        expect(installActs(server, mcp, [])).toEqual({ addressed: true, off: false, needsSignIn: true, connect: "sign-in", editable: true });
        expect(installActs(server, mcp, [account({ id: "x", connector_id: "mcp", auth_config_id: "s" })]).connect).toBeNull();
        expect(installActs(lemmaApp, oauth(), [])).toMatchObject({ addressed: false, connect: "connect", editable: false });
        expect(installActs({ ...lemmaApp, status: "DISABLED" }, oauth(), []).connect).toBeNull();
        expect(installWhere(server, mcp)).toBe("tools.acme.example/mcp");
        expect(installWhere({ ...lemmaApp, config_source: "ORG_CUSTOM" }, oauth())).toBe("Your own app");
        expect(installWhere(lemmaApp, oauth())).toBe("Lemma’s app");
        expect(installsLabel(mcp)).toBe("Servers");
        expect(installsLabel(oauth())).toBe("Installs");
        expect(installsShown(oauth(), [lemmaApp])).toBe(false);
        expect(installsShown(oauth(), [lemmaApp, { ...lemmaApp, id: "two" }])).toBe(true);
        expect(installsShown(mcp, [server])).toBe(true);
    });

    it("names installs apart, and says what an edit did", () => {
        expect(nameProblem("  ", "", [])).toBe("Give it a name you will recognise later.");
        expect(nameProblem("Docs", "", ["Docs"])).toBe("Another connection here is already called that.");
        expect(nameProblem("Docs", "Docs", ["Docs"])).toBeNull();
        expect(reauthWords(1)).toBe("One account was connected to the old one, and has to sign in again. It is kept, and marked in the list.");
        expect(reauthWords(3)).toContain("3 accounts were");
    });
});

describe("a sign-in, read back off the accounts", () => {
    const before = snapshot([
        account({ id: "old", connector_id: "slack", auth_config_id: "a1" }),
        account({ id: "out", connector_id: "slack", auth_config_id: "a1", status: "REAUTH_REQUIRED" }),
    ]);
    const pending = { connectorId: "slack", authConfigId: "a1", before };

    it("finds a new account, or one brought back, and never one already there", () => {
        expect(readSignIn(pending, [account({ id: "old", connector_id: "slack", auth_config_id: "a1" })])).toEqual({ kind: "nothing", pending });
        expect(readSignIn(pending, [account({ id: "new", connector_id: "slack", auth_config_id: "a1" })])).toEqual({ kind: "connected", accountId: "new" });
        expect(readSignIn(pending, [account({ id: "out", connector_id: "slack", auth_config_id: "a1" })])).toEqual({ kind: "connected", accountId: "out" });
        /* On another install: not this sign-in's. */
        expect(readSignIn(pending, [account({ id: "new", connector_id: "slack", auth_config_id: "a2" })]).kind).toBe("nothing");
    });

    it("tells an account that cannot reach anything yet from nothing at all", () => {
        const made = readSignIn(pending, [account({ id: "gh", connector_id: "slack", auth_config_id: "a1", install_state: "INSTALL_REQUIRED" })]);
        expect(made).toEqual({ kind: "unfinished", accountId: "gh", trouble: "Needs installing before it can reach anything" });
        expect(signInWords(made, "GitHub").text).toBe("Almost there. Needs installing before it can reach anything.");
    });

    it("passes on a refusal in the provider's words", () => {
        const refused = readSignIn(pending, [], { connect: "error", connector: "slack", account: null, reason: "access_denied" });
        expect(refused).toEqual({ kind: "refused", reason: "access_denied" });
        expect(signInWords(refused, "Slack")).toEqual({ text: "access_denied", bad: true });
        expect(signInWords({ kind: "nothing", pending }, "Slack").bad).toBe(false);
    });

    it("asks the web's completion page to come back to the phone", () => {
        expect(PHONE_RETURN.startsWith("/")).toBe(true);
        expect(PHONE_RETURN).not.toMatch(/^\/\//);
    });
});

describe("connector words", () => {
    it("searches by title or id", () => {
        const pool = [{ id: "googledrive", title: "Google Drive" }, { id: "gmail", title: "Gmail" }];
        expect(searchConnectors(pool, "drive").map((one) => one.id)).toEqual(["googledrive"]);
        expect(searchConnectors(pool, "GMAIL").map((one) => one.id)).toEqual(["gmail"]);
        expect(searchConnectors(pool, "  ")).toHaveLength(2);
    });

    it("says why a list is empty", () => {
        expect(emptyWords("zzz", "available", 10)).toBe("Nothing matches “zzz”.");
        expect(emptyWords("", "connected", 10)).toBe("Nothing connected yet.");
        expect(emptyWords("", "trouble", 10)).toBe("Everything connected is working.");
        expect(emptyWords("", "available", 10)).toBe("Every connector is already connected.");
        expect(emptyWords("", "available", 0)).toBe("No connectors are in the catalog yet. It may still be loading.");
    });

    it("falls back from needs attention once nothing does", () => {
        const sliced = { connected: [], available: [], ailing: [], trouble: 0 };
        expect(shownSlice("trouble", sliced)).toBe("connected");
        expect(shownSlice(null, sliced)).toBe("available");
    });

    it("says the Lemma tab's row as the web does", () => {
        const catalog = [{ id: "slack", title: "Slack" }, { id: "gmail", title: "Gmail" }] as Connector[];
        expect(connectorsLine(catalog, [])).toEqual({ text: "Nothing connected yet", needs: false });
        expect(connectorsLine(catalog, [account({ id: "1", connector_id: "slack" }), account({ id: "2", connector_id: "gmail" }), account({ id: "3", connector_id: "gmail" })]))
            .toEqual({ text: "Slack, Gmail", needs: false });
        expect(connectorsLine(catalog, [account({ id: "1", connector_id: "slack", status: "REAUTH_REQUIRED" })])).toEqual({ text: "1 account needs attention", needs: true });
        expect(metaWords([])).toBe("No accounts yet");
    });

    it("loads a logo only from an address a phone can reach", () => {
        expect(iconAddress("https://logos.example.invalid/slack.svg", "https://lemma.work")).toBe("https://logos.example.invalid/slack.svg");
        expect(iconAddress("/connector-logos/slack.svg", "https://lemma.work/")).toBe("https://lemma.work/connector-logos/slack.svg");
        expect(iconAddress("slack.png", "https://lemma.work")).toBeNull();
        expect(iconAddress("//evil.example/x.svg", "https://lemma.work")).toBeNull();
        expect(iconAddress("", "https://lemma.work")).toBeNull();
    });

    it("says an account's state and when it was connected", () => {
        const install: Install = { id: "a2", connector_id: "gmail", kind: "http", name: "gmail-2" };
        expect(accountHint(account({ id: "1", connector_id: "gmail", status: "REAUTH_REQUIRED", provider_account_id: "U1" }), install, true)).toBe("Needs signing in again · On gmail-2 · U1");
        expect(accountHint(account({ id: "1", connector_id: "gmail", is_default: true }), null, false)).toBe("Default");
        expect(statusWords(account({ id: "1", connector_id: "x" }))).toBe("Connected");
        expect(statusWords(account({ id: "1", connector_id: "x", status: "REAUTH_REQUIRED" }))).toBe("Signed out");
        expect(statusWords(account({ id: "1", connector_id: "x", install_state: "PENDING_APPROVAL" }))).toBe("Not ready");
        expect(dayWords("2026-08-14T10:00:00Z")).toBe("14 Aug 2026");
        expect(dayWords("not a date")).toBeNull();
    });

    it("reads the redirect URI the server publishes, and never builds one", () => {
        expect(redirectUriOf({ oauth_redirect_uri: " https://api.example.invalid/cb " })).toBe("https://api.example.invalid/cb");
        expect(redirectUriOf({ oauth_redirect_uri: "" })).toBeNull();
        expect(redirectUriOf(null)).toBeNull();
    });

    it("builds every connector route under settings", () => {
        expect(connectorsRoutes.connector("google drive")).toBe("/settings/connectors/google%20drive");
        expect(connectorsRoutes.connect("slack", { install: "a1", own: true })).toBe("/settings/connectors/slack/connect?install=a1&own=1");
        expect(connectorsRoutes.connect("slack")).toBe("/settings/connectors/slack/connect");
        expect(connectorsRoutes.add("openapi", "http")).toBe("/settings/connectors/openapi/add?kind=http");
        expect(connectorsRoutes.rotate("telegram", "acc-1")).toBe("/settings/connectors/telegram/account/acc-1/rotate");
        expect(connectorsRoutes.edit("mcp", "ac-2")).toBe("/settings/connectors/mcp/install/ac-2/edit");
    });
});

describe("an account's name", () => {
    it("keeps an address or a handle as the provider gave it", async () => {
        const { accountTitle } = await import("../src/areas/admin/connectors/model");
        const account = { id: "a", connectorId: "github", authConfigId: "", label: "ada@acme.example", ref: "", status: "CONNECTED", installState: "READY", usable: true, isDefault: false, createdAt: "" };
        expect(accountTitle(account, { title: "GitHub" })).toBe("ada@acme.example");
        expect(accountTitle({ ...account, label: "" }, { title: "Slack" })).toBe("Your Slack account");
    });
});
