import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { LINK_HOSTS, LINK_WAIT_MS, appPathOf, isLinkPath, linkArrived, rememberLink, takeWaitingLink } from "../src/areas/more/links";

/** Links from outside (PARITY.md, G): the web's addresses open the phone's
 *  own screens, and a link that came while signed out is kept. */

describe("where a link opens", () => {
    it("drops the web's host, and reads lemma:// the same way", () => {
        expect(appPathOf("https://lemma.work/invitations/inv_123/accept")).toBe("/invitations/inv_123/accept");
        expect(appPathOf("lemma://invitations/inv_123/reject")).toBe("/invitations/inv_123/reject");
        expect(appPathOf("https://www.lemma.work/import/github/acme/frontdesk")).toBe("/import/github/acme/frontdesk");
        expect(appPathOf("https://www.lemma.work/remix?source=https%3A%2F%2Fapp.example.com")).toBe("/remix?source=https%3A%2F%2Fapp.example.com");
        expect(appPathOf("https://lemma.work/sign-in-to-site/conv-1/call-2")).toBe("/sign-in-to-site/conv-1/call-2");
        expect(appPathOf("https://lemma.work/templates/frontdesk")).toBe("/templates");
    });

    it("opens the place an app address names, at the phone's own path for it", () => {
        expect(appPathOf("https://lemma.work/home")).toBe("/");
        expect(appPathOf("https://lemma.work/needs")).toBe("/needs");
        expect(appPathOf("https://lemma.work/chats")).toBe("/chats");
        expect(appPathOf("https://lemma.work/people")).toBe("/settings/people");
        expect(appPathOf("https://lemma.work/l/pod-1")).toBe("/lemma/pod-1");
        expect(appPathOf("https://lemma.work/l/pod-1/chats/c1")).toBe("/chat/pod-1/c1");
        expect(appPathOf("https://lemma.work/l/pod-1/groups/g1")).toBe("/shared/pod-1/g1");
        expect(appPathOf("https://lemma.work/l/pod-1/apps/crm")).toBe("/app/pod-1/crm");
        expect(appPathOf("https://lemma.work/l/pod-1/tables/tickets/r1")).toBe("/lemma/pod-1/record/tickets/r1");
        expect(appPathOf("https://lemma.work/l/pod-1/automations/waiting")).toBe("/lemma/pod-1/section/runs?view=waiting");
    });

    it("reads the older addresses too: the frame's first, and the workspace's the server still sends", () => {
        expect(appPathOf("https://lemma.work/t")).toBe("/");
        expect(appPathOf("https://lemma.work/t/needs-you")).toBe("/needs");
        expect(appPathOf("https://lemma.work/t/chats/pod-1/c/c9")).toBe("/chat/pod-1/c9");
        expect(appPathOf("https://lemma.work/t/pod-1")).toBe("/lemma/pod-1");
        expect(appPathOf("https://lemma.work/t/pod-1/table/tickets")).toBe("/lemma/pod-1/table/tickets");
        expect(appPathOf("lemma://t/pod-1/conversation/abc")).toBe("/chat/pod-1/abc");
        expect(appPathOf("https://lemma.work/t/pod-1/channel/ch1")).toBe("/shared/pod-1/ch1");
    });

    it("leaves alone what it does not know, and anybody else's host", () => {
        expect(appPathOf("https://example.com/invitations/x/accept")).toBeNull();
        expect(appPathOf("lemma://chat/pod/abc")).toBeNull();
        expect(appPathOf("https://lemma.work/invitations/x/maybe")).toBeNull();
        expect(appPathOf("https://lemma.work/invitations/../accept")).toBeNull();
        expect(appPathOf("https://lemma.work/blog/post")).toBeNull();
    });

    it("knows the hosts app.json hands the app", () => {
        const app = JSON.parse(readFileSync(fileURLToPath(new URL("../app.json", import.meta.url)), "utf8")) as { expo: { android: { intentFilters: { data: { host: string; pathPrefix: string }[] }[] } } };
        const declared = new Set(app.expo.android.intentFilters.flatMap((filter) => filter.data.map((one) => one.host)));
        for (const host of declared) expect(LINK_HOSTS).toContain(host);
        for (const one of app.expo.android.intentFilters[0].data) {
            const example: Record<string, string> = {
                "/invitations": "/inv_1/accept", "/import/github": "/acme/frontdesk", "/remix": "?source=x", "/templates": "", "/t/": "pod-1",
                "/sign-in-to-site": "/conv/call", "/l/": "pod-1/chats/c1", "/home": "", "/needs": "", "/apps": "", "/chats": "", "/people": "", "/connectors": "",
            };
            expect(one.pathPrefix in example).toBe(true);
            expect(appPathOf(`https://${one.host}${one.pathPrefix}${example[one.pathPrefix]}`)).not.toBeNull();
        }
    });
});

describe("a link while signed out", () => {
    it("waits for its screen, and opens once", () => {
        rememberLink("/invitations/inv_1/accept", 1000);
        expect(takeWaitingLink(2000)).toBe("/invitations/inv_1/accept");
        expect(takeWaitingLink(2000)).toBeNull();
    });

    it("is crossed off when the router opened it itself", () => {
        rememberLink("/remix?source=x", 1000);
        linkArrived("/remix");
        expect(takeWaitingLink(1500)).toBeNull();
    });

    it("lapses, and keeps only links a person follows", () => {
        rememberLink("/lemma/pod-1", 0);
        expect(takeWaitingLink(LINK_WAIT_MS + 1)).toBeNull();
        expect(isLinkPath("/account")).toBe(false);
        rememberLink("/account", 0);
        expect(takeWaitingLink(1)).toBeNull();
    });
});
