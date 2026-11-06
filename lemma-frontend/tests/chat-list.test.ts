import test from "node:test";
import assert from "node:assert/strict";
import {
    CHAT_FILTERS,
    archivedChat,
    chatItems,
    chatRows,
    chatsOf,
    keptBy,
    listedAsChat,
    listsChannels,
    matchesChat,
} from "../src/channels/chat-list.ts";
import type { ConversationRef } from "../src/data/types.ts";
import type { Channel } from "../src/channels/model.ts";

/* The one chat list every surface draws: the classic sidebar and Chats page,
   the frame's sidebar and ⌘K, and the phone. */

function channel(id: string, kind: Channel["kind"], extra: Partial<Channel> = {}): Channel {
    return {
        id,
        kind,
        title: kind === "GROUP" ? id : null,
        topic: null,
        person: null,
        visibility: "PUBLIC",
        answersShared: true,
        sources: [],
        assignee: null,
        agentsAnswer: true,
        handBackAfterHours: 24,
        status: null,
        attention: null,
        archived: false,
        joined: true,
        lastLine: null,
        lastMessageAt: null,
        lastSeq: 0,
        createdAt: "2026-10-01T00:00:00Z",
        unread: { lines: 0, mentions: 0, waiting: 0, readSeq: 0 },
        ...extra,
    };
}

function chat(id: string, activityAt: string | null, extra: Partial<ConversationRef> = {}): ConversationRef {
    return { id, title: id, at: "display only", activityAt, kind: "CHAT", ...extra };
}

const POD = { id: "p1", name: "Support" };

test("one teammate's chats and chats on a channel are one list, newest first, without contacts or what was archived", () => {
    const items = chatsOf({
        pod: POD,
        conversations: [chat("Refund question", "2026-10-20T10:00:00Z"), chat("No time yet", null)],
        channels: [
            channel("Ops", "GROUP", { lastMessageAt: "2026-10-21T11:00:00Z", unread: { lines: 3, mentions: 0, waiting: 0, readSeq: 1 } }),
            channel("mine", "DIRECT", { sources: [{ platform: "WHATSAPP", title: null }], lastMessageAt: "2026-10-18T08:00:00Z" }),
            channel("mine-empty", "DIRECT"),
            channel("customer", "CONTACT", { lastMessageAt: "2026-10-21T13:00:00Z" }),
            channel("Old", "GROUP", { archived: true, lastMessageAt: "2026-10-22T08:00:00Z" }),
        ],
        me: null,
    });
    assert.deepEqual(items.map((item) => item.title), ["Ops", "Refund question", "Your chat on WhatsApp", "No time yet"]);
    assert.equal(items[0].unread, 3);
    assert.deepEqual(items[0].open, { kind: "shared", podId: "p1", id: "Ops" });
    assert.equal(items[2].kind, "line", "the reader's own chat is named for where they reach it");
    assert.deepEqual(items[1].open, { kind: "chat", podId: "p1", id: "Refund question" });
});

test("archived chats are listed only under Archived, where they can be found and brought back", () => {
    const channels = [channel("Live", "GROUP"), channel("Old", "GROUP", { archived: true }), channel("Gone contact", "CONTACT", { archived: true })];
    assert.deepEqual(channels.filter(listedAsChat).map((one) => one.id), ["Live"]);
    assert.deepEqual(channels.filter(archivedChat).map((one) => one.id), ["Old"]);
    const archived = chatsOf({ pod: POD, conversations: [], channels, me: null, archived: true });
    assert.deepEqual(archived.map((item) => item.title), ["Old"]);
});

test("the Chats page's filters: Shared and Archived list only chats on a channel; the rest cut conversations by origin", () => {
    assert.deepEqual(CHAT_FILTERS.map((one) => one.id), ["all", "chats", "channels", "automations", "docs", "archived"]);
    assert.deepEqual(CHAT_FILTERS.filter((one) => listsChannels(one.id)).map((one) => one.id), ["all", "channels", "archived"]);
    const schedule = chat("digest", null, { origin: { kind: "schedule", label: "Schedule · digest" } });
    const mine = chat("mine", null);
    assert.equal(keptBy("all")(schedule), true);
    assert.equal(keptBy("automations")(schedule), true);
    assert.equal(keptBy("automations")(mine), false);
    assert.equal(keptBy("chats")(mine), true, "a conversation with no origin is a chat");
    assert.equal(keptBy("channels")(mine), false);
    assert.equal(keptBy("archived")(mine), false);
});

test("a chat is found by its name or what was last said in it; across teammates, by the teammate's name too", () => {
    const ops = channel("Ops", "GROUP", { lastLine: { sender: { kind: "MEMBER", id: "u2", name: "Priya" }, text: "Thursday works", createdAt: "2026-10-21T11:00:00Z" } } as Partial<Channel>);
    const [item] = chatsOf({ pod: POD, conversations: [], channels: [ops], me: "u1" });
    assert.equal(matchesChat(item, "thursday"), true);
    assert.equal(matchesChat(item, "ops"), true);
    assert.equal(matchesChat(item, "support"), false, "inside one teammate, its own name finds nothing");
    const across = chatItems({ pods: [POD], conversations: new Map(), channels: new Map([["p1", [ops]]]), me: "u1", search: "support" });
    assert.deepEqual(across.rest.map((one) => one.title), ["Ops"]);
});

test("the frame's list counts a conversation only when it is a chat, and puts what waits on you first", () => {
    const { needs, rest } = chatItems({
        pods: [POD],
        conversations: new Map([["p1", [
            chat("Morning digest", "2026-10-21T09:00:00Z", { origin: { kind: "schedule", label: "Schedule · digest" } }),
            chat("Approve a refund", "2026-10-19T09:00:00Z", { waitingOnYou: true }),
            chat("Refund question", "2026-10-20T10:00:00Z"),
        ]]]),
        channels: new Map([["p1", [channel("Escalations", "GROUP", { unread: { lines: 1, mentions: 0, waiting: 1, readSeq: 0 } })]]]),
        me: null,
    });
    assert.deepEqual(needs.map((one) => one.title), ["Approve a refund", "Escalations"]);
    assert.deepEqual(rest.map((one) => one.title), ["Refund question"]);
});

test("the phone's older shape is the same list", () => {
    const rows = chatRows(
        [chat("Refund question", "2026-10-20T10:00:00Z")],
        [channel("Ops", "GROUP", { lastMessageAt: "2026-10-21T11:00:00Z" }), channel("customer", "CONTACT")],
        "Support",
    );
    assert.deepEqual(rows.map((row) => [row.kind, row.id, row.title, row.at]), [
        ["channel", "channel:Ops", "Ops", "2026-10-21T11:00:00Z"],
        ["chat", "Refund question", "Refund question", "2026-10-20T10:00:00Z"],
    ]);
});
