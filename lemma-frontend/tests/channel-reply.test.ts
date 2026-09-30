import test from "node:test";
import assert from "node:assert/strict";
import { channelThread, replyChoices, sendMetadata } from "../src/thread/channel-reply.ts";
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
