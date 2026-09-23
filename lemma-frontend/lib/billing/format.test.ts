import { describe, expect, it } from "vitest";

import {
    describeStatus,
    formatCents,
    formatPlanPrice,
    intervalNoun,
    isContactSales,
    planTotalForSeats,
} from "./format";
import type { Plan } from "./types";

function plan(overrides: Partial<Plan> = {}): Plan {
    return {
        id: "plan-1",
        name: "Test Plan",
        description: null,
        plan_type: "TEAM",
        price_cents: 2500,
        currency: "USD",
        features: { billing_interval: "MONTHLY" },
        seat_limit: null,
        usage_limits: {},
        is_active: true,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
        ...overrides,
    };
}

describe("money", () => {
    it("drops trailing zeroes on whole amounts but keeps them on part dollars", () => {
        // A plan price reads better as $25; a $0.40 overage charge must not
        // round away to $0.
        expect(formatCents(2500)).toBe("$25");
        expect(formatCents(40)).toBe("$0.40");
        expect(formatCents(2340)).toBe("$23.40");
    });
});

describe("plan price", () => {
    it("takes the unit and the cadence from the plan, never from a constant here", () => {
        // This is the open-source frontend, and the cadence is the catalog's to
        // state: a plan billed yearly must not read "/ month" because the
        // frontend assumed one.
        expect(
            formatPlanPrice(
                plan({ features: { price_unit: "seat", billing_interval: "MONTHLY" } }),
            ),
        ).toBe("$25 per seat / month");
        expect(
            formatPlanPrice(plan({ features: { billing_interval: "YEARLY" } })),
        ).toBe("$25 / year");
        expect(
            formatPlanPrice(
                plan({ features: { price_unit: "user", billing_interval: "WEEKLY" } }),
            ),
        ).toBe("$25 per user / week");
    });

    it("does not invent a cadence the catalog did not send", () => {
        // "period" is visibly wrong, which is the point: silently printing
        // "month" would look right and bill people on a lie.
        expect(formatPlanPrice(plan({ features: {} }))).toBe("$25 / period");
        expect(intervalNoun(undefined)).toBe("period");
    });

    it("prices a free plan as a number, not the word", () => {
        // The catalog's free plan is named "Free", so returning "Free" here
        // printed "Free  Free" in the name/price pair.
        expect(formatPlanPrice(plan({ price_cents: 0 }))).toBe("$0 / month");
    });

    it("shows a contracted plan's own wording instead of $0", () => {
        // Enterprise is priced at 0 in the catalog because it has no list
        // price. Rendering "$0 / month" advertised it as free.
        const enterprise = plan({
            price_cents: 0,
            features: { billing_mode: "contact_sales", price_unit: "custom" },
        });
        expect(isContactSales(enterprise)).toBe(true);
        expect(formatPlanPrice(enterprise)).toBe("Custom");
    });
});

describe("status copy", () => {
    it("describes past_due as still running, because it is", () => {
        const copy = describeStatus("past_due", null, false);

        // A failed payment starts a grace window; the plan keeps working while
        // the customer is chased. This test used to assert the opposite -- that
        // past_due already meant free limits -- which is what `paused` means,
        // two days later.
        expect(copy.detail).toContain("still running");
        expect(copy.detail).not.toContain("free limits");
    });

    it("describes paused as the point where the allowances actually stop", () => {
        const copy = describeStatus("paused", null, false);

        expect(copy.label).toBe("Paused");
        expect(copy.detail).toContain("free limits");
        // Paying resumes it; nothing was cancelled, and the copy should not
        // make someone think they have to buy the plan again.
        expect(copy.detail).toContain("resumes");
    });

    it("does not call a pending subscription active", () => {
        // Team and personal both stay pending until the provider's webhook
        // lands; saying otherwise would promise entitlements that are not on.
        const copy = describeStatus("pending", null, false);
        expect(copy.label).toBe("Awaiting payment");
    });

    it("separates 'ending' from 'active' when a cancellation is pending", () => {
        const copy = describeStatus("active", "2026-10-01T00:00:00Z", true);
        expect(copy.label).toBe("Ending");
        expect(copy.detail).toContain("Oct");
    });

    it("leaves a plain active subscription without a caveat", () => {
        const copy = describeStatus("active", "2026-10-01T00:00:00Z", false);
        expect(copy.label).toBe("Active");
        expect(copy.detail).toBeNull();
    });
});

describe("what a per-seat plan costs this buyer", () => {
    it("states the total and the seats it is counted from", () => {
        // The card prices one seat, because that is how the plan is sold. A
        // fourteen-person team read "$200 / seat / month" and was charged
        // $2,800 at the checkout -- told the truth and still surprised.
        expect(
            planTotalForSeats(
                plan({ price_cents: 20000, features: { billing_interval: "MONTHLY", price_unit: "seat" } }),
                14,
            ),
        ).toBe("$2,800 / month for 14 seats");
    });

    it("says nothing when the total is the price already on the card", () => {
        const perSeat = plan({
            price_cents: 20000,
            features: { billing_interval: "MONTHLY", price_unit: "seat" },
        });
        // One seat: repeating "$200 / month for 1 seat" beside "$200 / seat /
        // month" adds a line and no information.
        expect(planTotalForSeats(perSeat, 1)).toBeNull();
        // Seats unknown -- the personal page has no seat count at all.
        expect(planTotalForSeats(perSeat, undefined)).toBeNull();
        expect(planTotalForSeats(perSeat, null)).toBeNull();
        // Not sold per unit, so there is nothing to multiply.
        expect(planTotalForSeats(plan({ price_cents: 2500 }), 14)).toBeNull();
    });

    it("refuses a seat count that is not a whole number of people", () => {
        // `Number.isFinite(2.5)` is true, so a fractional count priced itself
        // as "$500 / month for 2.5 seats" -- a total nobody will ever be
        // charged, off a seat count that cannot exist.
        const perSeat = plan({
            price_cents: 20000,
            features: { billing_interval: "MONTHLY", price_unit: "seat" },
        });
        expect(planTotalForSeats(perSeat, 2.5)).toBeNull();
        expect(planTotalForSeats(perSeat, Number.NaN)).toBeNull();
        expect(planTotalForSeats(perSeat, Number.POSITIVE_INFINITY)).toBeNull();
        // A whole number either side of it still prices.
        expect(planTotalForSeats(perSeat, 3)).toBe("$600 / month for 3 seats");
    });

    it("does not multiply a price that is not a price", () => {
        // A contracted plan carries 0 as a placeholder; "$0 / month for 14
        // seats" advertises a negotiated plan as free.
        expect(
            planTotalForSeats(
                plan({
                    price_cents: 0,
                    features: {
                        billing_interval: "MONTHLY",
                        price_unit: "seat",
                        billing_mode: "contact_sales",
                    },
                }),
                14,
            ),
        ).toBeNull();
        expect(
            planTotalForSeats(
                plan({ price_cents: 0, features: { billing_interval: "MONTHLY", price_unit: "seat" } }),
                14,
            ),
        ).toBeNull();
    });

    it("follows the plan's own cadence and unit", () => {
        expect(
            planTotalForSeats(
                plan({
                    price_cents: 1000,
                    features: { billing_interval: "YEARLY", price_unit: "member" },
                }),
                3,
            ),
        ).toBe("$30 / year for 3 members");
    });
});
