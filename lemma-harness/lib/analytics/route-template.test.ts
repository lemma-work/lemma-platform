/**
 * The URL boundary, held honest.
 *
 * posthog-js attaches the current URL to every event it sends, and this app's
 * URLs name pods, agents, flows, conversations and share targets. The normaliser
 * is the only thing between that and a third-party analytics database, so the
 * tests here are adversarial in the same spirit as the backend's
 * `test_analytics_safety.py`.
 */

import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
    ROUTE_TEMPLATES,
    UNMATCHED_ROUTE,
    toRouteTemplate,
    toTemplateUrl,
} from "./route-template";

const APP_DIR = join(__dirname, "..", "..", "app");
const UUID = "0192f1a0-7c3d-7000-8b2a-2f1e4d5c6b7a";

/** Walk `app/` for page routes, mirroring how Next derives URLs: route groups
 *  `(name)` contribute no segment, everything else does. */
function derivedTemplates(dir: string, prefix = ""): string[] {
    const found: string[] = [];
    for (const entry of readdirSync(dir)) {
        const full = join(dir, entry);
        if (entry === "page.tsx") {
            found.push(prefix === "" ? "/" : prefix);
            continue;
        }
        if (!statSync(full).isDirectory()) continue;
        if (entry.startsWith("_")) continue;
        const segment = entry.startsWith("(") && entry.endsWith(")") ? "" : `/${entry}`;
        found.push(...derivedTemplates(full, `${prefix}${segment}`));
    }
    return found;
}

describe("the template list tracks the app", () => {
    it("matches every page route on disk, in both directions", () => {
        // This is what keeps UNMATCHED_ROUTE unreachable in production: a new
        // route fails here rather than silently becoming "/_unmatched" in a
        // dashboard, and a deleted one cannot linger.
        const onDisk = [...new Set(derivedTemplates(APP_DIR))].sort();
        expect(onDisk).toEqual([...ROUTE_TEMPLATES].sort());
    });
});

describe("ids never survive normalisation", () => {
    it.each([
        [`/pod/${UUID}`, "/pod/[id]"],
        [`/pod/${UUID}/agents/${UUID}`, "/pod/[id]/agents/[agentId]"],
        [`/pod/${UUID}/conversations/${UUID}`, "/pod/[id]/conversations/[conversationId]"],
        [`/pod/${UUID}/flows/${UUID}/runs/${UUID}`, "/pod/[id]/flows/[flowId]/runs/[runId]"],
        [`/organizations/${UUID}/settings/members`, "/organizations/[id]/settings/members"],
        [`/invitations/${UUID}/accept`, "/invitations/[invitationId]/accept"],
        [`/templates/some-slug`, "/templates/[slug]"],
    ])("%s -> %s", (concrete, template) => {
        expect(toRouteTemplate(concrete)).toBe(template);
    });

    it("leaves no uuid anywhere in the output", () => {
        const paths = [
            `/pod/${UUID}/flows/${UUID}/runs/${UUID}`,
            `/s/pod/pod/${UUID}`,
            `/organizations/${UUID}/settings/usage`,
        ];
        for (const path of paths) {
            expect(toRouteTemplate(path)).not.toContain(UUID);
        }
    });
});

describe("a literal always beats an id slot", () => {
    // Six routes in this app collide this way. Getting it wrong reads "new" as
    // an agent id and invents a page nobody visited.
    it.each([
        [`/pod/${UUID}/agents/new`, "/pod/[id]/agents/new"],
        [`/pod/${UUID}/assistants/new`, "/pod/[id]/assistants/new"],
        [`/pod/${UUID}/flows/new`, "/pod/[id]/flows/new"],
        [`/pod/${UUID}/functions/new`, "/pod/[id]/functions/new"],
        ["/organizations/new", "/organizations/new"],
        ["/docs/how-lemma-works", "/docs/how-lemma-works"],
    ])("%s -> %s", (concrete, template) => {
        expect(toRouteTemplate(concrete)).toBe(template);
    });
});

describe("catch-alls absorb variable depth", () => {
    it("matches one segment or many", () => {
        expect(toRouteTemplate("/docs/a")).toBe("/docs/[...slug]");
        expect(toRouteTemplate("/docs/a/b/c")).toBe("/docs/[...slug]");
    });

    it("prefers the static sibling over the catch-all", () => {
        expect(toRouteTemplate("/docs")).toBe("/docs");
    });

    it("lets an optional catch-all match its own bare root", () => {
        expect(toRouteTemplate("/auth")).toBe("/auth/[[...path]]");
        expect(toRouteTemplate("/auth/callback/google")).toBe("/auth/[[...path]]");
    });

    it("collapses a share path, which is the most sensitive of all", () => {
        expect(toRouteTemplate(`/s/pod/pod/${UUID}`)).toBe("/s/[kind]/[...path]");
    });
});

describe("unknown paths fail closed", () => {
    it("does not echo a path it does not recognise", () => {
        const unknown = `/pod/${UUID}/some-future-route`;
        expect(toRouteTemplate(unknown)).toBe(UNMATCHED_ROUTE);
        expect(toRouteTemplate(unknown)).not.toContain(UUID);
    });

    it("normalises the root", () => {
        expect(toRouteTemplate("/")).toBe("/");
    });
});

describe("full URLs keep their origin and nothing else", () => {
    it("drops query strings, which carry tokens and filters", () => {
        expect(toTemplateUrl(`https://app.example.com/pod/${UUID}?token=sk-live-abc`)).toBe(
            "https://app.example.com/pod/[id]",
        );
    });

    it("drops fragments", () => {
        expect(toTemplateUrl(`https://app.example.com/pod/${UUID}#section`)).toBe(
            "https://app.example.com/pod/[id]",
        );
    });

    it("returns null for something that is not a URL, so callers can drop it", () => {
        expect(toTemplateUrl("not a url")).toBeNull();
    });
});
