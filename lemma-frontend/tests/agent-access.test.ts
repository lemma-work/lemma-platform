import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import {
    MCP_CLIENTS,
    accessLabel,
    connectorName,
    disconnectClient,
    mcpUrl,
    quote,
    reachableFromInternet,
    serverName,
    serverSteps,
    setupCommands,
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

test("a space's MCP URL hangs off the API, one per space", () => {
    assert.equal(mcpUrl("https://api.lemma.work", pod.id), "https://api.lemma.work/mcp/" + pod.id);
    assert.equal(mcpUrl("https://example.test/api/", pod.id), "https://example.test/api/mcp/" + pod.id);
    assert.equal(mcpUrl(null, pod.id), null);
    assert.equal(mcpUrl("not a url", pod.id), null);
});

test("Claude and ChatGPT are told when the API is only on this computer", () => {
    assert.equal(reachableFromInternet("https://api.lemma.work/mcp/x"), true);
    for (const local of ["http://localhost:8000", "http://127.0.0.1:8790", "http://api.lemma.localhost:8711"]) {
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
