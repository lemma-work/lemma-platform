import test from "node:test";
import assert from "node:assert/strict";
import {
    cleanupScopes, computeLabel, groupMachines, inputMessage, isFinished, parseStreamMessage, pathRef,
    previewLive, processStreamUrl, resetLines, resizeMessage, sizeLabel, totalFreed, usageSummary,
    validMachineName, type Machine, type MachineCapabilities,
} from "../src/computer/environments.ts";

const CAPS: MachineCapabilities = {
    processes: true, pty: true, python: true, files: true, previews: true, browser: true, docker: false,
};

function machine(overrides: Partial<Machine>): Machine {
    return {
        id: "00000000-0000-0000-0000-000000000000",
        ref: "me",
        kind: "default",
        name: "me",
        display_name: "",
        template: "workspace",
        size: "2x4",
        fabric: "e2b",
        capabilities: CAPS,
        visibility: "private",
        status: "stopped",
        access: "manage",
        run_slots: 1,
        home: "/home/user",
        ...overrides,
    };
}

test("a ref goes into a path with a colon for the pod slash, encoded", () => {
    assert.equal(pathRef("me"), "me");
    assert.equal(pathRef("pod:abc/build"), "pod%3Aabc%3Abuild");
    /* Only the first slash: the rest would be the name's, and a name cannot
       hold one anyway — but replacing every slash would hide that bug. */
    assert.equal(pathRef("pod:abc/a/b"), "pod%3Aabc%3Aa%2Fb");
    assert.equal(pathRef("mine:laptop"), "mine%3Alaptop");
});

test("the rail groups machines by kind and leaves out deleted ones", () => {
    const rail = groupMachines([
        machine({ ref: "pod:p/zeta", kind: "pod", name: "zeta" }),
        machine({ ref: "me" }),
        machine({ ref: "pod:p/alpha", kind: "pod", name: "alpha" }),
        machine({ ref: "pod:p/gone", kind: "pod", name: "gone", status: "deleted" }),
        machine({ ref: "mine:laptop", kind: "connected", name: "laptop", status: "offline" }),
    ]);
    assert.deepEqual(rail.mine.map((m) => m.ref), ["me"]);
    assert.deepEqual(rail.pod.map((m) => m.name), ["alpha", "zeta"]);
    assert.deepEqual(rail.connected.map((m) => m.name), ["laptop"]);
});

test("sizes and compute read the way a person says them", () => {
    assert.equal(sizeLabel("2x4"), "2 vCPU · 4 GB");
    assert.equal(sizeLabel("0.5x1"), "0.5 vCPU · 1 GB");
    assert.equal(sizeLabel("custom"), "custom");
    assert.equal(computeLabel(3.14159), "3.1 CU");
    assert.equal(computeLabel(0), "0.0 CU");
    assert.equal(computeLabel(null), null);
    assert.equal(computeLabel(undefined), null);
});

test("the usage line totals counted compute and counts the running", () => {
    assert.equal(usageSummary([
        machine({ compute_units_this_month: 1.25, status: "running" }),
        machine({ ref: "pod:p/a", kind: "pod", compute_units_this_month: 2 }),
        machine({ ref: "pod:p/b", kind: "pod" }),
    ]), "3.3 CU this month · 1 running");
    /* Nothing counted is not zero used: the total is left out, not printed as 0. */
    assert.equal(usageSummary([machine({})]), "0 running");
});

test("clean up offers docker only where the machine has it, and never home", () => {
    assert.deepEqual(cleanupScopes(CAPS), ["processes", "runs", "caches", "browser-session"]);
    assert.deepEqual(cleanupScopes({ ...CAPS, docker: true }), ["processes", "runs", "caches", "docker", "browser-session"]);
    assert.ok(!cleanupScopes({ ...CAPS, docker: true }).includes("home"));
});

test("reset results say what each scope gave back", () => {
    const results = [
        { scope: "caches" as const, freed_bytes: 3 * 1024 * 1024 },
        { scope: "processes" as const, freed_bytes: null, detail: "2 stopped" },
        { scope: "docker" as const, freed_bytes: 0 },
    ];
    assert.deepEqual(resetLines(results), [
        "Clear caches — freed 3.0 MB",
        "Stop processes — 2 stopped",
        "Prune Docker",
    ]);
    assert.equal(totalFreed(results), 3 * 1024 * 1024);
    assert.equal(totalFreed([{ scope: "runs", freed_bytes: -5 }]), 0);
});

test("stream messages parse, and anything else is dropped", () => {
    assert.deepEqual(
        parseStreamMessage(JSON.stringify({ type: "output", sequence: 4, channel: "pty", data: "hi" })),
        { type: "output", sequence: 4, channel: "pty", data: "hi" },
    );
    assert.deepEqual(
        parseStreamMessage(JSON.stringify({ type: "exit", state: "succeeded", exit_code: 0 })),
        { type: "exit", state: "succeeded", exit_code: 0 },
    );
    assert.deepEqual(
        parseStreamMessage(JSON.stringify({ type: "exit", state: "cancelled", exit_code: null })),
        { type: "exit", state: "cancelled", exit_code: null },
    );
    assert.equal(parseStreamMessage("not json"), null);
    assert.equal(parseStreamMessage(JSON.stringify({ type: "output" })), null);
    assert.equal(parseStreamMessage(JSON.stringify(["output"])), null);
    assert.equal(parseStreamMessage(JSON.stringify({ type: "hello" })), null);
    assert.equal(parseStreamMessage(new ArrayBuffer(2)), null);
});

test("what the terminal sends is the protocol's own two shapes", () => {
    assert.deepEqual(JSON.parse(inputMessage("ls\r")), { type: "input", data: "ls\r" });
    assert.deepEqual(JSON.parse(resizeMessage(120, 40)), { type: "resize", cols: 120, rows: 40 });
});

test("the stream socket is the API origin over ws, with the token only when there is one", () => {
    assert.equal(
        processStreamUrl("https://api.example.com/", "pod:p/build", "abc"),
        "wss://api.example.com/machines/pod%3Ap%3Abuild/processes/abc/stream",
    );
    assert.equal(
        processStreamUrl("http://localhost:8000", "me", "abc", { accessToken: "t", after: 7 }),
        "ws://localhost:8000/machines/me/processes/abc/stream?after=7&access_token=t",
    );
});

test("finished process states", () => {
    assert.ok(isFinished("succeeded"));
    assert.ok(isFinished("timed_out"));
    assert.ok(!isFinished("running"));
    assert.ok(!isFinished("reserved"));
});

test("machine names match the server's rule", () => {
    assert.ok(validMachineName("build-box"));
    assert.ok(validMachineName("a"));
    assert.ok(!validMachineName("-a"));
    assert.ok(!validMachineName("a-"));
    assert.ok(!validMachineName("Build"));
    assert.ok(!validMachineName("a".repeat(41)));
    assert.ok(validMachineName("a".repeat(40)));
});

test("a revoked or expired preview is not live", () => {
    const now = Date.parse("2026-10-22T12:00:00Z");
    const base = { id: "x", port: 3000, visibility: "link" as const, url: "https://x", created_at: "2026-10-22T10:00:00Z" };
    assert.ok(previewLive(base, now));
    assert.ok(!previewLive({ ...base, revoked_at: "2026-10-22T11:00:00Z" }, now));
    assert.ok(!previewLive({ ...base, expires_at: "2026-10-22T11:59:59Z" }, now));
    assert.ok(previewLive({ ...base, expires_at: "2026-10-22T13:00:00Z" }, now));
});
