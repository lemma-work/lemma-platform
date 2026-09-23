import { afterEach, describe, expect, it, vi } from "vitest";

// The bug this file exists for: the Local settings button decided whether to
// render from `window.__LEMMA_DESKTOP__.mode`, which is baked into the
// initialization script when the *window* is built. A first run that chooses
// Local Lemma writes the config and starts the stack without recreating the
// webview, so `mode` keeps replaying the launch-time value -- `undecided` --
// for the rest of the session. The one person who most needs Local settings,
// someone who has just finished setting local up, was the one person who could
// not see the button. A restart fixed it, which is exactly why it survived.
//
// The button now asks `useDesktopBridge`, whose predicate is asserted here:
// `DEPLOYMENT` comes from the frontend the local stack itself serves, so it
// cannot be older than the decision that started that stack.

const deployment = vi.hoisted(() => ({ value: "cloud" }));
vi.mock("@/lib/config", () => ({ isLocalDeployment: () => deployment.value === "local" }));

import { desktopBridgeAvailable } from "@/lib/desktop/local-capabilities";

function pretendWorkspace(options: { deployment: string; tauri: boolean }) {
    deployment.value = options.deployment;
    (globalThis as Record<string, unknown>).window = {
        // Deliberately the stale value a first run leaves behind.
        __LEMMA_DESKTOP__: { version: "0.7.2", mode: "undecided", platform: "macos" },
        __TAURI__: options.tauri ? { core: { invoke: () => Promise.resolve() } } : undefined,
    };
}

afterEach(() => {
    delete (globalThis as Record<string, unknown>).window;
});

describe("whether the workspace can open Local settings", () => {
    it("says yes on a local stack whose shell still reports the launch-time mode", () => {
        pretendWorkspace({ deployment: "local", tauri: true });
        expect(desktopBridgeAvailable()).toBe(true);
    });

    it("says no in a browser reaching the same local stack", () => {
        // A LAN visitor or a public link has no shell to invoke, so the button
        // could only ever produce an error.
        pretendWorkspace({ deployment: "local", tauri: false });
        expect(desktopBridgeAvailable()).toBe(false);
    });

    it("says no in the desktop app against a cloud workspace", () => {
        // There is no local installation here to configure.
        pretendWorkspace({ deployment: "cloud", tauri: true });
        expect(desktopBridgeAvailable()).toBe(false);
    });
});
