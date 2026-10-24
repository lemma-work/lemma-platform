import { describe, expect, it } from "vitest";
import { SAMPLE_LEMMAS } from "../src/data/sample";
import type { ConversationRef } from "../src/data/types";
import {
    agoSaid,
    botAnswerChoices,
    chatEntries,
    contactStrip,
    followUpSaid,
    introOf,
    lastReplySaid,
    lineOffers,
    newMessages,
    outsideOf,
    peopleRows,
    placeholderFor,
    removedNote,
    seenOnScreen,
    starterWords,
    subtitleOf,
    threadSaid,
    waitingEntries,
    whereSaid,
    whoAnswers,
} from "../src/areas/chats/model";
import { chatsRoutes } from "../src/areas/chats/routes";
import { chatsSample } from "../src/areas/chats/sample";
import { readChannelDetail, readLine, type Channel, type ChannelDetail, type Line } from "@web/channels/model";
import { readTarget } from "@web/channels/read-state";
import { mineRequest, postRequest, readAgents, readSurfaces, roleOf, updateRequest } from "../src/areas/chats/wire";

/* ── the web's own modules, held to the web ───────────────────────────── */

/* ── fixtures ─────────────────────────────────────────────────────────── */

const NOW = new Date("2026-10-24T12:00:00Z");
const ago = (ms: number) => new Date(NOW.getTime() - ms).toISOString();
const MIN = 60_000;

function channel(raw: Record<string, unknown>): ChannelDetail {
    const read = readChannelDetail({ id: "c1", kind: "GROUP", created_at: ago(30 * 24 * 60 * MIN), ...raw });
    if (!read) throw new Error("no channel");
    return read;
}

function line(raw: Record<string, unknown>): Line {
    const read = readLine({ id: "l" + Math.random(), seq: 1, created_at: ago(MIN), text: "Hi", sender: { kind: "MEMBER", id: "me", name: "Ada" }, ...raw });
    if (!read) throw new Error("no line");
    return read;
}

function chat(id: string, extra: Partial<ConversationRef> = {}): ConversationRef {
    return { id, title: id, activityAt: ago(5 * MIN), kind: "CHAT", boundTo: null, origin: { kind: "chat", label: "Chat" }, agentId: null, status: "IDLE", waitingOnYou: false, ...extra };
}

/* ── the chat list ────────────────────────────────────────────────────── */

describe("the chat list", () => {
    const group = channel({ id: "g", title: "Launch team", last_line_at: ago(1 * MIN), last_line: { sender: { kind: "MEMBER", id: "sam", name: "Sam" }, text: "Photos  are in", created_at: ago(MIN) }, unread: { lines: 3, mentions: 1 } });
    const direct = channel({ id: "d", kind: "DIRECT", sources: [{ platform: "WHATSAPP" }], last_line_at: ago(30 * MIN) });
    const contact = channel({ id: "k", kind: "CONTACT", person: { kind: "CONTACT", id: "p", name: "Maria" } });
    const chats = [chat("Refund", { activityAt: ago(10 * MIN), waitingOnYou: true }), chat("Digest", { activityAt: ago(2 * MIN), origin: { kind: "schedule", label: "Schedule · Digest" } })];
    const base = { podId: "pod", lemmaName: "Support", chats, channels: [group, direct, contact], search: "", me: "me" };

    it("puts chats and shared chats in one list, newest first, and leaves contacts to their queue", () => {
        const rows = chatEntries({ ...base, filter: "all" });
        expect(rows.map((one) => one.title)).toEqual(["Launch team", "Digest", "Refund", "WhatsApp"]);
        expect(rows[0]).toMatchObject({ unread: 3, needsYou: true, said: "Sam: Photos are in", href: chatsRoutes.shared("pod", "g"), glyph: { kind: "hash" } });
        expect(rows[1]).toMatchObject({ said: "Schedule · Digest", glyph: { kind: "automation" } });
        expect(rows[2]).toMatchObject({ needsYou: true, href: "/chat/pod/Refund" });
        expect(rows[3].glyph).toEqual({ kind: "platform", platform: "WHATSAPP" });
    });

    it("cuts the list the way the web's filters do", () => {
        expect(chatEntries({ ...base, filter: "chats" }).map((one) => one.title)).toEqual(["Refund"]);
        expect(chatEntries({ ...base, filter: "automations" }).map((one) => one.title)).toEqual(["Digest"]);
        expect(chatEntries({ ...base, filter: "shared" }).map((one) => one.title)).toEqual(["Launch team", "WhatsApp"]);
        expect(chatEntries({ ...base, filter: "all", search: "launch" }).map((one) => one.title)).toEqual(["Launch team", "Digest", "Refund"]);
    });

    it("lists what waits on the reader as the bell does: their own chat, questions, mentions", () => {
        const inbox = {
            channels: 1, mentions: 1, waiting: 1,
            items: [{ id: "q", channelId: "g", channelTitle: "Launch team", channelKind: "GROUP" as const, kind: "ASK" as const, text: "Which day?", from: "Launch", askedAt: ago(MIN), question: {}, threadRootId: null }],
            mentioned: [{ id: "m", seq: 4, channelId: "g", channelTitle: "#launch", channelKind: "GROUP" as const, from: "Dev", text: "@Ada", at: ago(MIN), threadRootId: null }],
        };
        const mine = { ...direct, unread: { lines: 2, mentions: 0, waiting: 0, readSeq: 0 } };
        expect(waitingEntries(inbox, mine).map((one) => one.kind)).toEqual(["direct", "question", "mention"]);
        expect(waitingEntries(inbox, direct).map((one) => one.kind)).toEqual(["question", "mention"]);
        expect(whereSaid(inbox.mentioned[0])).toBe("In launch");
        expect(whereSaid({ channelKind: "DIRECT", channelTitle: null })).toBe("Sent to you");
        expect(newMessages(1)).toBe("1 new message");
        expect(newMessages(140)).toBe("99+ new messages");
    });
});

/* ── one chat ─────────────────────────────────────────────────────────── */

describe("a chat's words", () => {
    it("says what the box is for, who answers and what a chat is", () => {
        const group = channel({ title: "Launch team", members: [{ person: { kind: "MEMBER", id: "me", name: "Ada" } }, { person: { kind: "MEMBER", id: "sam", name: "Sam" } }, { person: { kind: "AGENT", id: "a", name: "Support" }, answers: "MENTIONED" }] });
        expect(placeholderFor(group, "Support")).toBe("Message everyone, or type @ to ask Support");
        expect(placeholderFor({ kind: "DIRECT" }, "Support")).toBe("Message Support");
        expect(introOf(group, "Support", "me")).toMatch(/^You and Sam, since .+\. Type @ to ask Support\.$/);
        const mirrored = channel({ source_places: [{ id: "s", platform: "WHATSAPP", title: "Acme × Northwind" }], members: [] });
        expect(introOf(mirrored, "Support", "me")).toContain("This chat mirrors Acme × Northwind on WhatsApp.");
        const contact = channel({ kind: "CONTACT", person: { kind: "CONTACT", id: "p", name: "Maria" }, source_places: [{ id: "s", platform: "WHATSAPP" }] });
        expect(introOf(contact, "Support", "me")).toBe("Maria is outside Support and writes by WhatsApp. Your replies go back the same way.");
        expect(whoAnswers({ kind: "CONTACT", assignee: null, agentsAnswer: false, handBackAfterHours: 12 }, "Support", "me")).toBe("Support is off here: only people answer them.");
        expect(whoAnswers({ kind: "CONTACT", assignee: { kind: "MEMBER", id: "me", name: "Ada" }, agentsAnswer: true, handBackAfterHours: 8 }, "Support", "me")).toBe("You are answering here; Support answers again 8 hours after your last message.");
        expect(subtitleOf(channel({ topic: "The spring catalogue" }), "Support", "me")).toBe("The spring catalogue");
    });

    it("never says channel, line, agent or bot to a person", () => {
        const said = [
            placeholderFor({ kind: "GROUP" }, "Support"),
            whoAnswers({ kind: "GROUP", assignee: null, agentsAnswer: true, handBackAfterHours: 12 }, "Support", null),
            ...botAnswerChoices("Support").flatMap((one) => [one.label, one.note]),
            followUpSaid({ status: "SENT", reason: null, platform: "WHATSAPP" }),
            followUpSaid({ status: "DEAD", reason: "outside the window.", platform: null }),
            ...(["missing", "unwell", "ready"] as const).flatMap((state) => (["WHATSAPP", "TELEGRAM"] as const).flatMap((platform) => Object.values(starterWords({ platform, state }, "Support")))),
        ];
        for (const sentence of said) expect(sentence).not.toMatch(/\b(channel|line|agent|bot|member)s?\b/i);
    });

    it("says where a contact's chat stands, and the one move that changes it", () => {
        const waiting = channel({ kind: "CONTACT", status: "open", attention: { since: ago(90 * MIN), reason: "Wants a person" }, agents_answer: false });
        expect(contactStrip(waiting, "me", "Support", true, NOW)).toEqual({ state: "Needs a person: Wants a person · 1 h", asks: true, act: { to: "me", label: "I’ll answer" } });
        const mine = channel({ kind: "CONTACT", status: "open", assignee: { kind: "MEMBER", id: "me", name: "Ada" }, hand_back_after_hours: 6 });
        expect(contactStrip(mine, "me", "Support", true, NOW)).toEqual({ state: "You are answering. Support takes over after 6 quiet hours.", asks: false, act: { to: null, label: "Hand back to Support" } });
        expect(contactStrip(channel({ kind: "CONTACT", status: "closed" }), "me", "Support", true, NOW).act).toBeNull();
        expect(contactStrip(waiting, "me", "Support", false, NOW).act).toBeNull();
    });

    it("offers on a message only what the server would allow", () => {
        const group = channel({ me: { can_manage: false }, source_places: [{ id: "wa", platform: "WHATSAPP", title: "Acme" }] });
        const mine = line({});
        expect(lineOffers(mine, group, "me", true, false)).toMatchObject({ reply: true, copy: true, edit: true, remove: true, share: false, removeLabel: "Delete", removeAsk: "Delete this message?", removeNote: "It goes for everyone in this chat." });
        const sentOut = line({ delivered: [{ id: "d", source_id: "wa", platform: "WHATSAPP", status: "SENT" }] });
        expect(outsideOf(sentOut, group)).toBe("Acme (WhatsApp)");
        expect(lineOffers(sentOut, group, "me", true, false)).toMatchObject({ edit: false, remove: true, removeAsk: "Delete this message? It goes from here only; the copy in Acme (WhatsApp) stays.", removeTitle: "Delete this message?", removeNote: "It goes from here only; the copy in Acme (WhatsApp) stays." });
        const theirs = line({ sender: { kind: "MEMBER", id: "sam", name: "Sam" } });
        expect(lineOffers(theirs, group, "me", true, false)).toMatchObject({ edit: false, remove: false });
        const managed = channel({ me: { can_manage: true } });
        expect(lineOffers(theirs, managed, "me", true, false)).toMatchObject({ remove: true, removeLabel: "Remove for everyone", removeAsk: "Remove Sam’s message for everyone?" });
        expect(lineOffers(mine, group, "me", false, false)).toMatchObject({ edit: false, remove: false });
        expect(lineOffers(mine, group, "me", true, true).reply).toBe(false);
        expect(lineOffers(line({ deleted: true }), group, "me", true, false)).toMatchObject({ reply: false, copy: false, remove: false });
        expect(lineOffers(line({ can_share: true, sender: { kind: "AGENT", id: "a" } }), group, "me", true, false).share).toBe(true);
        expect(removedNote(line({ deleted: true, removed_by: { kind: "MEMBER", id: "sam", name: "Sam" } }), "me")).toBe("Removed by Sam.");
        expect(removedNote(line({ deleted: true }), "me")).toBe("This message was deleted.");
    });

    it("says when a group last heard from its place without \"Yesterday ago\"", () => {
        expect(agoSaid(ago(5 * MIN), NOW)).toBe("5 min ago");
        expect(agoSaid(ago(26 * 60 * MIN), NOW)).toBe("yesterday");
        expect(agoSaid(ago(30 * 24 * 60 * MIN), NOW)).toMatch(/^on /);
    });

    it("says a thread's replies and when the last came", () => {
        expect(lastReplySaid(ago(10 * MIN), NOW)).toMatch(/^today at /);
        expect(threadSaid(1, null, NOW)).toBe("1 reply");
        expect(threadSaid(3, ago(10 * MIN), NOW)).toMatch(/^3 replies · last today at /);
    });
});

/* ── reading ──────────────────────────────────────────────────────────── */

describe("what the reader has seen", () => {
    const boxes = [
        { seq: 1, y: 0, height: 100 },
        { seq: 2, y: 100, height: 100 },
        { seq: 3, y: 200, height: 100 },
        { seq: 4, y: 300, height: 600 },
    ];

    it("counts a message once most of it is on screen, or it fills half of it", () => {
        expect(seenOnScreen(boxes, 0, 250)).toBe(2);
        expect(seenOnScreen(boxes, 0, 280)).toBe(3);
        expect(seenOnScreen(boxes, 400, 300)).toBe(4);
        expect(seenOnScreen([{ seq: 0, y: 0, height: 50 }], 0, 500)).toBe(0);
    });

    it("marks read to the newest of all at the bottom, never past a mention left in a thread", () => {
        expect(readTarget({ seenMax: 9, atEnd: true, newestShown: 9, lastSeq: 12, readSeq: 4, marked: 0 })).toBe(12);
        expect(readTarget({ seenMax: 9, atEnd: true, newestShown: 9, lastSeq: 12, readSeq: 4, marked: 0, heldBack: 11 })).toBe(10);
        expect(readTarget({ seenMax: 6, atEnd: false, newestShown: 9, lastSeq: 12, readSeq: 4, marked: 0 })).toBe(6);
        expect(readTarget({ seenMax: 3, atEnd: false, newestShown: 9, lastSeq: 12, readSeq: 4, marked: 0 })).toBeNull();
    });
});

/* ── the people in a Lemma ────────────────────────────────────────────── */

describe("the people in a Lemma", () => {
    it("puts the reader first, then whoever has the app open", () => {
        const people = [
            { userId: "b", name: "Bea", email: null, role: "User" },
            { userId: "me", name: "Ada", email: null, role: "Admin" },
            { userId: "c", name: "Cy", email: null, role: "Editor" },
        ];
        expect(peopleRows(people, new Set(["c"]), "me").map((one) => [one.name, one.you, one.here])).toEqual([["Ada", true, false], ["Cy", false, true], ["Bea", false, false]]);
    });
});

/* ── what the area sends and reads ────────────────────────────────────── */

describe("what is sent", () => {
    it("posts mentions and files as parts, and only the changes asked for", () => {
        expect(postRequest({ text: "Hi @Sam", mentions: [{ kind: "MEMBER", id: "sam" }], files: [{ type: "file", path: "/me/a.png", name: "a.png", mimeType: "image/png", size: 10, fileId: null }], clientMessageId: "x", membersOnly: true })).toEqual({
            text: "Hi @Sam", members_only: true, source_id: null, thread_root_id: null, client_message_id: "x",
            parts: [{ type: "mention", id: "sam", kind: "MEMBER" }, { type: "file", path: "/me/a.png", name: "a.png", mime_type: "image/png", size: 10 }],
        });
        expect(updateRequest({ assignee: null, answersShared: false })).toEqual({ assignee: null, answers_shared: false });
        expect(mineRequest({ notify: "MENTIONS", mutedUntil: null })).toEqual({ notify: "MENTIONS", muted_until: null });
    });

    it("reads the Lemma's agents, people's roles and bots as the web does", () => {
        const agents = readAgents({ items: [{ id: "2", name: "researcher", description: "Finds sources. Checks facts." }, { id: "1", name: "pod_default" }, { name: "nameless" }] }, "Support");
        expect(agents.map((one) => [one.label, one.front, one.blurb])).toEqual([["Support", true, ""], ["Researcher", false, "Finds sources."]]);
        expect(roleOf(["POD_USER", "POD_EDITOR"])).toBe("Editor");
        expect(roleOf(["POD_SUPPORT_LEAD"])).toBe("Support lead");
        expect(readSurfaces({ items: [{ id: "s", platform: "WHATSAPP", name: "wa", uses_default_agent: true, reach: { handle: "+44" }, status: "INACTIVE" }] }, "Support")[0]).toMatchObject({ mine: true, agentName: "Support", handle: "+44", active: false });
    });
});

/* ── the sample source ────────────────────────────────────────────────── */

describe("the sample chats", () => {
    const [SUPPORT, LAUNCH] = SAMPLE_LEMMAS;

    it("has the core sample's chats, so a row on the Lemmas tab opens here", async () => {
        const source = chatsSample();
        for (const [podId, id] of [[SUPPORT.id, "support-line"], [SUPPORT.id, "support-maria"], [LAUNCH.id, "launch-week"]]) {
            await expect(source.get(podId, id)).resolves.toMatchObject({ id });
        }
        const listed = await source.list(SUPPORT.id, { archived: false });
        expect(listed.items.map((one: Channel) => one.id)).toContain("customer-care");
    });

    it("sends once per message, counts unread and answers questions", async () => {
        const source = chatsSample();
        const before = await source.inbox(LAUNCH.id);
        expect(before.items.map((one) => one.kind).sort()).toEqual(["APPROVAL", "FORM"]);
        expect(before.mentioned).toHaveLength(1);
        const sent = await source.post(LAUNCH.id, "launch-week", { text: "Tuesday then", clientMessageId: "same" });
        const again = await source.post(LAUNCH.id, "launch-week", { text: "Tuesday then", clientMessageId: "same" });
        expect(again.id).toBe(sent.id);
        const approval = before.items.find((one) => one.kind === "APPROVAL")!;
        await source.answer(LAUNCH.id, "launch-week", approval.id, { decision: "APPROVE_ONCE" });
        const after = await source.inbox(LAUNCH.id);
        expect(after.items.map((one) => one.kind)).toEqual(["FORM"]);
        await source.markRead(LAUNCH.id, "launch-week", sent.seq);
        expect((await source.inbox(LAUNCH.id)).mentioned).toHaveLength(0);
        expect((await source.get(LAUNCH.id, "launch-week")).unread.lines).toBe(0);
    });

    it("keeps a thread's replies with it and counts them on its first message", async () => {
        const source = chatsSample();
        const page = await source.lines(LAUNCH.id, "launch-week");
        const older = page.nextBefore === null ? page : await source.lines(LAUNCH.id, "launch-week", page.nextBefore);
        const root = [...older.items, ...page.items].find((one) => one.replyCount > 0)!;
        await source.post(LAUNCH.id, "launch-week", { text: "Agreed", threadRootId: root.id });
        const thread = await source.thread(LAUNCH.id, "launch-week", root.id);
        expect(thread.items.at(-1)?.text).toBe("Agreed");
        const all = [...(await source.lines(LAUNCH.id, "launch-week", page.nextBefore ?? undefined)).items, ...(await source.lines(LAUNCH.id, "launch-week")).items];
        expect(all.find((one) => one.id === root.id)?.replyCount).toBe(root.replyCount + 1);
    });

    it("finds what was said, and pages a long history", async () => {
        const source = chatsSample();
        const found = await source.search(SUPPORT.id, "brass lamp", {});
        expect(found.items[0]?.channel.id).toBe("customer-care");
        const first = await source.conversations(SUPPORT.id, null, null);
        expect(first.next).not.toBeNull();
        const second = await source.conversations(SUPPORT.id, first.next, null);
        expect(new Set([...first.items, ...second.items].map((one) => one.id)).size).toBe(first.items.length + second.items.length);
        expect((await source.conversations(SUPPORT.id, null, "invoice")).items.map((one) => one.title)).toEqual(["Invoice sent twice"]);
    });

    it("retries a send that gave up, and shares an answer kept to the reader", async () => {
        const source = chatsSample();
        const lines = [...(await source.lines(SUPPORT.id, "customer-care", 8)).items, ...(await source.lines(SUPPORT.id, "customer-care")).items];
        const failed = lines.find((one) => one.delivered?.some((each) => each.status === "DEAD"))!;
        await source.retryDelivery(SUPPORT.id, "customer-care", failed.id, failed.delivered![0].id);
        const kept = lines.find((one) => one.canShare)!;
        const shared = await source.share(SUPPORT.id, "customer-care", kept.id);
        expect(shared).toMatchObject({ visibility: "channel", canShare: false });
        const again = [...(await source.lines(SUPPORT.id, "customer-care", 8)).items, ...(await source.lines(SUPPORT.id, "customer-care")).items];
        expect(again.find((one) => one.id === failed.id)?.delivered?.[0].status).toBe("SENT");
    });

    it("starts a WhatsApp group with its link, and takes a group on", async () => {
        const source = chatsSample();
        const made = await source.startWhatsAppGroup(SUPPORT.id, { surfaceName: "support-whatsapp", title: "Northwind", answersOutsiders: true });
        expect(made.inviteLink).toMatch(/^https:\/\/chat\.whatsapp\.com\//);
        await expect(source.get(SUPPORT.id, made.channelId)).resolves.toMatchObject({ title: "Northwind", kind: "GROUP" });
        const taken = await source.updateSource(SUPPORT.id, "customer-care", "care-wa", { owner: "me" });
        expect(taken.owner?.id).toBe("me");
    });
});
