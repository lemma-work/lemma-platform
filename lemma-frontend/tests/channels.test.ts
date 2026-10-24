import test from "node:test";
import assert from "node:assert/strict";
import {
    addable,
    answerSummary,
    asWaitingQuestion,
    channelTitle,
    composerChoices,
    inboxBadge,
    isMine,
    joinPages,
    lineDays,
    newestSeq,
    lineMeta,
    personName,
    previewOf,
    questionState,
    readChannelDetail,
    readChannelPage,
    readLine,
    readLinePage,
    sayQuestion,
    sayUnread,
    sectionsOf,
    senderLabel,
    sourceLabel,
    whoAnswers,
    withheldNote,
    withLine,
    type Channel,
    type Line,
} from "../src/channels/model.ts";
import { bellCount, readInbox } from "../src/channels/waiting.ts";

const ME = "user-dana";
const dana = { kind: "MEMBER", id: ME, name: "Dana Jones" };
const priya = { kind: "MEMBER", id: "user-priya", name: "Priya" };
const lem = { kind: "AGENT", id: "agent-lem", name: "Lem" };
const tomas = { kind: "CONTACT", id: "contact-tomas", name: "Tomás" };

const listed = readChannelPage({
    items: [
        { id: "c-direct", kind: "DIRECT", person: dana, sources: [{ platform: "whatsapp", title: null }], unread: { lines: 2, waiting: 1, read_seq: 7 }, last_seq: 9, created_at: "2026-10-18T09:00:00Z" },
        {
            id: "c-launch", kind: "GROUP", title: "launch", topic: "Beta", sources: [{ platform: "SLACK", title: "#launch" }, { platform: "TELEGRAM", title: "Crew" }, { platform: "SLACK", title: "#other" }],
            last_line: { sender: priya, text: "Thursday\nworks", created_at: "2026-10-18T10:00:00Z" },
            unread: { lines: 150, mentions: 1 }, created_at: "2026-10-01T09:00:00Z", last_line_at: "2026-10-18T10:00:00Z", visibility: "PRIVATE",
        },
        {
            id: "c-contact", kind: "CONTACT", person: tomas, attention: { since: "2026-10-18T10:00:00Z", reason: "Refund" }, created_at: "2026-10-10T09:00:00Z",
            agents_answer: false, hand_back_after_hours: 6, assignee: priya, status: "waiting_on_contact",
        },
        { kind: "GROUP", title: "no id, never drawn" },
    ],
    next_cursor: "cursor-2",
});
const [direct, launch, contact] = listed.items;

test("reads a page of channels, keeping its cursor and dropping a row without an id", () => {
    assert.equal(listed.items.length, 3);
    assert.equal(listed.nextBefore, "cursor-2");
    assert.equal(direct.sources[0].platform, "WHATSAPP");
    assert.deepEqual(direct.unread, { lines: 2, mentions: 0, waiting: 1, readSeq: 7 });
    assert.equal(direct.lastSeq, 9);
    assert.deepEqual(contact.attention, { since: "2026-10-18T10:00:00Z", reason: "Refund" });
    assert.equal(launch.archived, false);
    assert.equal(launch.visibility, "PRIVATE");
    assert.equal(direct.visibility, "PUBLIC");
    assert.equal(launch.lastMessageAt, "2026-10-18T10:00:00Z");
});

test("reads a contact's inbox fields off the channel itself", () => {
    assert.equal(contact.agentsAnswer, false);
    assert.equal(contact.handBackAfterHours, 6);
    assert.equal(contact.assignee?.name, "Priya");
    assert.equal(contact.status, "waiting_on_contact");
    // A channel that says nothing about its agent has it answering.
    assert.equal(direct.agentsAnswer, true);
    assert.equal(direct.handBackAfterHours, 12);
    assert.equal(direct.status, null);
});

test("names a chat the way its reader sees it", () => {
    /* Your private line is named by where you reach it, or is the notices
       the teammate sends you. */
    assert.equal(channelTitle({ kind: "DIRECT", title: null, person: null, sources: [{ platform: "WHATSAPP", title: null }] }, "Marketing"), "WhatsApp");
    assert.equal(channelTitle({ kind: "DIRECT", title: null, person: null, sources: [{ platform: "WHATSAPP", title: null }, { platform: "TELEGRAM", title: null }] }, "Marketing"), "WhatsApp and Telegram");
    assert.equal(channelTitle({ kind: "DIRECT", title: null, person: null, sources: [] }, "Marketing"), "Notices");
    assert.equal(channelTitle(contact, "Marketing"), "Tomás");
    assert.equal(channelTitle(launch, "Marketing"), "launch");
    // A group with no title of its own is called where it is.
    assert.equal(channelTitle({ kind: "GROUP", title: null, person: null, sources: [{ platform: "WHATSAPP", title: "Acme × Northwind" }] }, "M"), "Acme × Northwind");
    assert.equal(channelTitle({ kind: "GROUP", title: null, person: null, sources: [] }, "M"), "Untitled chat");
});

test("a row's preview is who said the last line, on one line", () => {
    assert.equal(previewOf(launch, ME), "Priya: Thursday works");
    assert.equal(previewOf({ lastLine: { sender: dana, text: "On it", createdAt: "" }, topic: null } as Pick<Channel, "lastLine" | "topic">, ME), "You: On it");
    assert.equal(previewOf({ lastLine: null, topic: "Beta" }, ME), "Beta");
    assert.equal(previewOf({ lastLine: null, topic: null }, ME), "Nothing said yet.");
});

test("cuts the list into the reader's own, the groups and -- for an editor -- the contacts", () => {
    assert.deepEqual(sectionsOf(listed.items, true).map((section) => [section.key, section.label, section.channels.length]), [
        ["direct", "Your channel", 1], ["groups", "Groups", 1], ["contacts", "Contacts", 1],
    ]);
    assert.deepEqual(sectionsOf(listed.items, false).map((section) => section.key), ["direct", "groups"]);
    // An empty section is left out.
    assert.deepEqual(sectionsOf([launch], true).map((section) => section.key), ["groups"]);
});

test("says what is new, and the sidebar's badge puts questions first", () => {
    assert.equal(sayUnread(direct.unread), "2 new, a question waiting on you");
    assert.equal(sayUnread(launch.unread), "100 or more new, mentions you");
    assert.equal(sayUnread({ lines: 0, mentions: 0, waiting: 0, readSeq: 0 }), null);
    /* What the bell counts, so the two agree. */
    assert.deepEqual(inboxBadge({ mentions: 2, waiting: 1 }), { count: 3, tone: "waiting", said: "1 question waiting on you, 2 mentions of you" });
    assert.deepEqual(inboxBadge({ mentions: 2, waiting: 0 }), { count: 2, tone: "mention", said: "2 mentions of you" });
    assert.deepEqual(inboxBadge({ mentions: 0, waiting: 0 }, 4), { count: 4, tone: "new", said: "4 new messages for you" });
    /* A group with new lines and nothing for the reader -- every public
       group they never joined -- is not counted: the bell says nothing new.
       The inbox still counts the channels with new lines, so the bell is
       handed an inbox as read, the way the sidebar hands it one. */
    const unjoined = readInbox({ totals: { channels: 11, mentions: 0, waiting: 0 } });
    assert.equal(inboxBadge(unjoined), null);
    assert.equal(bellCount(unjoined, 0).badge, null);
    for (const [totals, direct] of [[{ mentions: 2, waiting: 1 }, 3], [{ mentions: 0, waiting: 0 }, 0], [{ mentions: 5, waiting: 0 }, 0]] as const) {
        const bell = bellCount(totals, direct);
        assert.equal(inboxBadge(totals, direct)?.count.toString() ?? null, bell.badge);
        assert.equal(inboxBadge(totals, direct)?.said ?? "Nothing new for you", bell.said);
    }
});

const detail = readChannelDetail({
    id: "c-launch", kind: "GROUP", title: "launch", created_at: "2026-10-01T09:00:00Z",
    source_places: [
        { id: "s-slack", platform: "slack", kind: "CHAT", title: "#acme", surface_id: "surface-1" },
        { id: "s-tg", platform: "TELEGRAM", kind: "CHAT", title: null },
        { id: "s-wa", platform: "WHATSAPP", kind: "CHAT", title: "New", pending: true },
        { platform: "WEB" },
    ],
    members: [
        { person: dana, role: "owner" },
        { person: priya, role: "ruler" },
        { person: tomas },
        { person: lem, role: "member", answers: "EVERY_LINE" },
    ],
    me: { joined: true, notify: "MENTIONS", read_seq: 4, role: "owner", can_manage: true },
})!;

test("reads a channel's places, members and the reader's own membership", () => {
    assert.deepEqual(detail.places.map((place) => [place.id, place.platform, place.pending]), [["s-slack", "SLACK", false], ["s-tg", "TELEGRAM", false], ["s-wa", "WHATSAPP", true]]);
    assert.equal(detail.places[0].surfaceId, "surface-1");
    // A role this build does not know reads as what the person is: a contact
    // is a guest, anybody else a member. Only an agent answers.
    assert.deepEqual(detail.members.map((one) => [one.role, one.answers]), [["owner", null], ["member", null], ["guest", null], ["member", "EVERY_LINE"]]);
    assert.deepEqual(detail.me, { joined: true, notify: "MENTIONS", notifyVia: null, mutedUntil: null, readSeq: 4, role: "owner", canManage: true });
    // Somebody not in it has not joined, and hears of everything by default.
    assert.deepEqual(readChannelDetail({ id: "c", kind: "GROUP", created_at: "" })!.me, { joined: false, notify: "ALL", notifyVia: null, mutedUntil: null, readSeq: 0, role: null, canManage: false });
});

test("says where a line was said: its place and platform, or nothing for Lemma", () => {
    assert.equal(sourceLabel("s-slack", detail.places), "#acme (Slack)");
    assert.equal(sourceLabel("s-tg", detail.places), "Telegram");
    assert.equal(sourceLabel(null, detail.places), null);
    assert.equal(sourceLabel("gone", detail.places), null);
    const meta = lineMeta({ sourceId: "s-slack", createdAt: "2026-10-18T10:02:00Z" }, detail.places);
    assert.match(meta, /^#acme \(Slack\) · \d{1,2}[:.]\d{2}/);
});

test("a composer offers a note to members only where something outside hears, and a place only among several", () => {
    const choices = composerChoices(detail);
    assert.equal(choices.notes, true);
    // The WhatsApp group is not made yet, so it is not somewhere to send.
    assert.deepEqual(choices.places.map((place) => place.id), ["s-slack", "s-tg"]);
    assert.deepEqual(composerChoices({ places: [] }), { notes: false, places: [] });
    assert.deepEqual(composerChoices({ places: [detail.places[0]] }).places, []);
});

const line = (raw: Record<string, unknown>): Line => readLine({ id: "l", created_at: "2026-10-18T10:00:00Z", sender: dana, ...raw })!;

test("reads a line, and keeps no words of one withheld or deleted", () => {
    assert.equal(readLine({ text: "no id" }), null);
    assert.equal(line({ text: "kept", withheld: true }).text, null);
    assert.equal(line({ text: "kept", deleted: true }).text, null);
    assert.equal(line({ reply_count: 3 }).replyCount, 3);
    assert.equal(line({ members_only: true }).membersOnly, true);
    assert.equal(line({ question: { kind: "POLL" } }).interaction, null);
    assert.equal(line({ seq: 12 }).seq, 12);
});

test("an agent's line says whom it answered, unless it was the reader", () => {
    assert.equal(senderLabel(line({ sender: lem, on_behalf_of: priya }), ME), "Lem, answering Priya");
    assert.equal(senderLabel(line({ sender: lem, on_behalf_of: dana }), ME), "Lem");
    assert.equal(senderLabel(line({ sender: lem }), ME), "Lem");
    assert.equal(senderLabel(line({ sender: dana }), ME), "You");
    assert.equal(senderLabel(line({ sender: { kind: "CONTACT" } }), ME), "A contact");
    assert.equal(personName(null, ME), "Someone");
});

test("a withheld answer says which agent answered whom, privately, never what", () => {
    assert.equal(withheldNote(line({ sender: lem, on_behalf_of: priya, withheld: true }), ME), "Lem answered Priya privately");
    assert.equal(withheldNote(line({ sender: lem, withheld: true }), ME), "Lem answered someone privately");
    assert.equal(withheldNote(line({ sender: lem, text: "hi" }), ME), null);
});

test("an answer only its asker reads says so, and may be shared only by them", () => {
    const mine = line({ sender: lem, on_behalf_of: dana, text: "41%", visibility: "asker_only", can_share: true });
    assert.equal(mine.visibility, "asker_only");
    assert.equal(mine.canShare, true);
    assert.equal(line({ sender: lem, text: "hi" }).visibility, "channel");
    assert.equal(line({ sender: lem, can_share: true, deleted: true }).canShare, false);
});

test("only the reader's own lines, still there, are theirs to change", () => {
    assert.equal(isMine(line({ text: "mine" }), ME), true);
    assert.equal(isMine(line({ sender: priya, text: "hers" }), ME), false);
    assert.equal(isMine(line({ deleted: true }), ME), false);
    assert.equal(isMine(line({ text: "mine" }), null), false);
});

test("a question is the reader's to answer only when it awaits them", () => {
    const asked = (question: Record<string, unknown>) => line({ sender: lem, text: "Which day?", question: { kind: "ASK", question: { prompt: "Which day?" }, ...question } }).interaction!;
    assert.deepEqual(questionState(asked({ state: "OPEN", awaits: dana }), ME), { kind: "yours" });
    /* While an answer is being taken it is still the reader's: a claim that
       lapsed puts it back, so its buttons never go dead. Anybody else is told
       it is being answered. */
    assert.deepEqual(questionState(asked({ state: "ANSWERING", awaits: dana }), ME), { kind: "yours", answering: true });
    assert.equal(sayQuestion(questionState(asked({ state: "ANSWERING", awaits: priya }), ME)), "Being answered");
    assert.equal(sayQuestion(questionState(asked({ state: "OPEN", awaits: priya }), ME)), "Waiting on Priya");
    assert.equal(sayQuestion(questionState(asked({ state: "ANSWERED", answered_by: dana }), ME)), "Answered by you");
    assert.equal(sayQuestion(questionState(asked({ state: "ANSWERED" }), ME)), "Answered");
    assert.equal(sayQuestion(questionState(asked({ state: "EXPIRED" }), ME)), "No longer waiting: it expired");
    assert.equal(answerSummary({ choice: "Thursday" }), "Thursday");
    assert.equal(answerSummary({ decision: "DENY" }), "Denied");
    assert.equal(answerSummary({ decision: "APPROVE_ONCE" }), "Approved");
    assert.equal(answerSummary({ amount: 3 }), null);
    /* An agent's ask_user, as the API stores the answer. */
    assert.equal(answerSummary({ answers: { Drink: "Coffee" } }), "Coffee");
    assert.equal(answerSummary({ answers: { Drink: ["Tea", "Coffee"], When: "Now" } }), "Tea, Coffee; Now");
    assert.equal(answerSummary({ answers: {} }), null);
    assert.equal(answerSummary({ approved: true }), "Approved");
    assert.equal(answerSummary({ approved: false }), "Denied");
});

test("a question on a line is answered the way the bell answers one", () => {
    const asked = line({ id: "q", sender: lem, on_behalf_of: priya, text: "May I?", question: { kind: "APPROVAL", state: "OPEN", awaits: dana, question: { prompt: "May I?" } } });
    assert.deepEqual(asWaitingQuestion(asked, { id: "c-launch", kind: "GROUP", title: "launch" }, ME), {
        id: "q", channelId: "c-launch", channelTitle: "launch", channelKind: "GROUP", kind: "APPROVAL",
        text: "May I?", from: "Lem, answering Priya", askedAt: "2026-10-18T10:00:00Z", question: { prompt: "May I?" }, threadRootId: null,
    });
    assert.equal(asWaitingQuestion(line({}), { id: "c", kind: "GROUP", title: null }, ME), null);
});

test("pages read back as one run, oldest first, each line once", () => {
    const newest = readLinePage({ items: [{ id: "c", seq: 3, created_at: "3" }, { id: "d", seq: 4, created_at: "4" }], has_more: true, next_before_seq: 3 });
    const older = readLinePage({ items: [{ id: "a", seq: 1, created_at: "1" }, { id: "b", seq: 2, created_at: "2" }, { id: "c", seq: 3, created_at: "3" }], has_more: false, next_before_seq: null });
    // The page before is asked for by the line it starts at; the oldest has none.
    assert.equal(newest.nextBefore, 3);
    assert.equal(older.nextBefore, null);
    assert.equal(newestSeq(joinPages([newest, older])), 4);
    assert.equal(newestSeq([]), null);
    assert.deepEqual(joinPages([newest, older]).map((one) => one.id), ["a", "b", "c", "d"]);
    // A line posted here joins the newest page; one edited replaces itself.
    const posted = withLine([newest, older], line({ id: "e" }));
    assert.deepEqual(joinPages(posted).map((one) => one.id), ["a", "b", "c", "d", "e"]);
    const edited = withLine(posted, line({ id: "b", text: "changed" }));
    assert.equal(joinPages(edited).find((one) => one.id === "b")?.text, "changed");
    assert.deepEqual(withLine([], line({ id: "x" })).map((page) => page.items.length), [1]);
});

test("cuts a timeline at each day, named the way a chat names them", () => {
    const now = new Date(2026, 9, 18, 15, 0);
    const at = (days: number, hour: number) => ({ createdAt: new Date(2026, 9, 18 - days, hour).toISOString() });
    const days = lineDays([at(9, 10), at(1, 9), at(1, 18), at(0, 8), { createdAt: "not a date" }], now);
    assert.deepEqual(days.map((day) => [day.label === "Today" || day.label === "Yesterday" ? day.label : "earlier", day.lines.length]), [["earlier", 1], ["Yesterday", 2], ["Today", 1]]);
});

test("says who answers when nobody is named", () => {
    const on = { agentsAnswer: true, handBackAfterHours: 12 };
    assert.equal(whoAnswers({ kind: "DIRECT", assignee: null, ...on }, "Marketing", ME), "Marketing answers everything you write here.");
    assert.equal(whoAnswers({ kind: "GROUP", assignee: null, ...on }, "Marketing", ME), "@mention an agent to have it answer.");
    assert.equal(whoAnswers({ kind: "CONTACT", assignee: null, ...on }, "Marketing", ME), "Marketing’s agent answers them. Write here to answer them yourself.");
    assert.equal(
        whoAnswers({ kind: "CONTACT", assignee: { kind: "MEMBER", id: ME, name: null }, ...on }, "M", ME),
        "You are answering here; its agent answers again 12 hours after your last message.",
    );
    assert.equal(
        whoAnswers({ kind: "CONTACT", assignee: priya as never, ...on }, "M", ME),
        "Priya is answering here; its agent answers again 12 hours after their last message.",
    );
});

test("a contact whose agent is off is answered by members alone, whoever stepped in", () => {
    const off = { agentsAnswer: false, handBackAfterHours: 12 };
    assert.equal(whoAnswers({ kind: "CONTACT", assignee: null, ...off }, "M", ME), "The agent is off here: only people answer them.");
    assert.equal(whoAnswers({ kind: "CONTACT", assignee: priya as never, ...off }, "M", ME), "The agent is off here: only people answer them.");
});

test("offers to add the space's people not in the channel yet, never a teammate", () => {
    const members = [
        { userId: ME, kind: "person", name: "Dana" },
        { userId: "user-priya", kind: "person", name: "Priya" },
        { userId: "user-jo", kind: "person", name: "Jo" },
        { userId: "pod-bot", kind: "teammate", name: "Lem" },
        { kind: "person", name: "Invited, no account yet" },
    ];
    assert.deepEqual(addable(members, detail.members).map((member) => member.name), ["Jo"]);
});

test("a group shows its agents' answers to everyone unless the API says it keeps them to the asker", () => {
    assert.equal(readChannelDetail({ id: "c", kind: "GROUP", created_at: "" })!.answersShared, true);
    assert.equal(readChannelDetail({ id: "c", kind: "GROUP", created_at: "", answers_shared: true })!.answersShared, true);
    assert.equal(readChannelDetail({ id: "c", kind: "GROUP", created_at: "", answers_shared: false })!.answersShared, false);
});
