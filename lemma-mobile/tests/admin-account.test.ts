import { afterEach, describe, expect, it, vi } from "vitest";
import { accountLive } from "../src/areas/admin/account/live";
import { accountSample } from "../src/areas/admin/account/sample";
import type { LiveContext } from "../src/areas/context";
import { deletionRefusal } from "@web/session/account-deletion";
import { clock, isVerifiedNumber, phoneProblems, reasonOf, secondsLeft, unknownSender, verifyBlocker } from "../src/areas/admin/account/verify";
import { changes, draftOf } from "@web/session/profile-edit";
import { dayToDate, isoDay, sameZone, spokenDay, zoneChoices, zoneCity, zoneMatches, zoneRegion } from "../src/areas/admin/account/zones";
import { LINK_HINT, botToOpen, linkButton, linkedRow, telegramShown } from "../src/areas/admin/account/link-telegram";
import { botDetail, botTitle, type BotChoice } from "@web/session/telegram-bots";

/** Your profile (PARITY.md, F): the time zones offered, the dates, and
 *  proving a mobile number over WhatsApp. */

describe("time zones", () => {
    it("offers the phone's zone first, keeps the one held, and lists each once", () => {
        const list = zoneChoices("Asia/Kolkata", "Europe/London");
        expect(list.slice(0, 2)).toEqual(["Asia/Kolkata", "Europe/London"]);
        expect(list.filter((zone) => zone === "Europe/London")).toHaveLength(1);
        expect(zoneChoices("", "", ["UTC", "Etc/GMT+5"])).toEqual(["UTC", "Etc/GMT+5"]);
        expect(zoneChoices("Asia/Calcutta", "", ["UTC"])).toEqual(["Asia/Calcutta", "UTC"]);
        expect(zoneChoices("UTC", "UTC").filter((zone) => zone === "UTC")).toHaveLength(1);
    });

    it("names a zone by its city, and finds it by city or region", () => {
        expect(zoneCity("America/Argentina/Buenos_Aires")).toBe("Buenos Aires");
        expect(zoneRegion("America/Argentina/Buenos_Aires")).toBe("America · Argentina");
        expect(zoneRegion("UTC")).toBe("");
        expect(zoneMatches("America/Los_Angeles", "los ang")).toBe(true);
        expect(zoneMatches("America/Los_Angeles", "europe")).toBe(false);
        expect(zoneMatches("Europe/London", "")).toBe(true);
    });
});

describe("dates", () => {
    it("keeps a day as the profile stores it, in the phone's own day", () => {
        expect(isoDay(new Date(1990, 3, 23, 12))).toBe("1990-04-23");
        expect(spokenDay("1990-04-23")).toBe("23 April 1990");
        expect(spokenDay("")).toBe("");
        expect(isoDay(dayToDate("1990-04-23")!)).toBe("1990-04-23");
        expect(dayToDate("not a day")).toBeNull();
    });
});

describe("the profile", () => {
    it("sends only what changed, and a cleared time zone as empty", () => {
        const before = draftOf({ first_name: "Ada", last_name: "Lovelace", timezone: "Europe/London" } as never);
        expect(changes(before, { ...before, first_name: "Ada " })).toEqual({});
        expect(changes(before, { ...before, timezone: "" })).toEqual({ timezone: "" });
        expect(changes(before, { ...before, country: "United Kingdom" })).toEqual({ country: "United Kingdom" });
    });

    it("says its problems for a phone", () => {
        expect(phoneProblems({ timezone: "That is not a timezone this browser knows." }).timezone).toBe("That is not a time zone this phone knows.");
        expect(phoneProblems({})).toEqual({});
    });
});

describe("verifying a mobile number", () => {
    afterEach(() => { vi.useRealTimers(); });

    it("counts down and says why it cannot start", () => {
        expect(clock(245)).toBe("4:05");
        expect(secondsLeft("2026-10-24T10:01:00Z", Date.parse("2026-10-24T10:00:00Z"))).toBe(60);
        expect(secondsLeft("2026-10-24T09:00:00Z", Date.parse("2026-10-24T10:00:00Z"))).toBe(0);
        expect(verifyBlocker("")).toBe("Enter a mobile number first.");
        expect(verifyBlocker("07700 900123")).toBe("Add the country code, starting with +, before verifying.");
        expect(verifyBlocker("+44 7700 900123")).toBeNull();
    });

    it("trusts the stamp only for the number stored", () => {
        const user = { mobile_number: "447700900123", mobile_verified_at: "2026-10-01T00:00:00Z" };
        expect(isVerifiedNumber(user, "+44 7700 900123")).toBe(true);
        expect(isVerifiedNumber(user, "+44 7700 900124")).toBe(false);
        expect(isVerifiedNumber({ mobile_number: "447700900123", mobile_verified_at: null }, "+447700900123")).toBe(false);
        expect(unknownSender({ mobile_number: null })).toBe(true);
        expect(unknownSender({ mobile_number: "+447700900123" })).toBe(false);
        expect(unknownSender(null)).toBe(false);
    });

    it("says the server's reason, never its JSON", () => {
        expect(reasonOf(new Error('{"detail":"This mobile number is already in use"}'), "x")).toBe("Somebody else's profile already has this number.");
        expect(reasonOf(new Error('{"nope":1}'), "Fallback.")).toBe("Fallback.");
        expect(reasonOf(new Error("Too many mobile verification attempts"), "x")).toBe("Too many tries for now. Wait a while, then start again.");
        expect(reasonOf(new Error("Network request failed"), "Fallback.")).toBe("Fallback.");
    });

    it("is acted out in the sample: a number somebody holds is refused, and the code arrives", async () => {
        vi.useFakeTimers();
        const sample = accountSample();
        const refused = sample.startWhatsAppVerification("+1 555-0177");
        const check = expect(refused).rejects.toThrow(/already in use/);
        await vi.runAllTimersAsync();
        await check;

        const started = sample.startWhatsAppVerification("+44 7700 900123");
        await vi.runAllTimersAsync();
        const transaction = await started;
        expect(transaction.whatsappUrl).toContain("LEMMA%20VERIFY%20");
        const early = sample.verificationStatus(transaction.id);
        await vi.advanceTimersByTimeAsync(500);
        expect(await early).toBe("PENDING");
        await vi.advanceTimersByTimeAsync(10_000);
        const late = sample.verificationStatus(transaction.id);
        await vi.advanceTimersByTimeAsync(500);
        expect(await late).toBe("VERIFIED");
        const profile = sample.profile();
        await vi.runAllTimersAsync();
        expect((await profile).mobile_number).toBe("+447700900123");
    });
});

describe("renamed time zones", () => {
    it("takes an old name and its new one for the same clock", () => {
        expect(sameZone("Asia/Calcutta", "Asia/Kolkata")).toBe(true);
        expect(sameZone("Europe/Kyiv", "Europe/Kiev")).toBe(true);
        expect(sameZone("Asia/Kolkata", "Asia/Dhaka")).toBe(false);
        expect(zoneChoices("Asia/Kolkata", "Asia/Calcutta", ["Asia/Calcutta", "Asia/Dhaka"])).toEqual(["Asia/Kolkata", "Asia/Dhaka"]);
    });
});

describe("deleting the account", () => {
    afterEach(() => { vi.useRealTimers(); });

    it("sends the address as typed to the API's own call", async () => {
        const sent: unknown[] = [];
        const client = { users: { deleteAccount: async (payload: unknown) => { sent.push(payload); return { deleted: true }; } } };
        await accountLive({ client: () => client } as unknown as LiveContext).deleteAccount("ada@acme.example");
        expect(sent).toEqual([{ confirm_email: "ada@acme.example" }]);
    });

    it("hands the API's refusal on as it came, for the web's words to read", async () => {
        const refusal = { statusCode: 409, code: "ACCOUNT_DELETION_SOLE_OWNER", message: "Make someone else an owner of Acme first.", details: { organizations: [{ id: "o1", name: "Acme" }] } };
        const client = { users: { deleteAccount: async () => { throw refusal; } } };
        const failed = await accountLive({ client: () => client } as unknown as LiveContext).deleteAccount("ada@acme.example").catch((problem: unknown) => problem);
        expect(deletionRefusal(failed)).toEqual({ message: "Make someone else an owner of Acme first.", organizations: ["Acme"] });
    });

    it("is refused in sample data, in words the screen shows", async () => {
        vi.useFakeTimers();
        const asked = accountSample().deleteAccount("ada@acme.example").catch((problem: unknown) => problem);
        await vi.runAllTimersAsync();
        expect(deletionRefusal(await asked)).toMatchObject({ message: "Sample data has no account to delete. Sign in to delete yours." });
    });
});

describe("Telegram on the profile", () => {
    afterEach(() => { vi.useRealTimers(); });

    const choice = (surfaceId: string, shared: boolean, spaces: string[], handle = "@example_bot"): BotChoice =>
        ({ surfaceId, podId: "pod-" + surfaceId, podName: spaces[0], handle, shared, spaces });

    it("shows nothing until the accounts are read, then only with a bot, an account or a failure", () => {
        expect(telegramShown({ pending: true, unread: false, bots: 2, linked: 1 })).toBe(false);
        expect(telegramShown({ pending: false, unread: false, bots: 0, linked: 0 })).toBe(false);
        expect(telegramShown({ pending: false, unread: false, bots: 1, linked: 0 })).toBe(true);
        expect(telegramShown({ pending: false, unread: false, bots: 0, linked: 1 })).toBe(true);
        expect(telegramShown({ pending: false, unread: true, bots: 0, linked: 0 })).toBe(true);
    });

    it("says each linked account on one line, and the button as there are some", () => {
        const now = new Date(2026, 10, 10, 15, 0);
        expect(linkedRow({ id: "a", name: "Ada L.", provedAt: new Date(2026, 10, 10, 9, 0).toISOString() }, now)).toEqual({ label: "Ada L.", value: "linked today" });
        expect(linkedRow({ id: "b", name: null, provedAt: null }, now)).toEqual({ label: "A Telegram account", value: null });
        expect(linkButton(0)).toBe("Link Telegram");
        expect(linkButton(2)).toBe("Link another account");
        expect(LINK_HINT).not.toMatch(/\b(space|channel|line|place)s?\b/i);
    });

    it("goes straight to the link with one bot, and otherwise to the one picked, the first until then", () => {
        const shared = choice("s-shared", true, ["Support", "Renewals"]);
        const own = choice("s-own", false, ["Launch"], "@launch_example_bot");
        expect(botToOpen([own], null, null)).toEqual({ through: own, selected: own });
        expect(botToOpen([shared, own], null, null)).toEqual({ through: null, selected: shared });
        expect(botToOpen([shared, own], "s-own", null)).toEqual({ through: null, selected: own });
        expect(botToOpen([shared, own], "s-own", own).through).toBe(own);
        expect(botToOpen([], null, null)).toEqual({ through: null, selected: null });
    });

    it("reads the bots live from /surfaces/me and each Lemma's own list, past a Lemma that will not say", async () => {
        const client = (podId?: string) => ({
            userSurfaces: {
                list: async () => ({ groups: [{ platform: "TELEGRAM", default_surface_id: null, surfaces: [{ id: "tg-a", pod_id: "pod-a" }, { id: "tg-b", pod_id: "pod-b" }, { id: "tg-c", pod_id: "pod-c" }] }] }),
                telegramAccounts: async () => ({ items: [{ id: "acct-1", name: "Ada L.", proved_at: "2026-11-10T09:00:00Z" }] }),
            },
            pods: { get: async () => ({ name: podId === "pod-a" ? "Support" : "Launch" }) },
            podSurfaces: {
                list: async (pod: string, options: { platform?: string }) => {
                    expect(options.platform).toBe("TELEGRAM");
                    if (pod === "pod-c") throw new Error("Not yours");
                    return pod === "pod-a"
                        ? { items: [{ id: "tg-a", status: "ACTIVE", reach: { handle: "@lemma_example_bot" } }] }
                        : { items: [{ id: "tg-b", status: "ACTIVE", credential_mode: "CUSTOM", surface_identity_username: "@launch_example_bot" }] };
                },
            },
        });
        const account = accountLive({ client } as unknown as LiveContext);
        const mine = await account.myTelegramSurfaces();
        expect(mine).toEqual({ surfaceIds: ["tg-a", "tg-b", "tg-c"], podIds: ["pod-a", "pod-b", "pod-c"], preferred: null });
        const bots = await account.telegramBots(mine);
        expect(bots.map((one) => [botTitle(one), botDetail(one)])).toEqual([
            ["Lemma’s bot", "@lemma_example_bot · Support"],
            ["@launch_example_bot", "Launch’s own bot"],
        ]);
        expect(await account.telegramAccounts()).toEqual([{ id: "acct-1", name: "Ada L.", provedAt: "2026-11-10T09:00:00Z" }]);
    });

    it("unlinks through the API's own call", async () => {
        const sent: string[] = [];
        const client = { userSurfaces: { unlinkTelegramAccount: async (id: string) => { sent.push(id); } } };
        await accountLive({ client: () => client } as unknown as LiveContext).unlinkTelegramAccount("acct-1");
        expect(sent).toEqual(["acct-1"]);
    });

    it("is acted out in the sample: a bot to pick, and an old account to unlink", async () => {
        vi.useFakeTimers();
        const sample = accountSample();
        const read = async <T,>(asked: Promise<T>) => { await vi.runAllTimersAsync(); return asked; };
        const mine = await read(sample.myTelegramSurfaces());
        const bots = await read(sample.telegramBots(mine));
        expect(bots.map((one) => [botTitle(one), botDetail(one)])).toEqual([
            ["Lemma’s bot", "@lemma_sample_bot · Support and Renewals"],
            ["@launch_acme_bot", "Launch’s own bot"],
        ]);
        const accounts = await read(sample.telegramAccounts());
        expect(accounts.map((one) => one.name)).toEqual(["Ada (old phone)"]);
        await read(sample.unlinkTelegramAccount(accounts[0].id));
        expect(await read(sample.telegramAccounts())).toEqual([]);
        const again = sample.unlinkTelegramAccount(accounts[0].id).catch((problem: unknown) => problem);
        expect(await read(again)).toMatchObject({ statusCode: 404 });
    });
});
