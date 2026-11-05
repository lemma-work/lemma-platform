import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
    connectionProblem,
    connectionsPath,
    connectorLabel,
    connectorsFor,
    failureOf,
    freshSlug,
    readPodConnectors,
    statusNote,
} from "../src/data/pod-connectors.ts";
import { liveSource } from "../src/data/live.ts";
import { disconnect } from "../src/session/client.ts";

/** A pod's connectors, by slug, and the refusals they answer with. Every
 *  place that used to pick an account now picks one of these, and every call
 *  that can be refused for want of an account says where to fix it. */

registerHooks({ load(url, context, nextLoad) {
    if (!url.endsWith(".tsx")) return nextLoad(url, context);
    return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), "utf8"), {
        compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
    }).outputText };
} });

const WIRE = [
    { slug: "slack", connector_id: "slack", mode: "SHARED", status: "READY", shared_account: { display_name: "Acme Slack", owner_user_id: "u1", pod_account_id: "pa1" } },
    { slug: "teams-ops", connector_id: "teams", mode: "SHARED", status: "NEEDS_ACCOUNT" },
    { slug: "github", connector_id: "github", mode: "PER_MEMBER", status: "READY" },
    { slug: "slack-old", connector_id: "slack", mode: "SHARED", status: "UNAVAILABLE" },
    { connector_id: "nameless", mode: "SHARED", status: "READY" },
];

test("a pod's connectors read off the wire, and one with no slug is not one", () => {
    const list = readPodConnectors({ items: WIRE });
    assert.deepEqual(list.map((one) => one.slug), ["slack", "teams-ops", "github", "slack-old"]);
    assert.equal(list[0].sharedAccount, "Acme Slack");
    assert.equal(readPodConnectors({ items: [{ slug: "x", mode: "SHARED", status: "SOMETHING_NEW" }] })[0].status, "UNAVAILABLE",
        "a status this client does not know is never drawn as ready");
});

test("a surface is only offered SHARED connectors, the ready ones first", () => {
    const list = readPodConnectors({ items: WIRE });
    assert.deepEqual(connectorsFor([...list].reverse(), "slack", "SHARED").map((one) => one.slug), ["slack", "slack-old"]);
    assert.deepEqual(connectorsFor(list, "github", "SHARED"), [], "a per-member connector is each person's own, not a surface's");
    assert.deepEqual(connectorsFor(list, "github").map((one) => one.slug), ["github"]);
    assert.equal(statusNote(list[1]), "No account shared yet");
    assert.equal(statusNote(list[3]), "Turned off for this organization");
    assert.equal(connectorLabel(list[0]), "slack — runs as Acme Slack");
    assert.equal(connectorLabel(list[2]), "github — each person's own account");
});

test("the connections page is addressed by pod, opened on a connector", () => {
    assert.equal(connectionsPath("p 1"), "/pods/p%201/connections");
    assert.equal(connectionsPath("p1", "support-inbox"), "/pods/p1/connections?connector=support-inbox");
});

test("a new connector's slug reads the same everywhere and is free in the pod", () => {
    assert.equal(freshSlug("telegram", []), "telegram");
    assert.equal(freshSlug("telegram", ["telegram", "telegram-2"]), "telegram-3");
    assert.equal(freshSlug("Google Drive!", []), "google-drive");
    assert.equal(freshSlug("", []), "account");
});

test("a connector refusal is read with its connect link, and any other failure is not one", () => {
    const refused = Object.assign(new Error("Connect your github account to use 'github' in this pod."), {
        code: "CONNECTION_REQUIRED",
        details: { pod_connector: "github", connector_id: "github", connect_url: "https://app.example.invalid/pods/p1/connections?connector=github" },
    });
    assert.deepEqual(connectionProblem(refused), {
        code: "CONNECTION_REQUIRED",
        message: "Connect your github account to use 'github' in this pod.",
        connectUrl: "https://app.example.invalid/pods/p1/connections?connector=github",
        podConnector: "github",
    });
    assert.equal(connectionProblem(Object.assign(new Error("nope"), { code: "FORBIDDEN" })), null);
    assert.deepEqual(failureOf(new Error("Boom"), "Fallback"), { message: "Boom", connectUrl: "", code: "" });
    assert.deepEqual(failureOf(null, "Fallback"), { message: "Fallback", connectUrl: "", code: "" });
    const link = "https://app.example.invalid/pods/p1/connections?connector=slack";
    const shared = failureOf(Object.assign(new Error("'slack' runs as one shared account…"), { code: "CONNECTOR_NEEDS_ACCOUNT", details: { pod_connector: "slack", connect_url: link } }), "x");
    assert.equal(shared.connectUrl, link, "the fix -- someone sharing an account -- is on the pod's connections page");
});

test("a CONNECTION_REQUIRED refusal is shown with its link", async () => {
    const { ProblemLine } = await import("../src/connect/connection-problem.tsx");
    const refused = Object.assign(new Error("Connect your github account to use 'github' in this pod."), {
        code: "CONNECTION_REQUIRED",
        details: { connect_url: "https://app.example.invalid/pods/p1/connections?connector=github" },
    });
    const html = renderToStaticMarkup(createElement(ProblemLine, { error: refused, fallback: "Couldn’t create the schedule." }));
    assert.match(html, /Connect your github account to use &#x27;github&#x27; in this pod\./);
    assert.match(html, /<a class="linkish" href="https:\/\/app\.example\.invalid\/pods\/p1\/connections\?connector=github">Connect your account<\/a>/);

    const expired = renderToStaticMarkup(createElement(ProblemLine, {
        error: Object.assign(new Error("Sign-in expired."), { code: "REAUTH_REQUIRED", details: { connect_url: "https://x.invalid/c" } }),
        fallback: "",
    }));
    assert.match(expired, />Reconnect<\/a>/);
    const unshared = renderToStaticMarkup(createElement(ProblemLine, {
        error: Object.assign(new Error("No account shared."), { code: "CONNECTOR_NEEDS_ACCOUNT", details: { connect_url: "https://x.invalid/c" } }),
        fallback: "",
    }));
    assert.match(unshared, />Open connections<\/a>/, "the reader need not be the one to share an account");
    const plain = renderToStaticMarkup(createElement(ProblemLine, { error: new Error("Name taken."), fallback: "" }));
    assert.doesNotMatch(plain, /<a /, "an ordinary failure has no link to offer");
});

/** The live source against a recorded API: what each call sends. */
async function recorded(
    context: { mock: { method: (target: object, name: string, impl: (...args: never[]) => unknown) => unknown } },
    answer: (path: string, method: string, body: Record<string, unknown> | null) => { status?: number; body: unknown },
) {
    process.env.NEXT_PUBLIC_API_URL = "https://pod-connectors.example.invalid";
    disconnect();
    const requests: { path: string; method: string; body: Record<string, unknown> | null }[] = [];
    context.mock.method(globalThis, "fetch", async (input: string | URL | Request, init?: RequestInit) => {
        const url = new URL(input instanceof Request ? input.url : String(input));
        const method = init?.method ?? (input instanceof Request ? input.method : "GET");
        const raw = typeof init?.body === "string" ? init.body : input instanceof Request ? await input.text() : "";
        const body = raw ? JSON.parse(raw) as Record<string, unknown> : null;
        requests.push({ path: url.pathname, method, body });
        const said = answer(url.pathname, method, body);
        return new Response(JSON.stringify(said.body), { status: said.status ?? 200, headers: { "content-type": "application/json" } });
    });
    return requests;
}

test("a surface binds to a shared connector by slug, never by account", async (context) => {
    const original = process.env.NEXT_PUBLIC_API_URL;
    try {
        const requests = await recorded(context, (path) => path.endsWith("/connectors")
            ? { body: { items: WIRE } }
            : { body: { id: "s1", name: "slack", platform: "SLACK", config: {}, pod_connector: "slack" } });
        assert.deepEqual((await liveSource.listPodConnectors("p1")).map((one) => one.slug), ["slack", "teams-ops", "github", "slack-old"]);
        await liveSource.connectPodConnector("p1", "SLACK", "slack");
        const made = requests.find((one) => one.method === "POST");
        assert.equal(made?.path, "/pods/p1/surfaces");
        assert.deepEqual(made?.body, { platform: "SLACK", credential_mode: "CUSTOM", pod_connector: "slack" });
    } finally {
        if (original === undefined) delete process.env.NEXT_PUBLIC_API_URL;
        else process.env.NEXT_PUBLIC_API_URL = original;
        disconnect();
    }
});

test("an account made for a surface is shared into the pod, then bound by the connector's slug", async (context) => {
    const original = process.env.NEXT_PUBLIC_API_URL;
    try {
        const requests = await recorded(context, (path, method, body) => {
            if (path.endsWith("/connector-accounts") && method === "GET") return { body: { items: [] } };
            if (path.endsWith("/connector-accounts")) {
                return { body: { id: "pa9", account_id: body?.account_id, auth_config_id: "install-tg", connector_id: "telegram", visibility: "POD", owner_user_id: "u1", pod_id: "p1", is_member_default: false, source: "MANUAL", created_at: "" } };
            }
            if (path.endsWith("/connectors") && method === "GET") return { body: { items: [{ slug: "telegram", mode: "PER_MEMBER", status: "READY" }] } };
            if (path.endsWith("/share-as")) {
                return { body: { ...body, id: "pc9", pod_id: "p1", auth_config_id: "install-tg", mode: "SHARED", shared_pod_account_id: "pa9", status: "READY", required: false, visibility: "POD", created_at: "", updated_at: "" } };
            }
            return { body: { id: "s9", name: "telegram", platform: "TELEGRAM", config: {} } };
        });
        await liveSource.connectOwnAccount("p1", "TELEGRAM", "acct-new");
        const writes = requests.filter((one) => one.method === "POST").map((one) => [one.path, one.body]);
        assert.deepEqual(writes, [
            ["/pods/p1/connector-accounts", { account_id: "acct-new", visibility: "POD" }],
            /* One request: the server shares the account and declares the
               connector, or does neither. */
            ["/pods/p1/connector-accounts/pa9/share-as", { slug: "telegram-2", purpose: "A teammate's own telegram" }],
            ["/pods/p1/surfaces", { platform: "TELEGRAM", credential_mode: "CUSTOM", pod_connector: "telegram-2" }],
        ]);
    } finally {
        if (original === undefined) delete process.env.NEXT_PUBLIC_API_URL;
        else process.env.NEXT_PUBLIC_API_URL = original;
        disconnect();
    }
});

test("an account the pod already shares as a connector is bound through that one", async (context) => {
    const original = process.env.NEXT_PUBLIC_API_URL;
    try {
        const requests = await recorded(context, (path, method) => {
            if (path.endsWith("/connector-accounts")) {
                return { body: { items: [{ id: "pa1", account_id: "acct-1", auth_config_id: "i1", visibility: "POD", owner_user_id: "u1", pod_id: "p1", is_member_default: false, source: "MANUAL", created_at: "" }] } };
            }
            if (path.endsWith("/connectors") && method === "GET") return { body: { items: [{ slug: "slack-bot", mode: "SHARED", status: "READY", shared_pod_account_id: "pa1" }] } };
            return { body: { id: "s1", name: "slack", platform: "SLACK", config: {} } };
        });
        await liveSource.connectOwnAccount("p1", "SLACK", "acct-1");
        const writes = requests.filter((one) => one.method !== "GET").map((one) => [one.path, one.body]);
        assert.deepEqual(writes, [["/pods/p1/surfaces", { platform: "SLACK", credential_mode: "CUSTOM", pod_connector: "slack-bot" }]]);
    } finally {
        if (original === undefined) delete process.env.NEXT_PUBLIC_API_URL;
        else process.env.NEXT_PUBLIC_API_URL = original;
        disconnect();
    }
});

test("a refused surface carries the server's connect link through the SDK", async (context) => {
    const original = process.env.NEXT_PUBLIC_API_URL;
    try {
        await recorded(context, () => ({
            status: 409,
            body: {
                code: "CONNECTOR_NEEDS_ACCOUNT",
                message: "'slack' runs as one shared account, and none is shared with this pod right now.",
                details: { pod_connector: "slack" },
            },
        }));
        const refused = await liveSource.connectPodConnector("p1", "SLACK", "slack").then(() => null, (error: unknown) => error);
        const problem = connectionProblem(refused);
        assert.equal(problem?.code, "CONNECTOR_NEEDS_ACCOUNT");
        assert.equal(problem?.podConnector, "slack");
        assert.match(problem?.message ?? "", /shared account/);
    } finally {
        if (original === undefined) delete process.env.NEXT_PUBLIC_API_URL;
        else process.env.NEXT_PUBLIC_API_URL = original;
        disconnect();
    }
});
