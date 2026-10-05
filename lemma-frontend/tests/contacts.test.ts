import test from "node:test";
import assert from "node:assert/strict";
import type { ContactResponse, WebWidgetResponse } from "lemma-sdk";
import {
    EMPTY_WIDGET_DRAFT,
    answerChoices,
    columnLabel,
    contactName,
    contactSubline,
    draftFields,
    followUpRefusal,
    formRequest,
    formSnippet,
    handleText,
    readContact,
    readOrigins,
    readWidget,
    sayCap,
    vouchedBy,
    widgetKindLine,
    widgetProblem,
    type FormColumn,
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
        answer: "something-new", looked_after_by: null, form_function: null, form_requires_code: false,
        created_at: "2026-10-01T10:00:00Z", embed: "<script></script>",
    } as unknown as WebWidgetResponse);
    assert.equal(widget.answer, "off");
    assert.equal(widget.kind, "chat");
});

test("origins are tidied, and only secure ones are accepted", () => {
    assert.deepEqual(readOrigins("https://Shop.example/ \n https://shop.example, http://localhost:3000"), ["https://shop.example", "http://localhost:3000"]);
    const draft: WidgetDraft = { ...EMPTY_WIDGET_DRAFT, name: "Chat", kind: "chat", origins: "http://shop.example" };
    assert.match(widgetProblem(draft) ?? "", /https/);
    assert.equal(widgetProblem({ ...draft, origins: "" }), null);
    assert.equal(widgetProblem({ ...draft, origins: "", kind: "form" }), "Choose the table answers go into.");
});

const COLUMNS: FormColumn[] = [
    { name: "full_name", required: true, description: null, suggested: "text", inputs: ["text", "long", "email", "phone"] },
    { name: "work_email", required: false, description: null, suggested: "email", inputs: ["text", "long", "email", "phone"] },
    { name: "internal_notes", required: false, description: null, suggested: "long", inputs: ["text", "long", "email", "phone"] },
];

test("a table's columns become a form with everything ticked and the needed ones locked", () => {
    const fields = draftFields(COLUMNS);
    assert.deepEqual(fields.map((field) => [field.label, field.on, field.locked]), [
        ["Full name", true, true],
        ["Work email", true, false],
        ["Internal notes", true, false],
    ]);
    assert.equal(columnLabel("starts_on"), "Starts on");
});

test("only the ticked columns are asked for, and a needed one is always required", () => {
    const fields = draftFields(COLUMNS).map((field) =>
        field.column === "internal_notes" ? { ...field, on: false } : field.column === "full_name" ? { ...field, required: false, label: " " } : field,
    );
    const draft: WidgetDraft = { ...EMPTY_WIDGET_DRAFT, name: "Sign-up", formTable: "signups", formFields: fields, formIntro: "  ", formConfirmation: "See you!" };
    assert.equal(widgetProblem(draft), null);
    assert.deepEqual(formRequest(draft), {
        table: "signups",
        fields: [
            { column: "full_name", label: "Full name", input: "text", required: true },
            { column: "work_email", label: "Work email", input: "email", required: false },
        ],
        intro: null,
        confirmation: "See you!",
    });
    const nothing = { ...draft, formFields: fields.map((field) => ({ ...field, on: false })) };
    assert.equal(widgetProblem(nothing), "Tick at least one thing to ask.");
});

test("a table form says where answers go, and its code is just the script", () => {
    const widget = readWidget({
        id: "w2", name: "Sign-up", agent_id: "a", kind: "form", public_key: "pk_2", allowed_origins: [],
        answer: "anyone", looked_after_by: null, form_function: null, form_requires_code: false,
        form: { table: "signups", fields: [{ column: "full_name", label: "Name", input: "text", column_type: "TEXT", required: true }] },
        created_at: "2026-10-01T10:00:00Z", embed: "<script data-lemma-key=pk_2></script>", page_url: "https://api.example/public/web/pk_2/page",
    } as unknown as WebWidgetResponse);
    assert.equal(widgetKindLine(widget), "Form · adds a row to signups");
    assert.equal(formSnippet(widget), "<script data-lemma-key=pk_2></script>");
    assert.equal(widget.pageUrl, "https://api.example/public/web/pk_2/page");
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
