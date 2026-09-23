import test from "node:test";
import assert from "node:assert/strict";
import {
    canChangeTier, formatPrice, isPaid, moveTo, offered, onDate, paying, priceLine, standing,
    type Plan, type Subscription,
} from "../src/billing/plan.ts";

function plan(over: Partial<Plan> = {}): Plan {
    return {
        id: "plan-paid", name: "Lemma Personal", description: null, plan_type: "PERSONAL",
        price_cents: 1000, currency: "USD", features: {}, seat_limit: 1, usage_limits: {},
        is_active: true, ...over,
    };
}
const free = plan({ id: "plan-free", name: "Free", price_cents: 0 });
const enterprise = plan({
    id: "plan-ent", name: "Lemma Enterprise", plan_type: "TEAM", price_cents: 0,
    features: { billing_mode: "contact_sales" },
});

function subscription(over: Partial<Subscription> = {}): Subscription {
    return {
        id: "sub", user_id: "user", organization_id: null, plan_id: "plan-paid", plan: plan(),
        status: "active", dodo_subscription_id: "sub_abc", current_period_start: null,
        current_period_end: null, seat_count: 1, cancel_at_period_end: false, ...over,
    };
}

test("paying reads the price, not the status and not the name", () => {
    // The free subscription is ACTIVE — that is the whole reason it blocked
    // purchases until lemma-app #391 — so status answers "may they work",
    // which is a different question from "have they given us a card". And the
    // name is a database row a deployment may well rename.
    assert.equal(paying(subscription({ plan_id: "plan-free", plan: free })), false);
    assert.equal(paying(subscription()), true);
    assert.equal(paying(null), false);
    assert.equal(paying(undefined), false);
    assert.equal(isPaid(free), false);
});

test("a tier can only be changed where something was actually bought", () => {
    // `_validate_move_to` refuses both of these server-side: there is nothing
    // at the provider to move for a plan nobody paid for, and an
    // admin-assigned plan is live with no provider record at all.
    assert.equal(canChangeTier(subscription({ plan_id: "plan-free", plan: free })), false);
    assert.equal(canChangeTier(subscription({ dodo_subscription_id: null })), false);
    assert.equal(canChangeTier(subscription()), true);
});

test("free to paid is a checkout, paid to paid is a change", () => {
    const target = plan({ id: "plan-plus", price_cents: 2500 });
    assert.equal(moveTo(null, target), "checkout");
    assert.equal(moveTo(subscription({ plan_id: "plan-free", plan: free }), target), "checkout");
    assert.equal(moveTo(subscription(), target), "change");
    // The plan already in force is neither.
    assert.equal(moveTo(subscription(), plan()), "current");
    // And one bought by talking to somebody is neither, whoever is asking.
    assert.equal(moveTo(subscription(), enterprise), "contact");
    assert.equal(moveTo(null, enterprise), "contact");
});

test("the offer is this audience's paid tiers, with contact-sales last", () => {
    const plans = [
        plan({ id: "max", price_cents: 20000 }),
        free,
        enterprise,
        plan({ id: "team", plan_type: "TEAM", price_cents: 1000 }),
        plan({ id: "starter", price_cents: 1000 }),
        plan({ id: "retired", price_cents: 500, is_active: false }),
    ];
    // Free is where most people already are: the current-plan line names it,
    // and a card reading "Free · Current plan" is furniture.
    assert.deepEqual(offered(plans, "PERSONAL").map((entry) => entry.id), ["starter", "max"]);
    // Enterprise is priced at zero because it has no price. Sorting on the
    // number would stand it in front of the cheapest tier.
    assert.deepEqual(offered(plans, "TEAM").map((entry) => entry.id), ["team", "plan-ent"]);
    assert.deepEqual(offered(undefined, "PERSONAL"), []);
});

test("a price is printed with the unit that makes it true", () => {
    // Per seat, unsaid, understates a five-person team by a factor of five.
    assert.equal(priceLine(plan({ price_cents: 1000, features: { price_unit: "seat" } })), "$10 per seat / month");
    assert.equal(priceLine(plan({ price_cents: 2500 })), "$25 / month");
    assert.equal(priceLine(free), "Free");
    assert.equal(priceLine(enterprise), "Talk to us");
});

test("whole dollars lose their cents, and priced cents keep them", () => {
    // "$10.00/mo" on a plan card is a receipt's precision on an advert; but
    // dropping real cents would misquote a deployment that priced in them.
    assert.equal(formatPrice(1000), "$10");
    assert.equal(formatPrice(2550), "$25.50");
    assert.equal(formatPrice(0), "$0");
});

test("standing says only what changes what to do next", () => {
    // "Active" under a plan name has never told anybody something to act on.
    assert.equal(standing(subscription()), "");
    assert.equal(standing(null), "");
    assert.match(standing(subscription({ status: "past_due" })), /did not go through/);
    assert.match(standing(subscription({ status: "paused" })), /never completed/);
    assert.match(standing(subscription({ status: "pending" })), /settle/);
    assert.match(
        standing(subscription({ cancel_at_period_end: true, current_period_end: "2026-10-12T00:00:00Z" })),
        /Will not renew\. Access runs to 12 October\./,
    );
    // The date is the only part that can be missing, and its sentence still has
    // to read.
    assert.equal(standing(subscription({ cancel_at_period_end: true })), "Will not renew.");
});

test("an unreadable date is nothing, never Invalid Date", () => {
    assert.equal(onDate(null), "");
    assert.equal(onDate("not a date"), "");
    assert.equal(onDate("2026-10-12T00:00:00Z"), "12 October");
});
