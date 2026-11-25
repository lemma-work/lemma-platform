import test from "node:test";
import assert from "node:assert/strict";
import {
    canChangeTier, compare, formatPrice, isPaid, isUpgrade, lemmaCap, monthlyCents, moveTo, offered, onDate,
    paying, priceLine, recommended, runsDocker, shortName, standing, tagline, usageBase, usageTimes, workspaceOf,
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

/* ── comparing plans ──────────────────────────────────────────────────── */

/** The team tiers as `plans.json` ships them: the shape the cards are built
 *  from, so a test here is a test against the real catalogue's fields. */
function tier(id: string, name: string, priceCents: number, credits: number, cores: number, memoryMb: number, docker: boolean, maxPods: number | null = null): Plan {
    return plan({
        id, name, plan_type: "TEAM", price_cents: priceCents, seat_limit: null,
        features: {
            price_unit: "seat", remove_lemma_branding: true, included_llm_credits_cents: credits,
            limits: {
                max_pods: maxPods,
                workspace: { cpu_count: cores, memory_mb: memoryMb },
                machines: { templates: docker ? ["workspace", "workspace-docker"] : ["workspace"] },
            },
        },
    });
}
const team = tier("team", "Lemma Team", 1000, 1000, 2, 4096, false);
const plus = tier("plus", "Lemma Team Plus", 2500, 3000, 4, 8192, true);
const max = tier("max", "Lemma Team Max", 20000, 30000, 8, 16384, true);

test("a card names the plan without the frame it sits in", () => {
    // The page already says "Lemma" and "Team", so a card keeps the rest.
    assert.equal(shortName(team), "Team");
    assert.equal(shortName(plus), "Plus");
    assert.equal(shortName(plan({ name: "Lemma Personal Max" })), "Max");
    assert.equal(shortName(plan({ name: "Lemma Enterprise" })), "Enterprise");
    assert.equal(shortName(free), "Free");
    // The catalogue's own short name wins once it has one.
    assert.equal(shortName(plan({ name: "Lemma Team Plus", features: { short_name: "Plus+" } })), "Plus+");
});

test("usage is a multiple of the smallest plan, never a dollar figure", () => {
    const tiers = [team, plus, max];
    assert.equal(usageBase(tiers)?.id, "team");
    assert.equal(usageTimes(team, tiers), 1);
    assert.equal(usageTimes(plus, tiers), 3);
    // Every multiple is against the same plan, so the cards read across:
    // Max is thirty times Team.
    assert.equal(usageTimes(max, tiers), 30);
    assert.equal(usageTimes(tier("half", "Lemma Team Half", 1500, 2500, 2, 4096, false), tiers), 2.5);
    assert.equal(usageTimes(enterprise, tiers), null);
});

test("the comparison reads the limits the workspace is started with", () => {
    assert.deepEqual(workspaceOf(plus), { cores: 4, gb: 8 });
    assert.equal(workspaceOf(free), null);
    assert.equal(runsDocker(team), false);
    assert.equal(runsDocker(plus), true);
    assert.equal(runsDocker(free), undefined);
    assert.equal(lemmaCap(team), null);
    assert.equal(lemmaCap(tier("p", "Lemma Personal", 1000, 1000, 2, 4096, false, 10)), 10);
    assert.equal(lemmaCap(free), undefined);
});

test("rows are what differs; what every plan shares is said once", () => {
    const { rows, shared } = compare([team, plus, max], "TEAM");
    assert.deepEqual(rows, [
        { label: "Usage", cells: ["1×", "3×", "30×"] },
        { label: "Workspace", cells: ["2 cores · 4 GB", "4 cores · 8 GB", "8 cores · 16 GB"] },
        { label: "Docker", cells: ["—", "Included", "Included"] },
    ]);
    // No team plan caps Lemmas, so that is not a row on three cards.
    assert.ok(shared.includes("As many Lemmas as you need"));
    assert.ok(shared.includes("Your branding instead of Lemma’s"));
    // A question one plan cannot answer is left off rather than drawn as a gap.
    assert.deepEqual(compare([team, enterprise], "TEAM").rows, []);
});

test("a per-seat plan is quoted for everyone in the organization", () => {
    // What the checkout charges: one seat per member.
    assert.equal(monthlyCents(plus, 6), 15000);
    assert.equal(monthlyCents(plus, 0), 2500);
    assert.equal(monthlyCents(plan({ price_cents: 2500 }), 6), 2500);
    assert.equal(isUpgrade(null, team), true);
    assert.equal(isUpgrade(max, plus), false);
    assert.equal(isUpgrade(team, plus), true);
});

test("a pick for somebody not paying, more usage for somebody near the limit", () => {
    const tiers = [team, plus, max];
    const onFree = subscription({ plan_id: "plan-free", plan: free });
    assert.equal(recommended(tiers, onFree, false)?.plan.id, "plus");
    assert.equal(recommended(tiers, null, false)?.why, "pick");
    // The catalogue's own pick wins over the middle.
    const flagged = { ...max, features: { ...max.features, recommended: true } };
    assert.equal(recommended([team, plus, flagged], null, false)?.plan.id, "max");
    // Paying and comfortable: nothing to push.
    const onTeam = subscription({ plan_id: "team", plan: team });
    assert.equal(recommended(tiers, onTeam, false), null);
    // Paying and near the limit: the next plan up, for more usage.
    assert.deepEqual(recommended(tiers, onTeam, true), { plan: plus, why: "more" });
    // Nothing above the top.
    assert.equal(recommended(tiers, subscription({ plan_id: "max", plan: max }), true), null);
});

test("a card's one line is who the plan is for, not what the page already says", () => {
    const described = (description: string) => plan({ description });
    assert.equal(tagline(described("For teams. Fair LLM and compute sandbox usage for everyday agent work, billed per seat.")), "For everyday agent work.");
    assert.equal(tagline(described("For teams. Higher LLM and compute sandbox usage, for longer and heavier agent runs. Billed per seat.")), "For longer and heavier agent runs.");
    assert.equal(tagline(described("For individuals. Our highest LLM and compute sandbox usage, for continuous heavy work.")), "For continuous heavy work.");
    // Any other shape is kept whole rather than guessed at.
    assert.equal(tagline(described("For large organizations. Custom usage, security and support.")), "For large organizations. Custom usage, security and support.");
    assert.equal(tagline(plan({ description: null })), "");
    assert.equal(tagline(plan({ features: { tagline: "Built for agents that never stop." } })), "Built for agents that never stop.");
});
