import test from "node:test";
import assert from "node:assert/strict";
import type { ContactResponse, WebWidgetResponse } from "lemma-sdk";
import {
    EMPTY_WIDGET_DRAFT,
    answerChoices,
    contactName,
    groupsNote,
    contactSubline,
    everyPage,
    outreachProblem,
    outreachSaid,
    handleText,
    readContact,
    readOrigins,
    readWidget,
    sayCap,
    vouchedBy,
    widgetProblem,
    writableHandles,
    type WidgetDraft,
} from "../src/data/contacts.ts";

const wire = {
    id: "c1",
    display_name: null,
    created_at: "2026-10-01T10:00:00Z",
    identities: [
        { kind: "HOST", value: "w1:cust-42", strength: "HOST", verified_at: "2026-10-01T10:00:00Z" },
        { kind: "EMAIL", value: "ana@shop.example", strength: "CODE", verified_at: "2026-10-01T10:00:00Z" },
    ],
} as unknown as ContactResponse;

test("a contact with no name is called by a handle a person can read", () => {
    const contact = readContact(wire);
    assert.equal(contactName(contact), "Signed in on your site");
    assert.equal(contactSubline(contact), "ana@shop.example");
});

test("a host id is never shown; a phone number is", () => {
    assert.equal(handleText({ kind: "HOST", value: "w1:cust-42", strength: "HOST" }), "Signed in on your site");
    assert.equal(handleText({ kind: "PHONE", value: "447700900123", strength: "CHANNEL" }), "+447700900123");
    assert.equal(vouchedBy({ kind: "EMAIL", value: "x", strength: "CHANNEL" }), "Verified by their mail service");
    assert.equal(vouchedBy({ kind: "EMAIL", value: "x", strength: "CODE" }), "Confirmed with a code");
});

test("a website visitor nobody vouched for reads as one, never as a session id", () => {
    const visitor = readContact({
        id: "c2",
        display_name: null,
        created_at: "2026-10-02T10:00:00Z",
        is_anonymous: true,
        identities: [{ kind: "WEB", value: "5f0c1e2a-0000-4000-8000-000000000001", strength: "SESSION", verified_at: "2026-10-02T10:00:00Z" }],
    } as unknown as ContactResponse);
    assert.equal(visitor.anonymous, true);
    assert.equal(contactName(visitor), "A visitor to your website");
    assert.equal(contactSubline(visitor), "Not confirmed yet");
    assert.equal(handleText(visitor.handles[0]), "Chat on your website");
    assert.equal(vouchedBy(visitor.handles[0]), "Not confirmed yet");
});

test("a Slack handle says Slack, whether a colleague's or another company's", () => {
    assert.equal(handleText({ kind: "SLACK", value: "T9:U7", strength: "CHANNEL" }), "Slack");
});

test("a widget reads as the app shows it, unknown answers as off", () => {
    const widget = readWidget({
        id: "w1", name: "Shop chat", agent_id: "a", kind: "chat", public_key: "pk_1", allowed_origins: [],
        answer: "something-new", looked_after_by: null,
        created_at: "2026-10-01T10:00:00Z", embed: "<script></script>", page_url: "https://api.example/public/web/pk_1/page",
    } as unknown as WebWidgetResponse);
    assert.equal(widget.answer, "off");
    assert.equal(widget.pageUrl, "https://api.example/public/web/pk_1/page");
});

test("origins are tidied, and only secure ones are accepted", () => {
    assert.deepEqual(readOrigins("https://Shop.example/ \n https://shop.example, http://localhost:3000"), ["https://shop.example", "http://localhost:3000"]);
    const draft: WidgetDraft = { ...EMPTY_WIDGET_DRAFT, name: "Chat", origins: "http://shop.example" };
    assert.match(widgetProblem(draft) ?? "", /https/);
    assert.equal(widgetProblem({ ...draft, origins: "" }), null);
    assert.equal(widgetProblem({ ...draft, name: " ", origins: "" }), "Give it a name.");
});

test("every refusal says why in words", () => {
    /* The API's refusal of the message is a sentence, said as given. */
    const refused = (statusCode: number, message: string, name = "ApiError") => Object.assign(new Error(message), { statusCode, name });
    assert.equal(outreachProblem(refused(409, "They asked not to be written to there.")), "They asked not to be written to there.");
    assert.equal(outreachProblem(refused(422, "WhatsApp only allows a reply within 24 hours of their last message.")), "WhatsApp only allows a reply within 24 hours of their last message.");
    /* Anything that is not a sentence for a person is never shown raw. */
    assert.equal(outreachProblem(refused(422, '{"detail":[{"loc":["body","text"]}]}')), "That can’t be sent to them there.");
    assert.equal(outreachProblem(refused(409, "Conflict")), "They can’t be written to there now.");
    assert.equal(outreachProblem(refused(500, "Traceback (most recent call last): KeyError")), "Lemma couldn’t send it just now. Try again.");
    assert.equal(outreachProblem(refused(503, "Service Unavailable")), "Sending isn’t available right now. Try again in a few minutes.");
    assert.equal(outreachProblem(refused(404, "Contact not found")), "They are not one of this pod’s contacts any more.");
    assert.match(outreachProblem(Object.assign(new Error("fetch failed"), { name: "NetworkError" })), /Couldn’t reach Lemma/);
    assert.equal(outreachProblem(undefined), "Couldn’t send it. Try again.");
});

test("the cap says what was spent, with or without one", () => {
    assert.equal(sayCap({ limit: 25, spentThisMonth: 3.2 }), "$3.20 of $25 this month");
    assert.equal(sayCap({ limit: null, spentThisMonth: 0 }), "$0.00 this month, no cap set");
});

test("the three answers are offered in order of who gets in", () => {
    assert.deepEqual(answerChoices("Kit").map((choice) => choice.value), ["off", "known", "anyone"]);
});

test("each answer is said of private messages, and groups are left to their own switch", () => {
    for (const choice of answerChoices("Kit")) assert.match(choice.note, /privately/);
    assert.equal(groupsNote("Kit"), "Groups follow “Answer people outside Kit”.");
});

test("a list is followed to its last page, and a page that repeats ends it", async () => {
    const pages: Record<string, { items: number[]; next: string | null }> = {
        start: { items: [1, 2], next: "b" },
        b: { items: [3], next: "c" },
        c: { items: [4], next: null },
    };
    const asked: (string | undefined)[] = [];
    const all = await everyPage(async (cursor) => {
        asked.push(cursor);
        return pages[cursor ?? "start"];
    });
    assert.deepEqual(all, [1, 2, 3, 4]);
    assert.deepEqual(asked, [undefined, "b", "c"]);

    let calls = 0;
    const looping = await everyPage(async () => {
        calls += 1;
        return { items: [calls], next: "same" };
    });
    assert.deepEqual(looping, [1, 2]);
});

test("a visitor who left no address is written to in their chat on the website", () => {
    const web = { kind: "WEB" as const, value: "5f0c1e2a-0000-4000-8000-000000000001", strength: "SESSION" };
    const host = { kind: "HOST" as const, value: "host:default:cust-42", strength: "HOST" };
    const email = { kind: "EMAIL" as const, value: "ana@shop.example", strength: "CODE" };
    assert.deepEqual(writableHandles({ handles: [web] }), [web]);
    // The chat their site signed them in to first, and the browser's too:
    // either may be the one that has ended.
    assert.deepEqual(writableHandles({ handles: [web, host] }), [host, web]);
    // An address they confirmed is how they are written to; the chat is not offered.
    assert.deepEqual(writableHandles({ handles: [web, email] }), [email]);
    assert.deepEqual(writableHandles({ handles: [{ kind: "SLACK", value: "T9:U7", strength: "CHANNEL" }] }), []);
    assert.equal(outreachSaid({ status: "SENT", platform: "WEB", reason: null }), "In their chat on your website, for when they next open it.");
    assert.equal(outreachSaid({ status: "PENDING", platform: "WEB", reason: null }), "In their channel, on its way to their chat on your website.");
    assert.equal(outreachSaid({ status: "SENT", platform: "RESEND", reason: null }), "Sent by email. It is in their channel.");
});
