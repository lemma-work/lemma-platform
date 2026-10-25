import { afterEach, describe, expect, it, vi } from "vitest";
import { accountSample } from "../src/areas/admin/account/sample";
import { clock, isVerifiedNumber, phoneProblems, reasonOf, secondsLeft, unknownSender, verifyBlocker } from "../src/areas/admin/account/verify";
import { changes, draftOf } from "@web/session/profile-edit";
import { dayToDate, isoDay, sameZone, spokenDay, zoneChoices, zoneCity, zoneMatches, zoneRegion } from "../src/areas/admin/account/zones";

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
