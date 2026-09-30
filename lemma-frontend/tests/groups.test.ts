import test from "node:test";
import assert from "node:assert/strict";
import {
    answeredNote,
    answering,
    arrivedSince,
    byActivity,
    groupHeadline,
    groupStarters,
    groupSubline,
    howToRemove,
    inviteMailto,
    lineAuthor,
    offersTakeOn,
    personInitials,
    readGroup,
    readGroupDetail,
    readGroups,
    readTimeline,
    sayAnswering,
    sayAnsweringFully,
    sayStanding,
    sayWaiting,
    sinceShort,
    slackChannelUrl,
    slackInvite,
    standing,
    timelineDays,
    waitingGroups,
    waitingTotal,
    whatsappShareUrl,
} from "../src/data/groups.ts";
import type { Group, GroupDetail, GroupLine, Member, Surface } from "../src/data/types.ts";

/** A space's groups: what the Groups page, a group's page and the sheets
 *  read off the wire, and every sentence they say about it. */

function group(partial: Partial<Group> = {}): Group {
    return {
        id: "g-launch",
        surfaceName: "telegram",
        platform: "TELEGRAM",
        title: "Launch crew",
        externalId: "-100183",
        inviteLink: null,
        pending: false,
        sharedExternally: false,
        owner: { userId: "me", name: null },
        answersOutsiders: true,
        welcomesOutsiders: true,
        peopleInSpace: 2,
        peopleOutside: 3,
        lastMessageAt: "2026-10-01T09:48:00Z",
        waitingForYou: 0,
        updatedAt: "2026-10-01T09:48:00Z",
        ...partial,
    };
}

function line(partial: Partial<GroupLine> = {}): GroupLine {
    return {
        authorName: "Mara Okafor",
        authorExternalId: "tg-1",
        inSpace: false,
        fromBot: false,
        text: "Is partner export ready yet?",
        at: "2026-10-01T09:14:00Z",
        answeredName: null,
        answeredFromPublic: false,
        ...partial,
    };
}

test("reads a group off the space-wide list, and refuses one without an id", () => {
    const read = readGroup({
        id: "g1", surface_name: "telegram", platform: "telegram", title: " Launch crew ", external_channel_id: "-100",
        shared_externally: false, owner: { user_id: "u1", display_name: "Priya" }, answers_outsiders: true,
        welcomes_outsiders: true, people_in_pod: 2, people_outside: 3, last_message_at: "2026-10-01T09:48:00Z",
        waiting_for_you: 1, updated_at: "2026-10-01T09:48:00Z",
    });
    assert.equal(read?.surfaceName, "telegram");
    assert.equal(read?.platform, "TELEGRAM");
    assert.equal(read?.title, "Launch crew");
    assert.equal(read?.peopleInSpace, 2);
    assert.equal(read?.peopleOutside, 3);
    assert.equal(read?.waitingForYou, 1);
    assert.deepEqual(read?.owner, { userId: "u1", name: "Priya" });
    assert.equal(readGroup({ title: "no id" }), null);
    // Slack keeps its own history, so the space counts nobody there.
    const slack = readGroup({ id: "s1", platform: "SLACK", shared_externally: true, people_in_pod: null, people_outside: null });
    assert.equal(slack?.sharedExternally, true);
    assert.equal(slack?.peopleInSpace, null);
    assert.equal(slack?.waitingForYou, 0);
    assert.deepEqual(readGroups({ items: [{ id: "a" }, "junk", { id: "" }] }).map((one) => one.id), ["a"]);
    assert.deepEqual(readGroups(undefined), []);
});

test("an opened group carries its people and what is waiting, and nothing it cannot answer through", () => {
    const detail = readGroupDetail({
        id: "g1", platform: "TELEGRAM",
        people: [
            { name: "Priya", external_id: "tg-2", user_id: "priya", in_pod: true },
            { name: "", external_id: "tg-9", user_id: null, in_pod: false },
            { external_id: null },
        ],
        waiting: [
            { notification_id: "n1", question: "Can we put our logo on the page?", asked_at: "2026-10-01T09:48:00Z" },
            { question: "No notification to answer it through" },
        ],
    });
    assert.deepEqual(detail?.people, [
        { name: "Priya", externalId: "tg-2", userId: "priya", inSpace: true },
        // No name: the platform's id is all there is to call them.
        { name: "tg-9", externalId: "tg-9", userId: null, inSpace: false },
    ]);
    assert.deepEqual(detail?.waiting.map((one) => one.notificationId), ["n1"]);
    assert.equal(readGroupDetail({}), null);
});

test("what was said reads oldest first, the bot's own lines counted in the space", () => {
    const lines = readTimeline({ items: [
        { text: "later", at: "2026-10-01T10:00:00Z", in_pod: false, from_bot: false },
        { text: "answer", at: "2026-10-01T09:00:00Z", in_pod: false, from_bot: true, answered_name: "Mara", answered_from_public: true },
        { text: "same second, second", at: "2026-10-01T09:00:00Z", in_pod: true, from_bot: false },
        { at: "2026-10-01T09:00:00Z" },
        { text: "no time" },
    ] });
    assert.deepEqual(lines.map((one) => one.text), ["answer", "same second, second", "later"]);
    assert.equal(lines[0].inSpace, true);
    assert.equal(lines[0].answeredFromPublic, true);
});

test("says who answers people outside the space, and never a person who is not", () => {
    const members: Member[] = [{ id: "m1", name: "Priya Shah", initials: "PS", kind: "person", role: "Member", can: "", userId: "priya" }];
    assert.deepEqual(answering(group(), "me"), { kind: "you" });
    assert.deepEqual(answering(group({ owner: { userId: "priya", name: null } }), "me", members), { kind: "someone", name: "Priya Shah" });
    assert.deepEqual(answering(group({ owner: { userId: "gone", name: null } }), "me"), { kind: "someone", name: null });
    assert.deepEqual(answering(group({ owner: null }), "me"), { kind: "nobody" });
    // Off is off, whoever looks after it.
    assert.deepEqual(answering(group({ answersOutsiders: false }), "me"), { kind: "off" });
    assert.deepEqual(answering(group({ pending: true }), "me"), { kind: "pending" });
    // Inside the company, a colleague who is not in the space is invited, not answered.
    assert.deepEqual(answering(group({ platform: "SLACK", sharedExternally: false }), "me"), { kind: "invited" });
    assert.deepEqual(answering(group({ platform: "SLACK", sharedExternally: true, owner: null }), "me"), { kind: "nobody" });

    assert.equal(sayAnswering({ kind: "you" }, "Marketing"), "You answer for them");
    assert.equal(sayAnswering({ kind: "someone", name: "Priya" }, "Marketing"), "Priya answers for them");
    assert.equal(sayAnswering({ kind: "someone", name: null }, "Marketing"), "Someone in Marketing answers for them");
    assert.equal(sayAnswering({ kind: "nobody" }, "Marketing"), "Nobody answers them");
    assert.equal(sayAnswering({ kind: "invited" }, "Marketing"), "Invited to join");
    assert.equal(sayAnsweringFully({ kind: "you" }, "Marketing"), "You answer for people outside Marketing");
    assert.equal(sayAnsweringFully({ kind: "pending" }, "Marketing"), "Being created");
    assert.equal(sayAnsweringFully({ kind: "off" }, "Marketing"), "People outside Marketing are not answered");
});

test("offers to take a group on from nobody or from somebody else, never from you", () => {
    assert.equal(offersTakeOn({ kind: "nobody" }), true);
    assert.equal(offersTakeOn({ kind: "someone", name: "Priya" }), true);
    assert.equal(offersTakeOn({ kind: "you" }), false);
    // Switched off, turning it on is what makes you the one who answers.
    assert.equal(offersTakeOn({ kind: "off" }), false);
    assert.equal(offersTakeOn({ kind: "invited" }), false);
});

test("a row says the platform, the agent when it is not the space's own, and who has spoken", () => {
    assert.equal(groupSubline(group(), "Marketing"), "Telegram · 2 in Marketing, 3 outside");
    assert.equal(groupSubline(group({ peopleInSpace: 0, peopleOutside: 1 }), "Marketing"), "Telegram · 1 person outside Marketing");
    assert.equal(groupSubline(group({ peopleOutside: 0 }), "Marketing"), "Telegram · 2 people in Marketing");
    assert.equal(groupSubline(group({ peopleInSpace: 0, peopleOutside: 0 }), "Marketing"), "Telegram · nobody has spoken yet");
    const asked = new Date("2026-10-01T09:49:00Z");
    assert.equal(groupSubline(group({ platform: "WHATSAPP", pending: true }), "Marketing", null, asked), "WhatsApp · being created, a few seconds");
    // WhatsApp confirms in seconds; an hour on, "a few seconds" would be a promise it is not keeping.
    const anHourOn = new Date("2026-10-01T10:48:00Z");
    assert.equal(groupSubline(group({ platform: "WHATSAPP", pending: true }), "Marketing", null, anHourOn), "WhatsApp · waiting for WhatsApp to confirm it");
    assert.equal(groupSubline(group({ platform: "SLACK", sharedExternally: true, peopleInSpace: null, peopleOutside: null }), "Marketing"), "Slack · shared with another company");
    assert.equal(groupSubline(group({ platform: "SLACK", peopleInSpace: null, peopleOutside: null }), "Marketing"), "Slack · inside your company");
    assert.equal(groupSubline(group(), "Marketing", "Researcher"), "Telegram · Researcher · 2 in Marketing, 3 outside");
});

test("a group's page says what the place is and who has spoken there", () => {
    const detail: GroupDetail = { ...group(), people: [
        { name: "Priya", externalId: "a", userId: "priya", inSpace: true },
        { name: "Mara", externalId: "b", userId: null, inSpace: false },
    ], waiting: [] };
    assert.equal(groupHeadline(detail), "Telegram group · 2 people have spoken here");
    assert.equal(groupHeadline({ ...detail, people: [] }), "Telegram group · nobody has spoken yet");
    assert.equal(groupHeadline({ ...detail, platform: "SLACK", sharedExternally: true }), "Slack channel · shared with another company");
    assert.equal(groupHeadline({ ...detail, platform: "WHATSAPP", pending: true }, "Researcher"), "WhatsApp group · Researcher answers there · being created");
});

test("when a row last moved, short enough for a column", () => {
    const now = new Date(2026, 9, 1, 12, 0);
    const back = (minutes: number) => new Date(now.getTime() - minutes * 60_000).toISOString();
    assert.equal(sinceShort(back(0), now), "Now");
    assert.equal(sinceShort(back(12), now), "12 min");
    assert.equal(sinceShort(back(150), now), "2 h");
    assert.equal(sinceShort(new Date(2026, 8, 30, 9, 0).toISOString(), now), "Yesterday");
    assert.equal(sinceShort(new Date(2026, 8, 28, 9, 0).toISOString(), now), "3 days");
    assert.notEqual(sinceShort(new Date(2026, 8, 1, 9, 0).toISOString(), now), "");
    assert.equal(sinceShort(null, now), "");
    assert.equal(sinceShort("not a date", now), "");
    // A clock a little ahead is still now, not "-1 min".
    assert.equal(sinceShort(new Date(now.getTime() + 30_000).toISOString(), now), "Now");
});

test("the most recently active group comes first, a pending one by when it was asked for", () => {
    const quiet = group({ id: "quiet", lastMessageAt: "2026-09-28T09:00:00Z", updatedAt: "2026-09-28T09:00:00Z" });
    const busy = group({ id: "busy", lastMessageAt: "2026-10-01T09:00:00Z" });
    const making = group({ id: "making", pending: true, lastMessageAt: null, updatedAt: "2026-10-01T11:00:00Z" });
    assert.deepEqual(byActivity([quiet, busy, making]).map((one) => one.id), ["making", "busy", "quiet"]);
});

test("counts what is waiting on you across the space, most first", () => {
    const groups = [group({ id: "a", waitingForYou: 1 }), group({ id: "b" }), group({ id: "c", waitingForYou: 2 })];
    assert.equal(waitingTotal(groups), 3);
    assert.deepEqual(waitingGroups(groups).map((one) => one.id), ["c", "a"]);
    assert.equal(sayWaiting(1), "1 question waiting for you");
    assert.equal(sayWaiting(4), "4 questions waiting for you");
});

test("offers a platform only where the space's own bot is connected, and says what is missing otherwise", () => {
    const surface = (partial: Partial<Surface>): Surface => ({
        id: "s", platform: "TELEGRAM", name: "telegram", mine: true, agentName: "Marketing", handle: "@m_bot", active: true, ...partial,
    });
    const starters = groupStarters([
        surface({ platform: "WHATSAPP", name: "whatsapp" }),
        // An agent's own Telegram bot is not the space's to start groups with.
        surface({ platform: "TELEGRAM", name: "researcher", mine: false }),
        surface({ platform: "SLACK", name: "slack", active: false, status: "INACTIVE" }),
    ]);
    assert.deepEqual(starters.map((one) => [one.platform, one.state, one.surface?.name ?? null]), [
        ["WHATSAPP", "ready", "whatsapp"],
        ["TELEGRAM", "missing", null],
        ["SLACK", "unwell", "slack"],
    ]);
});

test("a sheet waiting on Telegram or Slack sees only a group that was not there before", () => {
    const before = new Set(["old"]);
    const groups = [group({ id: "old" }), group({ id: "elsewhere", surfaceName: "researcher" }), group({ id: "new" })];
    assert.equal(arrivedSince(before, groups, "telegram")?.id, "new");
    assert.equal(arrivedSince(new Set(["old", "new"]), groups, "telegram"), null);
});

test("an invite link goes out with the words already written", () => {
    const link = "https://chat.whatsapp.com/Kx4vQ9";
    assert.equal(whatsappShareUrl("Acme × Northwind", link), "https://wa.me/?text=" + encodeURIComponent("Join “Acme × Northwind” on WhatsApp: " + link));
    const mail = new URL(inviteMailto("Acme & Co", link));
    assert.equal(mail.protocol, "mailto:");
    assert.equal(mail.searchParams.get("subject"), "Join Acme & Co on WhatsApp");
    assert.equal(mail.searchParams.get("body"), "Join “Acme & Co” on WhatsApp: " + link);
});

test("Slack is told who to invite, by handle when there is one", () => {
    assert.equal(slackInvite("@marketing", "Marketing"), "/invite @marketing");
    assert.equal(slackInvite("", "Marketing"), "/invite @Marketing");
    assert.equal(slackChannelUrl("C07PARTNERS"), "https://slack.com/app_redirect?channel=C07PARTNERS");
    assert.equal(slackChannelUrl(null), null);
});

test("a person is in the space, a Lemma account outside it, or somebody Lemma cannot place", () => {
    assert.equal(standing({ name: "Priya", externalId: "a", userId: "p", inSpace: true }), "in");
    assert.equal(standing({ name: "Mara", externalId: "b", userId: "m", inSpace: false }), "outside");
    assert.equal(standing({ name: "@kbrandt", externalId: "c", userId: null, inSpace: false }), "unknown");
    assert.equal(sayStanding("in", "Marketing"), "In Marketing");
    assert.equal(sayStanding("outside", "Marketing"), "Not in Marketing");
    assert.equal(sayStanding("unknown", "Marketing"), "Not recognised");
    assert.equal(personInitials("Tomás Rivera"), "TR");
    assert.equal(personInitials("@kbrandt"), "K");
    assert.equal(personInitials("+44 7700 900123"), "47");
    assert.equal(personInitials("Anyone", "unknown"), "?");
});

test("a line is the reader's own when its author is linked to them", () => {
    const people = [{ name: "Dana Jones", externalId: "tg-me", userId: "me", inSpace: true }];
    assert.deepEqual(lineAuthor(line({ authorExternalId: "tg-me", authorName: "Dana Jones", inSpace: true }), people, "me", "Marketing"), { name: "You", you: true });
    assert.deepEqual(lineAuthor(line({ fromBot: true, authorName: null, authorExternalId: null }), people, "me", "Marketing"), { name: "Marketing", you: false });
    assert.deepEqual(lineAuthor(line(), people, "me", "Marketing"), { name: "Mara Okafor", you: false });
    assert.deepEqual(lineAuthor(line({ authorName: null, authorExternalId: "tg-7" }), [], null, "Marketing"), { name: "tg-7", you: false });
});

test("the bot's lines say whom they answered, and on whose access", () => {
    assert.equal(answeredNote(line()), null);
    assert.equal(answeredNote(line({ fromBot: true, answeredName: "Mara", answeredFromPublic: true })), "Answered Mara from what is Public");
    assert.equal(answeredNote(line({ fromBot: true, answeredFromPublic: true })), "Answered from what is Public");
    assert.equal(answeredNote(line({ fromBot: true, answeredName: "Priya" })), "Answered Priya, with their access");
    assert.equal(answeredNote(line({ fromBot: true })), null);
});

test("the timeline is cut at each day, today and yesterday by name", () => {
    const now = new Date(2026, 9, 1, 12, 0);
    const days = timelineDays([
        line({ at: new Date(2026, 8, 30, 9, 0).toISOString() }),
        line({ at: new Date(2026, 9, 1, 9, 0).toISOString() }),
        line({ at: new Date(2026, 9, 1, 10, 0).toISOString() }),
        line({ at: "not a time" }),
    ], now);
    assert.deepEqual(days.map((day) => [day.label, day.lines.length]), [["Yesterday", 1], ["Today", 2]]);
});

test("says how to take the bot out only where a person can", () => {
    assert.equal(howToRemove("TELEGRAM", "Marketing"), "To take Marketing out, remove it from the group in Telegram.");
    assert.equal(howToRemove("SLACK", "@marketing"), "To take @marketing out, send /remove @marketing in the channel.");
    // A WhatsApp group is one the bot made.
    assert.equal(howToRemove("WHATSAPP", "Marketing"), null);
});
