/**
 * Properties of the analytics client that are invisible at runtime.
 *
 * These are source-contract tests, like `lib/desktop/local-deployment.test.ts`:
 * the module cannot be imported under `environment: 'node'` without a DOM, and
 * the things worth guarding here are structural anyway — *where* a call happens,
 * and whether a second one exists. Every regression these catch shipped once
 * already on this branch.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const FRONTEND = join(__dirname, "..", "..");
const read = (relative: string) => readFileSync(join(FRONTEND, relative), "utf8");

describe("posthog-js is never in the shared bundle", () => {
    const source = read("lib/analytics/client.ts");

    it("is loaded dynamically", () => {
        // A static import lands it in the chunk every visitor downloads,
        // including Desktop-local users who will never send an event.
        expect(source).toContain('import("posthog-js")');
    });

    it("has no value-level static import of it", () => {
        const staticImport = /^import\s+(?!type)[^;]*from\s+"posthog-js"/m;
        expect(staticImport.test(source)).toBe(false);
    });
});

describe("one export boundary, not per-call-site filtering", () => {
    const source = read("lib/analytics/client.ts");

    it("routes every event through before_send", () => {
        expect(source).toContain("before_send:");
    });

    it("does not configure the deprecated sanitize_properties", () => {
        // The name appears in the docstring explaining why it is not used, so
        // this checks for the config key rather than the word.
        expect(source).not.toMatch(/sanitize_properties\s*:/);
    });

    it("scrubs the persisted first-touch URLs, not just the live ones", () => {
        // `$initial_current_url` is written to the *person* on identify, so an
        // unscrubbed one outlives every event it came from.
        expect(source).toContain("$initial_current_url");
        expect(source).toContain("$set_once");
    });
});

describe("identity", () => {
    const source = read("lib/analytics/client.ts");

    it("identifies before grouping", () => {
        // With `identified_only`, group() also triggers person processing, so
        // grouping first creates a person keyed to the anonymous id.
        const identify = source.indexOf("ph.identify(");
        const group = source.indexOf('ph.group("organization"');
        expect(identify).toBeGreaterThan(-1);
        expect(group).toBeGreaterThan(identify);
    });

    it("clears groups before re-applying them", () => {
        // posthog-js has no "leave this group": without a reset, every event
        // after leaving a pod stays attributed to the last pod visited.
        const reset = source.indexOf("resetGroups()");
        const group = source.indexOf('ph.group("organization"');
        expect(reset).toBeGreaterThan(-1);
        expect(group).toBeGreaterThan(reset);
    });

    it("is reset on every sign-out path", () => {
        // Two paths exist: the app's, and the auth portal's, which bypasses
        // `logoutToHome` entirely. Missing either leaks identity across accounts
        // on a shared browser.
        expect(read("lib/auth/logout.ts")).toContain("resetAnalyticsIdentity");
        expect(read("components/auth/portal/auth-portal.tsx")).toContain(
            "resetAnalyticsIdentity",
        );
    });
});

describe("mount points", () => {
    const providers = read("app/providers.tsx");

    it("keeps init and pageviews outside the org provider", () => {
        // The auth routes are the top of the funnel; they render without
        // OrganizationProvider, and moving analytics inside it would lose them.
        const analytics = providers.indexOf("<AnalyticsProvider />");
        const orgOpen = providers.indexOf("<OrganizationProvider>");
        expect(analytics).toBeGreaterThan(-1);
        expect(analytics).toBeGreaterThan(orgOpen === -1 ? -1 : orgOpen);
        expect(providers.indexOf("<AnalyticsIdentity />")).toBeGreaterThan(orgOpen);
    });

    it("puts identity inside the org provider, where the org id exists", () => {
        const orgOpen = providers.indexOf("<OrganizationProvider>");
        const orgClose = providers.indexOf("</OrganizationProvider>");
        const identity = providers.indexOf("<AnalyticsIdentity />");
        expect(identity).toBeGreaterThan(orgOpen);
        expect(identity).toBeLessThan(orgClose);
    });
});

describe("consent", () => {
    it("starts in memory and upgrades only on acceptance", () => {
        const source = read("lib/analytics/client.ts");
        expect(source).toContain('hasAnalyticsConsent() ? "localStorage+cookie" : "memory"');
    });

    it("never asks where analytics does not run", () => {
        const banner = read("components/analytics/consent-banner.tsx");
        expect(banner).toContain("isLocalDeployment()");
        expect(banner).toContain("config.ANALYTICS_KEY");
    });

    it("is described on the privacy page, by vendor name", () => {
        const legal = read("lib/data/legal.ts");
        expect(legal).toContain("PostHog");
        expect(legal).toContain("Product Analytics");
    });
});
