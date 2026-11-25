import { describe, expect, it } from "vitest";
import { arrivalOf, askingSaid, askingText, faceIdOf, roomFaces, saidOn, snapAfter, snapOffset, stripOf, withoutQuery } from "../src/areas/chats/room";
import { sayReason } from "@web/channels/deliveries";
import { mentionsIn, type Mentionable } from "@web/channels/mentions";
import type { LineDelivery, Member } from "@web/channels/model";

const LEMMA = { id: "pod-support" };
const member = (kind: Member["person"]["kind"], id: string, name: string): Member => ({ person: { kind, id, name }, role: "member", answers: kind === "AGENT" ? "MENTIONED" : null });

describe("a group chat, drawn", () => {
    it("draws a Lemma's agents from the Lemma's face, and people as people", () => {
        expect(faceIdOf({ kind: "AGENT", id: "front" }, LEMMA, "front")).toBe("pod-support");
        expect(faceIdOf({ kind: "AGENT", id: null }, LEMMA, "front")).toBe("pod-support");
        expect(faceIdOf({ kind: "AGENT", id: "refunds" }, LEMMA, "front")).toBe("pod-support:refunds");
        expect(faceIdOf({ kind: "MEMBER", id: "sam" }, LEMMA, "front")).toBeNull();
        expect(faceIdOf({ kind: "CONTACT", id: "maria" }, LEMMA, "front")).toBeNull();
        expect(faceIdOf({ kind: "AGENT", id: "front" }, null, "front")).toBeNull();
    });

    it("stacks the room's Lemmas first, then its people, leaving the reader out", () => {
        const members = [member("MEMBER", "me", "Ada"), member("MEMBER", "sam", "Sam"), member("AGENT", "front", "Support"), member("CONTACT", "maria", "Maria"), member("MEMBER", "priya", "Priya"), member("AGENT", "refunds", "Refunds")];
        const faces = roomFaces(members, "me");
        expect(faces.shown.map((face) => face.person.name)).toEqual(["Support", "Refunds", "Sam", "Maria"]);
        expect(faces.shown.map((face) => face.lemma)).toEqual([true, true, false, false]);
        expect(faces.more).toBe(1);
        expect(faces.total).toBe(6);
        /* Alone in it, the reader is who is shown. */
        expect(roomFaces([member("MEMBER", "me", "Ada")], "me").shown.map((face) => face.person.name)).toEqual(["Ada"]);
        expect(roomFaces([], "me")).toEqual({ shown: [], more: 0, total: 0 });
        /* The Lemma is in every chat it has, seated or not: once. */
        const front = { kind: "AGENT" as const, id: "front", name: "Support" };
        const unseated = roomFaces([member("MEMBER", "me", "Ada")], "me", front);
        expect(unseated.shown.map((face) => face.person.name)).toEqual(["Support"]);
        expect(unseated.total).toBe(2);
        expect(roomFaces(members, "me", front).shown.map((face) => face.person.name)).toEqual(["Support", "Refunds", "Sam", "Maria"]);
    });

    it("marks a name with the platform it was written from", () => {
        const places = [{ id: "wa", platform: "WHATSAPP" }, { id: "tg", platform: "telegram" }, { id: "mail", platform: "RESEND" }];
        expect(saidOn("wa", places)).toBe("WHATSAPP");
        expect(saidOn("tg", places)).toBe("TELEGRAM");
        expect(saidOn("mail", places)).toBeNull();
        expect(saidOn(null, places)).toBeNull();
        expect(saidOn("gone", places)).toBeNull();
    });

    it("says a reply that didn't arrive in amber, by platform, and nothing for one that did", () => {
        const places = [{ id: "wa", platform: "WHATSAPP" }, { id: "tg", platform: "TELEGRAM" }];
        const sent = (status: LineDelivery["status"], sourceId = "wa", reason: string | null = null): LineDelivery => ({ id: "d-" + status + sourceId, sourceId, platform: null, status, reason });
        expect(arrivalOf([sent("SENT")], places, sayReason)).toEqual({ failed: [], pending: null, skipped: [] });
        expect(arrivalOf(null, places, sayReason)).toEqual({ failed: [], pending: null, skipped: [] });
        const dead = arrivalOf([sent("DEAD", "wa", "outside_window")], places, sayReason);
        expect(dead.failed).toEqual([{ id: "d-DEADwa", said: "Not delivered to WhatsApp", retry: true, why: "WhatsApp only allows a reply within a day of their last message" }]);
        expect(arrivalOf([sent("FAILED", "tg")], places, sayReason).failed[0].said).toBe("Not delivered to Telegram");
        expect(arrivalOf([sent("PENDING"), sent("PENDING", "tg")], places, sayReason).pending).toBe("Sending to WhatsApp and Telegram…");
        expect(arrivalOf([sent("SKIPPED", "wa", "opted_out")], places, sayReason).skipped).toEqual(["Not sent to WhatsApp: they asked not to be written to there"]);
        /* A place gone from the chat is said by the send's own platform. */
        expect(arrivalOf([{ id: "x", sourceId: "old", platform: "TELEGRAM", status: "DEAD", reason: null }], places, sayReason).failed[0]).toMatchObject({ said: "Not delivered to Telegram", why: null });
    });

    it("offers the room's Lemmas, then its people, then Lemmas from outside it", () => {
        const people: Mentionable[] = [
            { kind: "AGENT", id: "front", name: "Support", inChannel: true },
            { kind: "MEMBER", id: "sam", name: "Sam Rivera", inChannel: true },
            { kind: "MEMBER", id: "priya", name: "Priya Shah", inChannel: false },
            { kind: "AGENT", id: "refunds", name: "Refund checker", inChannel: false },
            { kind: "AGENT", id: "copy", name: "Sales copy", inChannel: true },
        ];
        expect(stripOf(people, "").map((who) => who.id)).toEqual(["front", "copy", "sam", "priya", "refunds"]);
        expect(stripOf(people, "s").map((who) => who.id)).toEqual(["front", "copy", "sam", "priya"]);
        expect(stripOf(people, "ri").map((who) => who.id)).toEqual(["sam"]);
        expect(stripOf(people, "", 2)).toHaveLength(2);
    });

    it("asks a Lemma picked from the strip by leading the words with its name", () => {
        expect(askingSaid("Support")).toBe("ask Support…");
        expect(askingText("where is order 4471?", "Support")).toBe("@Support where is order 4471?");
        expect(askingText("  ", "Support")).toBe("@Support");
        expect(askingText("thanks @Support for that", "Support")).toBe("thanks @Support for that");
        expect(askingText("@Support2 hi", "Support")).toBe("@Support @Support2 hi");
        const asked: Mentionable = { kind: "AGENT", id: "front", name: "Support" };
        expect(mentionsIn(askingText("hi", "Support"), [asked])).toEqual([{ kind: "AGENT", id: "front" }]);
    });

    it("takes the @ that opened the strip out of the words once a Lemma is picked", () => {
        expect(withoutQuery("@", { start: 0 }, 1)).toEqual({ text: "", caret: 0 });
        expect(withoutQuery("can you @Su", { start: 8 }, 11)).toEqual({ text: "can you", caret: 7 });
        expect(withoutQuery("can you @Su check", { start: 8 }, 11)).toEqual({ text: "can you check", caret: 8 });
        expect(withoutQuery("@ check", { start: 0 }, 1)).toEqual({ text: "check", caret: 0 });
    });

    it("settles a thread's drawer at half, full, or gone", () => {
        const full = 800;
        const half = snapOffset("half", full, 420);
        expect(half).toBe(380);
        expect(snapOffset("full", full, 420)).toBe(0);
        expect(snapOffset("closed", full, 420)).toBe(800);
        expect(snapOffset("half", 300, 420)).toBe(0);
        /* Slow: to the nearer, and down past a quarter of the rest closes. */
        expect(snapAfter(half - 20, 0, half, full)).toBe("half");
        expect(snapAfter(120, 0, half, full)).toBe("full");
        expect(snapAfter(half + 80, 0, half, full)).toBe("half");
        expect(snapAfter(half + 160, 0, half, full)).toBe("closed");
        /* A flick goes one step the way it was thrown. */
        expect(snapAfter(half, -1400, half, full)).toBe("full");
        expect(snapAfter(40, 1400, half, full)).toBe("half");
        expect(snapAfter(half, 1400, half, full)).toBe("closed");
    });
});
