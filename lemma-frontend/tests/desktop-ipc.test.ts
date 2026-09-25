import test from "node:test";
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { WORKSPACE_COMMANDS } from "../src/desktop/bridge.ts";

/** The contract between this app and the desktop shell.
 *
 *  The workspace is a remote origin to Tauri, so a command reaches the shell
 *  only if `desktop/capabilities/workspace.json` grants it to that origin and
 *  `desktop/src/app.rs` registers a handler for it. Miss either and the call
 *  fails at runtime, inside the app, with an ACL error nobody can act on — and
 *  nothing in this app's own checks would have noticed. So both files are read
 *  here, as text, against the one list `invoke` accepts. */

const FRONTEND = path.resolve(import.meta.dirname, "..");
const DESKTOP = path.resolve(FRONTEND, "../desktop");

/** `allow-agent-host-status` → `agent_host_status`, Tauri's own mapping. */
function grantedCommands(capability: { permissions?: unknown[] }): Set<string> {
    const granted = new Set<string>();
    for (const permission of capability.permissions ?? []) {
        if (typeof permission !== "string" || !permission.startsWith("allow-")) continue;
        granted.add(permission.slice("allow-".length).replace(/-/g, "_"));
    }
    return granted;
}

/** The last path segment of every entry in `generate_handler![...]`. */
function registeredCommands(appRs: string): Set<string> {
    const block = /generate_handler!\[([\s\S]*?)\]/.exec(appRs);
    assert.ok(block, "desktop/src/app.rs has no generate_handler![...] block to read");
    return new Set(
        block[1]
            .split(",")
            .map((entry) => entry.replace(/\/\/.*$/gm, "").trim())
            .filter(Boolean)
            .map((entry) => entry.split("::").pop()!.trim()),
    );
}

async function workspaceGrants(): Promise<Set<string>> {
    return grantedCommands(JSON.parse(await readFile(path.join(DESKTOP, "capabilities/workspace.json"), "utf8")));
}

async function registered(): Promise<Set<string>> {
    return registeredCommands(await readFile(path.join(DESKTOP, "src/app.rs"), "utf8"));
}

test("every command this app can invoke is granted to the workspace origin", async () => {
    const granted = await workspaceGrants();
    const missing = WORKSPACE_COMMANDS.filter((command) => !granted.has(command));
    assert.deepEqual(missing, [], `not granted in desktop/capabilities/workspace.json: ${missing.join(", ")}`);
});

test("every command this app can invoke is registered by the shell", async () => {
    const commands = await registered();
    const missing = WORKSPACE_COMMANDS.filter((command) => !commands.has(command));
    assert.deepEqual(missing, [], `not registered in desktop/src/app.rs: ${missing.join(", ")}`);
});

test("Run commands on this Mac is one of them, granted and registered", async () => {
    /* Named here as well as by the list, so dropping it from the list does
       not quietly drop it from the contract. */
    assert.ok((WORKSPACE_COMMANDS as readonly string[]).includes("set_host_execution"));
    assert.ok((await workspaceGrants()).has("set_host_execution"));
    assert.ok((await registered()).has("set_host_execution"));
});

test("the check fails when a command is missing from either file", () => {
    /* A contract test that cannot fail is documentation. Prove both readers
       notice an absence rather than, say, matching everything. */
    const granted = grantedCommands({ permissions: ["allow-agent-host-status", "core:default"] });
    assert.ok(granted.has("agent_host_status"));
    assert.ok(!granted.has("agent_host_pair"));

    const handlers = registeredCommands("tauri::generate_handler![\n  agent_host_ui::agent_host_status,\n  // a comment\n  x::y,\n]");
    assert.deepEqual([...handlers], ["agent_host_status", "y"]);
    assert.ok(!handlers.has("agent_host_pair"));
});

test("nothing outside src/desktop/bridge.ts reaches the shell directly", async () => {
    /* The union only protects the calls that go through `invoke`. A second
       door would be unchecked, so there must not be one. */
    const offenders: string[] = [];
    async function walk(dir: string) {
        for (const entry of await readdir(dir, { withFileTypes: true })) {
            const full = path.join(dir, entry.name);
            if (entry.isDirectory()) await walk(full);
            else if (/\.(ts|tsx)$/.test(entry.name)) {
                const relative = path.relative(FRONTEND, full);
                if (relative === path.join("src", "desktop", "bridge.ts")) continue;
                if ((await readFile(full, "utf8")).includes("__TAURI__")) offenders.push(relative);
            }
        }
    }
    await walk(path.join(FRONTEND, "src"));
    assert.deepEqual(offenders, []);
});
