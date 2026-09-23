import test from "node:test";
import assert from "node:assert/strict";
import {
    badgeCount,
    conflictNote,
    deliveryNote,
    formTarget,
    isUnread,
    moveFor,
    outcomeOf,
    waitingSchema,
    waitingUiSchema,
    type Notification,
} from "../src/shell/notification-state.ts";

function notification(over: Partial<Notification> = {}): Notification {
    return {
        id: "n1",
        title: "Approve the vendor list",
        body: "Three names need a yes or a no.",
        status: "OPEN",
        delivery_status: "DELIVERED",
        expects_response: true,
        awaiting_response: true,
        responds_through_action: false,
        created_at: "2026-09-18T09:00:00Z",
        ...over,
    };
}

test("a notification that wants words offers a place to type them", () => {
    assert.equal(moveFor(notification()), "answer");
});

test("a notification answered by its own form does not offer a text box", () => {
    // The form is validated against the node's schema on the server. A text box
    // that pretends to stand in for it produces an answer that cannot be taken.
    assert.equal(moveFor(notification({ responds_through_action: true })), "elsewhere");
});

test("a notification that asked for nothing can only be dismissed", () => {
    assert.equal(moveFor(notification({ awaiting_response: false, expects_response: false })), "acknowledge");
});

test("a notification that is over offers nothing", () => {
    for (const status of ["RESPONDED", "ACKNOWLEDGED", "EXPIRED", "CANCELLED"]) {
        assert.equal(moveFor(notification({ status })), "done", status);
    }
});

test("undeliverable is explained rather than treated as an error", () => {
    // Nothing could carry it. It is in the inbox regardless, which is the whole
    // reason there is an inbox.
    assert.equal(
        deliveryNote(notification({ delivery_status: "UNDELIVERABLE", undeliverable_reason: "No Telegram account linked." })),
        "No Telegram account linked.",
    );
    assert.match(
        deliveryNote(notification({ delivery_status: "UNDELIVERABLE" })) ?? "",
        /No channel could carry this/,
    );
    assert.match(deliveryNote(notification({ delivery_status: "FAILED" })) ?? "", /failed/);
});

test("a delivered notification says nothing about its delivery", () => {
    // The interesting case is the one where somebody is reading this *because*
    // nothing else reached them. Saying "delivered" on every other row is noise.
    assert.equal(deliveryNote(notification()), null);
});

test("unread is about being read, not about being finished", () => {
    // Matching the server's own count. A badge that clears only when the work
    // is done is a badge people stop looking at.
    assert.equal(isUnread(notification()), true);
    assert.equal(isUnread(notification({ read_at: "2026-09-18T09:05:00Z" })), false);
    assert.equal(isUnread(notification({ status: "RESPONDED", read_at: null })), true);
});

test("past a point the number stops being the information", () => {
    assert.equal(badgeCount(0), null);
    assert.equal(badgeCount(-1), null);
    assert.equal(badgeCount(1), "1");
    assert.equal(badgeCount(99), "99");
    assert.equal(badgeCount(100), "99+");
});

test("what already happened is said in the person's own words when there are any", () => {
    assert.equal(outcomeOf(notification({ status: "RESPONDED", response_summary: "  Approved all three  " })), "You said: Approved all three");
    assert.equal(outcomeOf(notification({ status: "RESPONDED", response_summary: "   " })), "Answered");
    assert.equal(outcomeOf(notification({ status: "ACKNOWLEDGED" })), "Dismissed");
    assert.equal(outcomeOf(notification({ status: "EXPIRED" })), "Expired before it was answered");
    assert.equal(outcomeOf(notification({ status: "CANCELLED" })), "Withdrawn");
    assert.equal(outcomeOf(notification()), null);
});

test("a refused answer says which of the two things happened", () => {
    // Both are product states, not errors: somebody got there first, or the
    // thing is answered by completing its own form.
    assert.match(conflictNote(notification()), /already answered/);
    assert.match(conflictNote(notification({ responds_through_action: true })), /completing its form/);
});

test("a form is drawn only when the ask says which run and node", () => {
    // Without both there is nothing to submit against, and a form that would
    // 422 is worse than a link to the page that works.
    const asksForm = (over: Record<string, unknown>) =>
        moveFor(notification({ responds_through_action: true, action: over }));

    assert.equal(asksForm({ run_id: "run_1", node_id: "collect" }), "form");
    assert.equal(asksForm({ run_id: "run_1" }), "elsewhere");
    assert.equal(asksForm({ node_id: "collect" }), "elsewhere");
    assert.equal(asksForm({ run_id: "", node_id: "collect" }), "elsewhere");
    assert.equal(asksForm({ run_id: 7, node_id: "collect" }), "elsewhere");
    assert.equal(moveFor(notification({ responds_through_action: true })), "elsewhere");
});

test("the run and node come off the notification, never from anywhere else", () => {
    // The backend is deliberate about this: they belong to whoever asked, and
    // letting a recipient name them would let somebody submit against any run
    // whose ids they could guess.
    assert.deepEqual(
        formTarget(notification({ action: { run_id: "run_1", node_id: "collect" } })),
        { runId: "run_1", nodeId: "collect" },
    );
    assert.equal(formTarget(notification()), null);
    assert.equal(formTarget(notification({ action: null })), null);
});

test("a run that has moved past the node offers no form", () => {
    // Submitting against a node the run has already passed is a 422. A stale
    // notification should say the work moved on, not draw a dead form.
    const schema = { type: "object", properties: { amount: { type: "number" } } };

    assert.deepEqual(waitingSchema({ active_wait: { node_id: "collect", payload: { input_schema: schema } } }, "collect"), schema);
    assert.equal(waitingSchema({ active_wait: { node_id: "somewhere-else", payload: { input_schema: schema } } }, "collect"), null);
    assert.equal(waitingSchema({ active_wait: null }, "collect"), null);
    assert.equal(waitingSchema(null, "collect"), null);
    assert.equal(waitingSchema({ active_wait: { node_id: "collect", payload: {} } }, "collect"), null);
});

test("the author's field order is read off the same wait as the schema", () => {
    // Without `ui:order` the SDK sorts fields alphabetically by label, so a
    // form authored terms_ok / payment_days / contact_email was answered as
    // Contact, Payment days, The terms are acceptable — an order nobody chose.
    // Both halves were on the wait the whole time; only one was being read.
    const input_schema = { type: "object", properties: { a: { type: "string" } } };
    const ui_schema = { "ui:order": ["terms_ok", "payment_days", "contact_email"] };
    const run = { active_wait: { node_id: "collect", payload: { input_schema, ui_schema } } };

    assert.deepEqual(waitingUiSchema(run, "collect"), ui_schema);
    assert.deepEqual(waitingSchema(run, "collect"), input_schema);
});

test("the order is never taken from a wait the schema did not come from", () => {
    // The node check is shared between the two readers on purpose. Reading a
    // schema from one wait and an order from another would render a form
    // nobody authored, and would do it without complaining.
    const run = {
        active_wait: {
            node_id: "collect",
            payload: { input_schema: { type: "object" }, ui_schema: { "ui:order": ["a"] } },
        },
    };

    assert.equal(waitingUiSchema(run, "a-different-node"), null);
    assert.equal(waitingSchema(run, "a-different-node"), null);
});

test("a form with no stated order is not a broken one", () => {
    // `ui_schema` is optional: a workflow author who never set an order gets
    // the SDK's fallback, which is what happened before this was read at all.
    const run = { active_wait: { node_id: "collect", payload: { input_schema: { type: "object" } } } };

    assert.equal(waitingUiSchema(run, "collect"), null);
    assert.equal(waitingUiSchema({ active_wait: null }, "collect"), null);
    assert.equal(waitingUiSchema(null, "collect"), null);
    // Anything that is not a plain object is not a ui schema. An array passes
    // `typeof x === "object"`, which is why that check is not enough on its own.
    assert.equal(waitingUiSchema({ active_wait: { node_id: "c", payload: { ui_schema: ["ui:order"] } } }, "c"), null);
    assert.equal(waitingUiSchema({ active_wait: { node_id: "c", payload: { ui_schema: "later" } } }, "c"), null);
});
