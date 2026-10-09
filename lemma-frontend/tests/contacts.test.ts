import test from "node:test";
import assert from "node:assert/strict";
import type { ContactResponse, WebWidgetResponse } from "lemma-sdk";
import {
    EMPTY_WIDGET_DRAFT,
    answerChoices,
    contactName,
    contactSubline,
    everyPage,
    followUpRefusal,
    handleText,
    readContact,
    readOrigins,
    readWidget,
    sayCap,
    vouchedBy,
    widgetProblem,
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
    assert.match(followUpRefusal("outside_window"), /WhatsApp/);
    assert.match(followUpRefusal("unsubscribed"), /asked not to/);
    assert.equal(followUpRefusal(undefined), "Couldn’t send it. Try again.");
});

test("the cap says what was spent, with or without one", () => {
    assert.equal(sayCap({ limit: 25, spentThisMonth: 3.2 }), "$3.20 of $25 this month");
    assert.equal(sayCap({ limit: null, spentThisMonth: 0 }), "$0.00 this month, no cap set");
});

test("the three answers are offered in order of who gets in", () => {
    assert.deepEqual(answerChoices("Kit").map((choice) => choice.value), ["off", "known", "anyone"]);
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
