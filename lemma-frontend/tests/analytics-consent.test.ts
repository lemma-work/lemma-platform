import assert from "node:assert/strict";
import { test } from "node:test";

import { ASKING_SECTIONS, asksForConsent } from "../src/site/analytics/consent.ts";
import { ROUTE_TEMPLATES } from "../src/site/analytics/route-template.ts";

test("the public site asks about analytics storage", () => {
    for (const path of [
        "/",
        "/blog",
        "/blog/a-post",
        "/docs",
        "/docs/pods/tables/",
        "/product/apps",
        "/templates/crm",
        "/privacy",
        "/download",
        "/s/app/pod-1/crm",
        "/d/abc123",
        "/import/github/acme/crm",
    ]) {
        assert.equal(asksForConsent(path), true, path);
    }
});

test("sign-in, the hand-offs either side of it, and the workspace do not", () => {
    for (const path of [
        "/auth",
        "/auth/sign-in",
        "/auth/authorize",
        "/oauth/complete",
        "/invitations/inv-1/accept",
        "/slack/installed",
        "/sign-in-to-site/c-1/call-1",
        "/logout",
        "/connect",
        "/remix",
        "/create-pod",
        "/organizations/new",
        "/t",
        "/t/pod-1/conversation",
        "/o",
        "/demo/launch",
        "/an-old-address",
    ]) {
        assert.equal(asksForConsent(path), false, path);
    }
});

test("every section that asks is one the app has", () => {
    /* A renamed section would quietly stop asking. Safe, but nobody would notice. */
    const sections = new Set(ROUTE_TEMPLATES.map((route) => route.split("/")[1]));
    for (const section of ASKING_SECTIONS) assert.ok(sections.has(section), JSON.stringify(section));
});
