import { afterEach, describe, expect, it, vi } from "vitest";
import { SAMPLE_LEMMAS } from "../src/data/sample";
import { accountSample } from "../src/areas/admin/account/sample";
import { isForbidden } from "../src/areas/chats/model";
import { chatsSample } from "../src/areas/chats/sample";
import { grantableNames, ownColumns } from "../src/areas/chats/wire";
import { accountSaid, linkStep, readIdentityLinkStatus } from "@web/channels/identity-link-model";
import { GIVE_REFUSED, grantSaid, readTableGrant } from "@web/channels/table-grants-model";

/** Groups (PARITY.md, E): proving a Telegram account from a group's people
 *  ("This is me"), and giving a group a table. The decisions are the web's
 *  own modules; these hold what the phone reads live to the API's shapes,
 *  and the sample to acting each one out. (`chats/live.ts` loads Expo's
 *  native modules, so it is not run here.) */

const [, LAUNCH] = SAMPLE_LEMMAS;

describe("proving a Telegram account, in sample data", () => {
    afterEach(() => { vi.useRealTimers(); });

    it("opens a code from Ada's account a few seconds after it is made, and confirming makes her the one who spoke", async () => {
        vi.useFakeTimers();
        const chats = chatsSample();
        const before = await chats.get(LAUNCH.id, "press-friends");
        const contact = before.members.find((one) => one.person.kind === "CONTACT" && one.person.name === "Ada L.");
        expect(contact?.person.id).toBeTruthy();
        expect(before.places.find((place) => place.platform === "TELEGRAM")?.surfaceId).toBeTruthy();

        const link = await chats.identityLink(LAUNCH.id, "surface-telegram", contact!.person.id!);
        expect(link.url).not.toContain("t.me");
        expect(linkStep(await chats.identityLinkStatus(LAUNCH.id, link.code), false)).toBe("open");
        await expect(chats.confirmIdentityLink(LAUNCH.id, link.code)).rejects.toThrow(/expired/);

        vi.advanceTimersByTime(5_000);
        const opened = await chats.identityLinkStatus(LAUNCH.id, link.code);
        expect(opened).toEqual({ state: "OPENED", account: { name: "Ada L.", username: "ada_l" } });
        expect(await chats.confirmIdentityLink(LAUNCH.id, link.code)).toEqual({ name: "Ada L.", username: "ada_l" });

        const after = await chats.get(LAUNCH.id, "press-friends");
        expect(after.members.some((one) => one.person.id === contact!.person.id)).toBe(false);
        expect(after.members.filter((one) => one.person.kind === "MEMBER" && one.person.id === "me")).toHaveLength(1);
        const lines = await chats.lines(LAUNCH.id, "press-friends");
        expect(lines.items.find((line) => line.text?.startsWith("Dropping the press kit"))?.sender).toMatchObject({ kind: "MEMBER", id: "me" });
        expect(await chats.identityLinkStatus(LAUNCH.id, link.code)).toEqual({ state: "GONE" });

        /* The profile lists it beside the old phone's, once. */
        const accounts = accountSample().telegramAccounts();
        await vi.runAllTimersAsync();
        expect((await accounts).map((one) => one.name)).toContain("Ada L.");
    });

    it("says a code made for somebody else in the group was opened from another account, and links nothing", async () => {
        vi.useFakeTimers();
        const chats = chatsSample();
        const link = await chats.identityLink(LAUNCH.id, "surface-telegram", "contact-ines");
        vi.advanceTimersByTime(5_000);
        expect(linkStep(await chats.identityLinkStatus(LAUNCH.id, link.code), false)).toBe("other-account");
        expect(linkStep(await chats.identityLinkStatus(LAUNCH.id, link.code), false)).toBe("other-account");
        await expect(chats.confirmIdentityLink(LAUNCH.id, link.code)).rejects.toThrow();
        const group = await chats.get(LAUNCH.id, "press-friends");
        expect(group.members.some((one) => one.person.id === "contact-ines")).toBe(true);
    });

    it("spends a code its account was declined on", async () => {
        vi.useFakeTimers();
        const chats = chatsSample();
        const link = await chats.identityLink(LAUNCH.id, "surface-telegram");
        vi.advanceTimersByTime(5_000);
        expect(linkStep(await chats.identityLinkStatus(LAUNCH.id, link.code), false)).toBe("confirm");
        await chats.declineIdentityLink(LAUNCH.id, link.code);
        expect(linkStep(await chats.identityLinkStatus(LAUNCH.id, link.code), false)).toBe("gone");
    });
});

describe("proving a Telegram account, as the server says it", () => {
    it("reads the server's answers with the web's own readers, and names the account the web's way", () => {
        expect(readIdentityLinkStatus({ state: "OPENED", account: null })).toEqual({ state: "GONE" });
        expect(readIdentityLinkStatus({ state: "OPENED", account: { name: "Ada L.", username: "ada_l" } })).toEqual({ state: "OPENED", account: { name: "Ada L.", username: "ada_l" } });
        expect(accountSaid({ name: "Ada L.", username: "ada_l" })).toBe("Ada L. (@ada_l)");
    });
});

describe("giving a group a table", () => {
    it("is acted out in sample data: one given, one to give, and one taken back", async () => {
        const chats = chatsSample();
        const given = await chats.tableGrants(LAUNCH.id, "press-friends");
        expect(given.map((one) => [one.table, grantSaid(one)])).toEqual([["releases", "read title, shipped_at, version"]]);
        expect(await chats.grantableTables(LAUNCH.id)).toEqual(["launch_checklist", "releases"]);
        expect(await chats.grantableColumns(LAUNCH.id, "launch_checklist")).toEqual(["task", "owner", "due", "done"]);

        await chats.setTableGrant(LAUNCH.id, "press-friends", "launch_checklist", { readColumns: ["task", "due"], addColumns: ["task"] });
        await chats.removeTableGrant(LAUNCH.id, "press-friends", "releases");
        const now = await chats.tableGrants(LAUNCH.id, "press-friends");
        expect(now.map((one) => [one.table, grantSaid(one)])).toEqual([["launch_checklist", "read task, due · add a row with task"]]);
    });

    it("refuses a table the reader may not give, the way the server does", async () => {
        const chats = chatsSample();
        const refused = await chats.setTableGrant(LAUNCH.id, "press-friends", "invoices", { readColumns: ["total"], addColumns: [] }).catch((problem: unknown) => problem);
        expect(isForbidden(refused)).toBe(true);
        expect(GIVE_REFUSED).toMatch(/owners or an admin/);
    });

    it("offers live only tables read whole, and only a table's own columns", () => {
        expect(grantableNames([{ name: "orders" }, { name: "notes", enable_rls: true }, { name: "stock", enable_rls: false }])).toEqual(["orders", "stock"]);
        expect(ownColumns([{ name: "id", system: true }, { name: "sku" }, { name: "count", system: false }])).toEqual(["sku", "count"]);
        expect(readTableGrant({ table: "orders", read_columns: ["item"], add_columns: [] })).toEqual({ table: "orders", readColumns: ["item"], addColumns: [] });
    });
});
