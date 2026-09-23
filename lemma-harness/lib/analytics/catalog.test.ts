/**
 * The client catalog is only worth having if it agrees with the backend one.
 *
 * Two emitters sharing an event *name* while disagreeing about its shape is
 * worse than two names: the dashboard looks right and the property is missing
 * from half the rows. That already happened twice on this branch before the
 * catalogs existed, which is why the cross-language check below is worth its
 * awkwardness.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
    CLIENT_CATALOG,
    KNOWN_UNEMITTED,
    allowedProperties,
    isCatalogued,
    type WebAnalyticEvent,
} from "./catalog";

const BACKEND_CATALOG = join(
    __dirname,
    "..",
    "..",
    "..",
    "lemma-backend",
    "app",
    "core",
    "analytics",
    "event_catalog.py",
);

/** Pull `properties=frozenset({...})` for one event out of the Python catalog. */
function backendProperties(event: string): string[] {
    const source = readFileSync(BACKEND_CATALOG, "utf8");
    const entry = new RegExp(
        `"${event.replace(".", "\\.")}":\\s*AnalyticEvent\\(([\\s\\S]*?)\\n    \\),`,
    ).exec(source);
    expect(entry, `no backend catalog entry for ${event}`).not.toBeNull();
    const props = /properties=frozenset\(\{([\s\S]*?)\}\)/.exec(entry![1]);
    if (!props) return [];
    return [...props[1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1]).sort();
}

describe("shared events have identical shapes on both sides", () => {
    // Only the names both emitters raise. The rest of each catalog is its own.
    it.each(["share_link.viewed", "import.started"])("%s", (event) => {
        const client = [...CLIENT_CATALOG[event as WebAnalyticEvent].properties].sort();
        expect(client).toEqual(backendProperties(event));
    });
});

describe("default-deny", () => {
    it("recognises catalogued names and nothing else", () => {
        expect(isCatalogued("share_link.viewed")).toBe(true);
        expect(isCatalogued("pod.exfiltrated")).toBe(false);
    });

    it("drops a property the event did not declare", () => {
        const kept = allowedProperties("share_link.viewed", {
            kind: "pod",
            viewer_is_member: true,
            pod_name: "<script>alert(1)</script>",
            email: "someone@customer.example",
        });
        expect(kept).toEqual({ kind: "pod", viewer_is_member: true });
    });

    it("keeps the spine the client attaches to everything", () => {
        const kept = allowedProperties("pod.create_started", { deployment: "hosted" });
        expect(kept).toEqual({ deployment: "hosted" });
    });
});

describe("declared events are emitted or named as gaps", () => {
    it("accounts for every entry", () => {
        // Same ratchet as the backend's KNOWN_GAPS: a declared event nothing
        // raises is a permanently-zero dashboard, and the bad version is the one
        // nobody knows about.
        // `client.error` is raised by app/global-error.tsx, the root error
        // boundary — the only place that can see an error which escaped
        // everything below it.
        const emitted = [
            "share_link.viewed",
            // Both raised by app/s/[kind]/[...path]/contact-landing.tsx.
            "share_link.contact_opened",
            "share_link.contact_saved",
            "import.started",
            "client.error",
            // lib/analytics/onboarding.ts is the only emitter of these five.
            "onboarding.step_viewed",
            "onboarding.pod_ready",
            "activation.surface_connected",
            "activation.app_opened",
            "activation.member_joined",
        ];
        const accounted = new Set([...emitted, ...KNOWN_UNEMITTED]);
        const unaccounted = Object.keys(CLIENT_CATALOG).filter((n) => !accounted.has(n as never));
        expect(unaccounted).toEqual([]);
    });

    it("does not list gaps that left the catalog", () => {
        for (const name of KNOWN_UNEMITTED) {
            expect(isCatalogued(name)).toBe(true);
        }
    });
});
