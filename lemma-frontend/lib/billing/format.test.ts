import { describe, expect, it } from "vitest";

import {
    describeStatus,
    formatCents,
    formatPlanPrice,
    intervalNoun,
    isContactSales,
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
    it("describes past_due as a downgrade, not a lockout", () => {
        // The backend resolves any non-active subscription to free-tier
        // limits, so nothing is taken away -- the allowances just stop.
        const copy = describeStatus("past_due", null, false);
        expect(copy.label).toBe("Payment failed");
        expect(copy.detail).toContain("free limits");
        expect(copy.tone).toBe("attention");
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
