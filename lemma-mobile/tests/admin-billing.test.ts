import { describe, expect, it } from "vitest";
import { readCap, readDays, readSpend } from "../src/areas/admin/billing/reading";
import { billingSample } from "../src/areas/admin/billing/sample";
import { formatPrice, offered, priceLine, type Subscription } from "@web/billing/plan";
import {
    afterStart, busiestWords, buyingHere, capWords, checkoutPollMs, currentWords, dayBars, hostOf, invoicePeriod,
    invoiceWord, isLocal, kindLabel, meterFill, planPlace, priceOf, priceWords, readCapTyped, seatWords, teamPowers, tokens, whoMayChange,
    windowNote, windowTone,
} from "../src/areas/admin/billing/words";

/** Usage and paying (PARITY.md, F): what the billing screens decide and say. */

const DAY = 24 * 60 * 60_000;

const team = {
    id: "p-team", name: "Lemma Team", description: null, plan_type: "TEAM" as const, price_cents: 1000, currency: "USD",
    features: { price_unit: "seat" }, seat_limit: null, usage_limits: {}, is_active: true,
};
const free = { ...team, id: "p-free", name: "Free", plan_type: "PERSONAL" as const, price_cents: 0, features: {} };

function subscription(patch: Partial<Subscription>): Subscription {
    return {
        id: "s", user_id: null, organization_id: "o", plan_id: team.id, plan: team, status: "active",
        dodo_subscription_id: "d", current_period_start: null, current_period_end: "2026-11-02T12:00:00Z",
        seat_count: 3, cancel_at_period_end: false, ...patch,
    };
}

describe("buying from the phone", () => {
    it("offers nothing that pays on iOS, and says where instead, naming the server's own web address", () => {
        expect(buyingHere("ios", "https://lemma.work", "mine")).toEqual({ offer: false, elsewhere: "Change your plan on the web, at lemma.work." });
        expect(buyingHere("ios", "https://staging.example.com/", "organization")).toEqual({
            offer: false,
            elsewhere: "Change what this organization pays for on the web, at staging.example.com.",
        });
        expect(buyingHere("ios", "http://localhost:3000", "mine").elsewhere).toBe("Change your plan on the web, at localhost:3000.");
    });

    it("offers it all on Android, with nothing said in its place", () => {
        expect(buyingHere("android", "https://lemma.work", "mine")).toEqual({ offer: true, elsewhere: null });
        expect(buyingHere("android", "https://lemma.work", "organization")).toEqual({ offer: true, elsewhere: null });
    });

    it("names a host without its scheme or a trailing slash", () => {
        expect(hostOf("https://lemma.work/")).toBe("lemma.work");
        expect(hostOf(" http://10.0.2.2:3000 ")).toBe("10.0.2.2:3000");
    });

    it("asks after a checkout often at first, then less, then not at all", () => {
        expect(checkoutPollMs(0)).toBe(3_000);
        expect(checkoutPollMs(119_000)).toBe(3_000);
        expect(checkoutPollMs(2 * 60_000)).toBe(15_000);
        expect(checkoutPollMs(15 * 60_000)).toBe(false);
    });

    it("opens a checkout, takes a plan already moved, and refuses a start with neither", () => {
        expect(afterStart({ subscription: null, checkout_url: "https://pay.example/c/1", message: "" })).toEqual({ kind: "open", url: "https://pay.example/c/1" });
        expect(afterStart({ subscription: subscription({}), checkout_url: null, message: "" })).toEqual({ kind: "settled" });
        expect(afterStart({ subscription: null, checkout_url: "  ", message: "" }).kind).toBe("failed");
    });

    it("has no billing on a local installation", () => {
        expect(isLocal("local")).toBe(true);
        expect(isLocal("hosted")).toBe(false);
        expect(isLocal(null)).toBe(false);
    });
});

describe("the allowance", () => {
    const window = (used: number, allowed = true) => ({ key: "k", label: "Your weekly allowance", used_percent: used, allowed, reset_at: new Date(Date.now() + 3 * DAY + 60_000).toISOString() });

    it("warns at the server's threshold, and a spent window outranks any percentage", () => {
        expect(windowTone(window(40), { warning_percent: 80 })).toBe("quiet");
        expect(windowTone(window(85), { warning_percent: 80 })).toBe("warn");
        expect(windowTone(window(55), { warning_percent: 50 })).toBe("warn");
        expect(windowTone(window(10, false), { warning_percent: 80 })).toBe("blocked");
        /* A threshold that makes no sense falls back to 80. */
        expect(windowTone(window(79), { warning_percent: 0 })).toBe("quiet");
    });

    it("says when a window starts over, and that a spent one is spent", () => {
        expect(windowNote(window(40))).toBe("Resets in 3 days");
        expect(windowNote(window(100, false))).toBe("Spent. Resets in 3 days");
        expect(windowNote({ ...window(100, false), reset_at: "" })).toBe("Spent");
    });

    it("caps the meter at full, past the cap too", () => {
        expect(meterFill(42)).toBeCloseTo(0.42);
        expect(meterFill(230)).toBe(1);
        expect(meterFill(-3)).toBe(0);
        expect(meterFill(Number.NaN)).toBe(0);
    });

    it("sends a raise to whoever pays", () => {
        expect(planPlace({ plan_type: "TEAM" })).toBe("team-billing");
        expect(planPlace({ plan_type: "PERSONAL" })).toBe("plan");
        expect(planPlace(undefined)).toBe("plan");
    });
});

describe("what was spent", () => {
    it("reads a summary into totals and where the money went, an unpriced model kept apart from free", () => {
        const spend = readSpend({
            start_date: "", end_date: "", period_days: 30, system_cost_usd: 1.5, total_input_tokens: 900, total_output_tokens: 100,
            total_tokens: 1000, total_units: 0, total_by_profile: {},
            total_by_model: { cheap: { system_cost_usd: 0.25, total_tokens: 400 }, dear: { system_cost_usd: 1.25, total_tokens: 500 }, odd: { total_tokens: 100 } },
            total_by_kind: { LLM: { system_cost_usd: 1.5, total_tokens: 1000 } },
        });
        expect(spend.cost).toBe(1.5);
        expect(spend.tokensIn).toBe(900);
        expect(spend.byModel.map((one) => one.label)).toEqual(["dear", "cheap", "odd"]);
        expect(spend.byModel[2].cost).toBeNull();
        expect(spend.byKind[0].label).toBe("LLM");
    });

    it("reads the days oldest first, and the cap", () => {
        const days = readDays({ items: [
            { bucket: "2026-10-02T00:00:00Z", system_cost_usd: 2, input_tokens: 0, output_tokens: 0, total_tokens: 0, units: 0 },
            { bucket: "2026-10-01T00:00:00Z", system_cost_usd: 1, input_tokens: 0, output_tokens: 0, total_tokens: 0, units: 0 },
        ] });
        expect(days).toEqual([{ day: "2026-10-01", cost: 1 }, { day: "2026-10-02", cost: 2 }]);
        expect(readCap({ monthly_limit_usd: null, spent_this_month_usd: 3.2 })).toEqual({ limit: null, spent: 3.2 });
    });

    it("draws a bar per day against the busiest, a quiet day as a sliver, nothing when nothing was spent", () => {
        const { heights, busiest } = dayBars([{ day: "2026-10-01", cost: 1 }, { day: "2026-10-02", cost: 4 }, { day: "2026-10-03", cost: 0 }]);
        expect(heights).toEqual([0.25, 1, 0.02]);
        expect(busiest?.day).toBe("2026-10-02");
        expect(dayBars([{ day: "2026-10-01", cost: 0 }])).toEqual({ heights: [], busiest: null });
        expect(busiestWords({ day: "2026-10-14T12:00:00Z", cost: 1.2 })).toBe("Busiest day: 14 October, $1.20");
    });

    it("names kinds of work in words, and counts tokens with commas", () => {
        expect(kindLabel("LLM")).toBe("Language models");
        expect(kindLabel("embedding")).toBe("Embeddings");
        expect(kindLabel("VIDEO_FRAMES")).toBe("Video frames");
        expect(tokens(1204332)).toBe("1,204,332");
    });

    it("says the contacts cap as the web does, and reads what is typed", () => {
        expect(capWords({ limit: 25, spent: 3.2 })).toBe("$3.20 of $25 this month");
        expect(capWords({ limit: null, spent: 12.4 })).toBe("$12 this month, no cap set");
        expect(readCapTyped("")).toEqual({ limit: null });
        expect(readCapTyped(" $40 ")).toEqual({ limit: 40 });
        expect(readCapTyped("1,250.5")).toEqual({ limit: 1250.5 });
        expect(readCapTyped("-1")).toHaveProperty("problem");
        expect(readCapTyped("lots")).toHaveProperty("problem");
    });
});

describe("what is paid for", () => {
    it("says free and nothing alike, and a paid plan with its price and renewal", () => {
        expect(currentWords(null, "Nothing is charged.")).toMatchObject({ name: "No plan yet", price: "Nothing is charged.", renews: null, note: null });
        expect(currentWords(subscription({ plan: free, plan_id: free.id }), "Nothing is charged.").price).toBe("Nothing is charged.");
        const paid = currentWords(subscription({}), "Nothing is charged.");
        expect(paid.price).toBe("$10 per seat / month");
        expect(paid.renews).toBe("Renews 2 November");
    });

    it("stops saying renews once it will not, and flags trouble", () => {
        const stopping = currentWords(subscription({ cancel_at_period_end: true }), "");
        expect(stopping.renews).toBeNull();
        expect(stopping.note).toBe("Will not renew. Access runs to 2 November.");
        const owing = currentWords(subscription({ status: "past_due" }), "");
        expect(owing.trouble).toBe(true);
    });

    it("prints prices as the web does, with both fraction bounds set", async () => {
        for (const cents of [0, 1000, 2500, 1999, 20000, 5]) expect(priceOf(cents)).toBe(formatPrice(cents));
        expect(priceOf(1000)).toBe("$10");
        expect(priceOf(1999)).toBe("$19.99");
        for (const plan of await billingSample().plans()) expect(priceWords(plan)).toBe(priceLine(plan));
    });

    it("counts seats and people, and the gap only when the plan is paid for", () => {
        expect(seatWords({ current_seats: 3, member_count: 4 }, true)).toEqual({ bought: "3 seats", present: "4 people", short: "1 person not yet paid for." });
        expect(seatWords({ current_seats: 3, member_count: 4 }, false).short).toBeNull();
        expect(seatWords({ current_seats: 1, member_count: 1 }, true)).toEqual({ bought: "1 seat", present: "1 person", short: null });
    });

    it("says an invoice's state and period", () => {
        expect(invoiceWord("unpaid")).toBe("Due");
        expect(invoiceWord("failed")).toBe("Payment failed");
        expect(invoiceWord("draft")).toBe("Draft");
        expect(invoicePeriod({ period_start: "2026-09-01T12:00:00Z", period_end: "2026-09-30T12:00:00Z" })).toBe("1 September – 30 September");
    });

    it("lets owners and editors buy, only an owner stop, and tells a member why not", () => {
        expect(teamPowers("ORG_OWNER", true)).toEqual({ mayBuy: true, mayStop: true, seesInvoices: true, held: null });
        expect(teamPowers("ORG_EDITOR", true)).toMatchObject({ mayBuy: true, mayStop: false, held: null });
        expect(teamPowers("ORG_MEMBER", true)).toMatchObject({ mayBuy: false, mayStop: false, seesInvoices: false });
        expect(teamPowers("ORG_MEMBER", true).held).toMatch(/owner or an editor/);
        /* Not yet known: nothing said on a guess. */
        expect(teamPowers(null, false).held).toBeNull();
        expect(whoMayChange("ORG_EDITOR")).toMatch(/You are an editor here\.$/);
        expect(whoMayChange("ORG_MEMBER")).toMatch(/You are a member here\.$/);
    });
});

describe("Acme's sample billing", () => {
    it("offers Acme's plans cheapest first, Enterprise last, Free left out", async () => {
        const source = billingSample();
        const plans = await source.plans();
        expect(offered(plans, "PERSONAL").map((one) => one.name)).toEqual(["Lemma Personal", "Lemma Personal Plus", "Lemma Personal Max"]);
        expect(offered(plans, "TEAM").at(-1)?.name).toBe("Lemma Enterprise");
    });

    it("shows the gap between seats and people, and a window past its warning", async () => {
        const source = billingSample();
        const seats = await source.seats("sample-acme");
        expect(seats.member_count).toBeGreaterThan(seats.current_seats);
        const limits = await source.limits("sample-acme");
        expect(limits.plan_type).toBe("TEAM");
        expect(limits.windows.some((one) => windowTone(one, limits) === "warn")).toBe(true);
        const spend = await source.spend({ whose: "organization", orgId: "sample-acme" }, 30);
        expect(spend.cost).toBeGreaterThan(0);
        expect(spend.byModel.some((one) => one.cost === null)).toBe(true);
        expect(await source.daily({ whose: "mine", orgId: null }, 7)).toHaveLength(7);
    });

    it("moves Ada's plan at once and stops it renewing, in memory", async () => {
        const source = billingSample();
        const plans = await source.plans();
        const personal = offered(plans, "PERSONAL")[0];
        const started = await source.start(null, personal.id);
        expect(afterStart(started)).toEqual({ kind: "settled" });
        expect((await source.subscription(null))?.plan_id).toBe(personal.id);
        await source.cancel(null);
        expect((await source.subscription(null))?.cancel_at_period_end).toBe(true);
        await expect(source.start("sample-acme", offered(plans, "TEAM").at(-1)!.id)).rejects.toThrow();
    });
});
