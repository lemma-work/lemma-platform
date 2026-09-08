// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Naming the client is what makes WEB and DESKTOP reachable as traffic
 * origins; unnamed, every human's request lands under SDK, indistinguishable
 * from somebody's script.
 *
 * The name was chosen with `desktopBridgeAvailable()`, which also requires the
 * deployment to be local because it gates the *privileged local* commands. A
 * cloud workspace opened in the desktop app therefore reported itself as a web
 * visitor, and DESKTOP counted only the subset of desktop users who had chosen
 * Local Lemma.
 */

const constructed = vi.hoisted(() => ({ options: [] as Array<Record<string, unknown>> }));
vi.mock("lemma-sdk", () => ({
    LemmaClient: class {
        constructor(options: Record<string, unknown>) {
            constructed.options.push(options);
        }
        withPod() {
            return this;
        }
    },
}));

const deployment = vi.hoisted(() => ({ local: false }));
vi.mock("@/lib/config", () => ({
    config: {
        API_URL: "https://api.test",
        AUTH_URL: "https://auth.test",
        SITE_URL: "https://site.test",
    },
    isLocalDeployment: () => deployment.local,
}));

async function clientName({ shell, local }: { shell: boolean; local: boolean }) {
    constructed.options.length = 0;
    deployment.local = local;
    if (shell) {
        (window as unknown as { __TAURI__: unknown }).__TAURI__ = { core: { invoke: () => {} } };
    } else {
        delete (window as unknown as { __TAURI__?: unknown }).__TAURI__;
    }
    // The module caches its client, so each case needs its own instance.
    vi.resetModules();
    const { getLemmaClient } = await import("@/lib/sdk/lemma-client");
    getLemmaClient();
    return constructed.options.at(0)?.client;
}

beforeEach(() => {
    vi.resetModules();
});

afterEach(() => {
    delete (window as unknown as { __TAURI__?: unknown }).__TAURI__;
});

describe("who the SDK says it is", () => {
    it("counts a cloud workspace in the desktop app as the desktop app", async () => {
        expect(await clientName({ shell: true, local: false })).toBe("lemma-desktop");
    });

    it("counts a local workspace in the desktop app as the desktop app", async () => {
        expect(await clientName({ shell: true, local: true })).toBe("lemma-desktop");
    });

    it("counts a browser as the web, even against a local deployment", async () => {
        // A phone on the same Wi-Fi, or someone holding a public link. Local
        // deployment, no shell: still a web visitor.
        expect(await clientName({ shell: false, local: true })).toBe("lemma-web");
        expect(await clientName({ shell: false, local: false })).toBe("lemma-web");
    });
});
