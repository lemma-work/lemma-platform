import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { quote, serverSteps, setupCommands, setupPrompt, starterPrompts } from "../src/space/agent-access-model.ts";

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
