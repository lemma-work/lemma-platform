import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import {
    MCP_CLIENTS,
    accessLabel,
    connectorName,
    disconnectClient,
    verifiedHost,
    fetchMcpUrl,
    listeningLine,
    listeningStatus,
    loadConnections,
    quote,
    reachableFromInternet,
    serverName,
    serverSteps,
    setupCommands,
    setListening,
    setupPrompt,
    starterPrompts,
} from "../src/space/agent-access-model.ts";

const pod = { id: "0b6f3c1e-1111-4222-8333-944455556666", name: "Marketing" } as Parameters<typeof setupPrompt>[0];
const claude = { id: "claude", label: "Claude Code", target: "claude", launch: null };

test("a quoted prompt reaches the program as exactly one argument, apostrophes and all", () => {
    const text = "It's $HOME and `ls` — \"quoted\"";
    const echoed = execFileSync("/bin/sh", ["-c", "printf %s " + quote(text)]).toString();
    assert.equal(echoed, text);
});

test("the cloud needs no server step; anything else is introduced and selected", () => {
    assert.deepEqual(serverSteps("https://api.lemma.work", "https://lemma.work"), []);
    assert.deepEqual(serverSteps(null, "https://x"), []);
    assert.deepEqual(serverSteps("http://localhost:8000", "http://localhost:3000"), [
        "lemma servers create local --base-url http://localhost:8000 --auth-url http://localhost:3000/auth",
        "lemma servers select local",
    ]);
    assert.equal(serverSteps("https://api.acme.dev/", "https://lemma.acme.dev")[1], "lemma servers select acme");
});

test("setup binds this space by id, after sign-in and skills", () => {
    const steps = setupCommands(pod, claude, []);
    assert.equal(steps[0], "uv tool install lemma-terminal");
    assert.ok(steps.indexOf("lemma auth login") < steps.indexOf("lemma pods select " + pod.id + " --save-default"));
    assert.ok(steps.includes("lemma skills install --target claude"));
    assert.match(setupPrompt(pod, claude, []), /Marketing/);
});

test("every starter prompt names the space and its pod id", () => {
    for (const item of starterPrompts(pod)) {
        assert.ok(item.prompt.includes("Marketing") && item.prompt.includes(pod.id), item.title);
    }
});

test("the link is the API's own, and there is none where the feature is off", async () => {
    const on = (async () => new Response(JSON.stringify({ url: "https://api.lemma.work/mcp/" + pod.id }), { status: 200 })) as unknown as typeof fetch;
    assert.equal(await fetchMcpUrl("https://api.lemma.work", pod.id, on), "https://api.lemma.work/mcp/" + pod.id);
    const off = (async () => new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 })) as unknown as typeof fetch;
    assert.equal(await fetchMcpUrl("https://api.lemma.work", pod.id, off), null);
});

test("a failed request for the link is an error, not the feature being off", async () => {
    const down = (async () => new Response("{}", { status: 502 })) as unknown as typeof fetch;
    await assert.rejects(fetchMcpUrl("https://api.lemma.work", pod.id, down), /could not be loaded \(502\)/);
    const offline = (async () => { throw new TypeError("Failed to fetch"); }) as unknown as typeof fetch;
    await assert.rejects(fetchMcpUrl("https://api.lemma.work", pod.id, offline), TypeError);
});

test("admins are shown everyone's connections, everyone else their own", async () => {
    const asked: string[] = [];
    const member = (async (url: string) => {
        asked.push(url);
        return url.includes("everyone=true")
            ? new Response("{}", { status: 403 })
            : new Response(JSON.stringify({ items: [] }), { status: 200 });
    }) as unknown as typeof fetch;
    assert.deepEqual(await loadConnections("https://api.lemma.work", pod.id, member), { items: [], everyone: false });
    assert.equal(asked.length, 2);
    const admin = (async () => new Response(JSON.stringify({ items: [] }), { status: 200 })) as unknown as typeof fetch;
    assert.equal((await loadConnections("https://api.lemma.work", pod.id, admin)).everyone, true);
});

test("Claude and ChatGPT are told when the API is only on this computer", () => {
    assert.equal(reachableFromInternet("https://api.lemma.work/mcp/x"), true);
    for (const local of [
        "http://localhost:8000", "http://127.0.0.1:8790", "http://api.lemma.localhost:8711",
        "http://10.0.0.5:8000", "http://192.168.68.101:8790", "http://172.20.1.1", "http://mac.local:8000",
    ]) {
        assert.equal(reachableFromInternet(local), false, local);
    }
    const remote = MCP_CLIENTS.filter(client => client.remote).map(client => client.id);
    assert.deepEqual(remote, ["claude", "chatgpt"]);
});

test("the Claude Code command names the space and passes the URL whole", () => {
    const code = MCP_CLIENTS.find(client => client.id === "claude-code");
    assert.ok(code?.command);
    const url = "https://api.lemma.work/mcp/" + pod.id;
    assert.equal(code.command(url, pod), "claude mcp add --transport http lemma-marketing " + url);
    assert.equal(serverName({ name: "Q3 Launch — EU/US!" }), "lemma-q3-launch-eu-us");
    assert.equal(serverName({ name: "日本" }), "lemma");
});

test("what a connection may do is said in plain words", () => {
    assert.equal(accessLabel(["pod:read", "pod:write"]), "Read and write");
    assert.equal(accessLabel(["pod:read"]), "Read only");
    assert.equal(accessLabel(["pod:read", "pod:events"]), "Read only, told about new rows");
});

test("a subscription says what it is told about and how it stands", () => {
    const base = { id: "sub_1", name: "record.created", arguments: { table: "leads" }, last_delivery_at: null, last_error: null };
    const ago = () => "5m ago";
    assert.equal(listeningLine(base), "New rows in leads");
    assert.equal(listeningStatus(base, ago), "nothing sent yet");
    assert.equal(listeningStatus({ ...base, last_delivery_at: "2026-10-09T10:00:00Z" }, ago), "last sent 5m ago");
    assert.equal(listeningStatus({ ...base, last_error: "http_500" }, ago), "last delivery failed");
    assert.match(listeningStatus({ ...base, paused_at: "2026-10-09T10:00:00Z" }, ago), /^paused/);
    assert.match(
        listeningStatus({ ...base, stopped_at: "2026-10-09T10:00:00Z", paused_at: "2026-10-09T10:00:00Z" }, ago),
        /^stopped 5m ago; the app is refused until you resume it/,
        "a person's Stop is what they need to see, over a pause",
    );
});

test("stop and resume reach the right endpoint, and a 404 is said", async () => {
    const calls: Array<{ url: string; method: string }> = [];
    let status = 204;
    const fetcher = (async (url: string, init: RequestInit) => {
        calls.push({ url, method: String(init.method) });
        return new Response(null, { status });
    }) as unknown as typeof fetch;

    await setListening("https://api.test", "g/1", "sub_1", false, fetcher);
    await setListening("https://api.test", "g/1", "sub_1", true, fetcher);
    assert.deepEqual(calls, [
        { url: "https://api.test/oauth/grants/g%2F1/subscriptions/sub_1", method: "DELETE" },
        { url: "https://api.test/oauth/grants/g%2F1/subscriptions/sub_1/resume", method: "POST" },
    ]);

    status = 404;
    await assert.rejects(setListening("https://api.test", "g", "sub_1", false, fetcher), /not yours to change/);
    status = 500;
    await assert.rejects(setListening("https://api.test", "g", "sub_1", true, fetcher), /could not be resumed \(500\)/);
});

test("disconnecting something already gone is not an error", async () => {
    const gone = (async () => new Response(null, { status: 404 })) as unknown as typeof fetch;
    await disconnectClient("https://api.lemma.work", "g", gone);
    const broken = (async () => new Response(null, { status: 500 })) as unknown as typeof fetch;
    await assert.rejects(disconnectClient("https://api.lemma.work", "g", broken), /could not be disconnected/);
});

test("each client's steps name the connector the same way and never ask for more than three clicks", () => {
    const url = "https://api.lemma.work/mcp/" + pod.id;
    assert.equal(connectorName(pod), "Lemma – Marketing");
    for (const client of MCP_CLIENTS) {
        const steps = client.steps(url, pod);
        assert.ok(steps.length >= 1 && steps.length <= 3, client.id);
    }
    for (const id of ["claude", "chatgpt"]) {
        const steps = MCP_CLIENTS.find(client => client.id === id)?.steps(url, pod).join(" ") ?? "";
        assert.match(steps, /Lemma – Marketing/);
        assert.match(steps, /allow Lemma/);
    }
    assert.deepEqual(MCP_CLIENTS.map(client => client.id), ["claude", "chatgpt", "claude-code", "other"]);
});

test("an app is shown with the host its document is served from, or as unverified", () => {
    assert.equal(verifiedHost({ client_id: "https://claude.ai/oauth/claude-code-client-metadata" }), "claude.ai");
    assert.equal(verifiedHost({ client_id: "3f1c0e7a-registered-itself" }), null);
    assert.equal(verifiedHost({ client_id: "http://claude.ai/doc" }), null);
});
