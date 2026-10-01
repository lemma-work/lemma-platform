import test from "node:test";
import assert from "node:assert/strict";
import {
    channelThread,
    defaultReplyMode,
    rememberReplyMode,
    rememberedReplyMode,
    replyChoices,
    replyModeFor,
    sendMetadata,
} from "../src/thread/channel-reply.ts";
import { originLabel, originOf } from "../src/thread/conversation-origin.ts";
import { buildTurns, humanMarks } from "../src/thread/turns.ts";

/** Conversations that live on a chat platform: where an answer lands, and who
 *  is speaking in them. */

const OUTSIDERS = {
    source: "agent_surfaces",
    surface_platform: "TELEGRAM",
    conversation_kind: "CHANNEL",
    channel_name: "Design partners",
    audience: "outsiders",
};

test("a conversation that lives only here offers no choice", () => {
    assert.equal(channelThread(null), null);
    assert.equal(channelThread({ source: "SCHEDULE" }), null);
});

test("names where a reply lands the way the platform does", () => {
    assert.deepEqual(channelThread(OUTSIDERS), { platform: "TELEGRAM", place: "Design partners", kind: "CHANNEL", outsiders: true });
    assert.equal(channelThread({ surface_platform: "SLACK", channel_name: "launch" })?.place, "#launch");
    assert.equal(channelThread({ surface_platform: "WHATSAPP", conversation_kind: "DM" })?.place, "WhatsApp");
    assert.equal(channelThread({ surface_platform: "SLACK", channel_name: "launch" })?.outsiders, false);
});

test("the note comes first, and each choice says what it does", () => {
    const group = replyChoices(channelThread(OUTSIDERS)!, "Researcher");
    assert.deepEqual(group, [
        { mode: "note", label: "Note to Researcher", hint: "Only Researcher sees this." },
        { mode: "reply", label: "Reply in Design partners", hint: "Researcher’s answer goes to the group." },
    ]);
    const direct = replyChoices(channelThread({ surface_platform: "WHATSAPP", conversation_kind: "DM" })!, "Kit");
    assert.equal(direct[1].label, "Reply in WhatsApp");
    assert.equal(direct[1].hint, "Kit’s answer goes to WhatsApp.");
    const email = replyChoices(channelThread({ surface_platform: "RESEND", conversation_kind: "EMAIL" })!, "Kit");
    assert.equal(email[1].label, "Reply by email");
    assert.equal(email[1].hint, "Kit’s answer goes out by email.");
    const slack = replyChoices(channelThread({ surface_platform: "SLACK", conversation_kind: "CHANNEL", channel_name: "launch" })!, "Kit");
    assert.equal(slack[1].hint, "Kit’s answer goes to #launch.");
});

test("in a person's own DM or email thread, a note says nothing is sent rather than sounding like a secret", () => {
    const dm = replyChoices(channelThread({ surface_platform: "TELEGRAM", conversation_kind: "DM" })!, "Sales");
    assert.equal(dm[0].hint, "Only Sales sees this. Nothing is sent to Telegram.");
    const email = replyChoices(channelThread({ surface_platform: "RESEND", conversation_kind: "EMAIL" })!, "Sales");
    assert.equal(email[0].hint, "Only Sales sees this. No email is sent.");
    // In a group the note is kept from the people in it, and says only that.
    assert.equal(replyChoices(channelThread(OUTSIDERS)!, "Sales")[0].hint, "Only Sales sees this.");
});

test("a reply first in a person's own DM or email thread, a note first wherever others read along", () => {
    assert.equal(defaultReplyMode(channelThread({ surface_platform: "WHATSAPP", conversation_kind: "DM" })!), "reply");
    assert.equal(defaultReplyMode(channelThread({ surface_platform: "RESEND", conversation_kind: "EMAIL" })!), "reply");
    assert.equal(defaultReplyMode(channelThread(OUTSIDERS)!), "note");
    assert.equal(defaultReplyMode(channelThread({ surface_platform: "SLACK", conversation_kind: "CHANNEL", channel_name: "launch" })!), "note");
    // Not said which it is: the one that cannot speak in front of a group.
    assert.equal(defaultReplyMode(channelThread({ surface_platform: "SLACK" })!), "note");
    // What was chosen in the conversation before wins over its default.
    const dm = channelThread({ surface_platform: "WHATSAPP", conversation_kind: "DM" })!;
    assert.equal(replyModeFor(dm, null), "reply");
    assert.equal(replyModeFor(dm, "note"), "note");
    assert.equal(replyModeFor(channelThread(OUTSIDERS)!, "reply"), "reply");
});

/** A browser's storage, in memory: what was written is what is read. */
function memoryStore(): { getItem(name: string): string | null; setItem(name: string, value: string): void; values: Map<string, string> } {
    const values = new Map<string, string>();
    return { values, getItem: (name) => values.get(name) ?? null, setItem: (name, value) => { values.set(name, value); } };
}

test("the choice is kept per conversation, and one conversation's never reaches another", () => {
    const store = memoryStore();
    assert.equal(rememberedReplyMode(store, "c-dm"), null);
    rememberReplyMode(store, "c-dm", "note");
    rememberReplyMode(store, "c-group", "reply");
    assert.equal(rememberedReplyMode(store, "c-dm"), "note");
    assert.equal(rememberedReplyMode(store, "c-group"), "reply");
    assert.equal(rememberedReplyMode(store, "c-other"), null);
    // Chosen again, the last choice is the one kept.
    rememberReplyMode(store, "c-dm", "reply");
    assert.equal(rememberedReplyMode(store, "c-dm"), "reply");
    // A conversation not made yet has nothing to keep it against.
    rememberReplyMode(store, null, "reply");
    assert.equal(rememberedReplyMode(store, null), null);
    // One entry for all of them, under the app's own prefix.
    assert.deepEqual([...store.values.keys()], ["lemma-app:reply-modes"]);
});

test("only the most recently chosen conversations are kept", () => {
    const store = memoryStore();
    for (let at = 0; at < 205; at += 1) rememberReplyMode(store, "c" + at, "reply");
    assert.equal(rememberedReplyMode(store, "c0"), null);
    assert.equal(rememberedReplyMode(store, "c4"), null);
    assert.equal(rememberedReplyMode(store, "c5"), "reply");
    assert.equal(rememberedReplyMode(store, "c204"), "reply");
    // Choosing an old one again moves it to the end rather than losing it.
    rememberReplyMode(store, "c5", "note");
    rememberReplyMode(store, "c205", "reply");
    assert.equal(rememberedReplyMode(store, "c5"), "note");
    assert.equal(rememberedReplyMode(store, "c6"), null);
});

test("without storage, or with storage that refuses or holds nonsense, it still works", () => {
    assert.equal(rememberedReplyMode(null, "c-dm"), null);
    assert.doesNotThrow(() => rememberReplyMode(null, "c-dm", "reply"));
    const refusing = {
        getItem(): string | null { throw new Error("SecurityError"); },
        setItem(): void { throw new Error("QuotaExceededError"); },
    };
    assert.equal(rememberedReplyMode(refusing, "c-dm"), null);
    assert.doesNotThrow(() => rememberReplyMode(refusing, "c-dm", "reply"));
    const garbled = memoryStore();
    garbled.setItem("lemma-app:reply-modes", "{not json");
    assert.equal(rememberedReplyMode(garbled, "c-dm"), null);
    garbled.setItem("lemma-app:reply-modes", JSON.stringify([["c-dm", "shout"], ["c-ok", "note"], "junk"]));
    assert.equal(rememberedReplyMode(garbled, "c-dm"), null);
    assert.equal(rememberedReplyMode(garbled, "c-ok"), "note");
    // Writing over nonsense keeps what was readable.
    rememberReplyMode(garbled, "c-dm", "reply");
    assert.equal(rememberedReplyMode(garbled, "c-ok"), "note");
    assert.equal(rememberedReplyMode(garbled, "c-dm"), "reply");
});

test("only a note carries the mark, and only where there is a platform to keep it from", () => {
    const thread = channelThread(OUTSIDERS);
    assert.deepEqual(sendMetadata(thread, "note"), { private_note: true });
    assert.equal(sendMetadata(thread, "reply"), undefined);
    assert.equal(sendMetadata(null, "note"), undefined);
});

test("outsiders' conversations come from the people, then the group", () => {
    const origin = originOf(OUTSIDERS, "CHAT");
    assert.deepEqual(origin, { kind: "channel", label: "Design partners", platform: "TELEGRAM", outsiders: true });
    assert.equal(originLabel(origin, "Marketing"), "People outside Marketing · Design partners");
    const unnamed = originOf({ ...OUTSIDERS, channel_name: null }, "CHAT");
    assert.equal(originLabel(unnamed, "Marketing"), "People outside Marketing · Telegram group");
    // Every other channel conversation reads as it did.
    const slack = originOf({ source: "agent_surfaces", surface_platform: "SLACK", channel_name: "launch" }, "CHAT");
    assert.equal(originLabel(slack, "Marketing"), "Slack · #launch");
});

test("a note, and a message from somebody in the group, say so; a plain one says nothing", () => {
    assert.deepEqual(humanMarks({ private_note: true }), { note: true });
    assert.deepEqual(humanMarks({ surface_platform: "TELEGRAM", sender_display_name: " Mara Okafor " }), { from: "Mara Okafor" });
    assert.deepEqual(humanMarks({ surface_platform: "WHATSAPP", sender_phone: "+15550100" }), { from: "+15550100" });
    assert.deepEqual(humanMarks({ surface_platform: "TELEGRAM" }), { from: "Someone" });
    assert.deepEqual(humanMarks(null), {});

    const turns = buildTurns([
        { id: "a", role: "user", kind: "TEXT", sequence: 1, text: "is export ready?", metadata: { surface_platform: "TELEGRAM", sender_display_name: "Mara Okafor" } },
        { id: "b", role: "assistant", kind: "TEXT", sequence: 2, text: "Not yet." },
        { id: "c", role: "user", kind: "TEXT", sequence: 3, text: "Tell them November.", metadata: { private_note: true } },
        { id: "d", role: "user", kind: "TEXT", sequence: 4, text: "plain" },
    ]);
    assert.equal(turns[0].human?.from, "Mara Okafor");
    assert.equal(turns[1].human?.note, true);
    assert.equal(turns[1].human?.from, undefined);
    assert.deepEqual(Object.keys(turns[2].human ?? {}).sort(), ["at", "id", "text"]);
});
