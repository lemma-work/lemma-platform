import type { Plan, SubscriptionStatus } from "./types";

/**
 * Money as the API reports it.
 *
 * Every figure a customer reads comes from the catalog over the wire, never
 * from a constant in this repo: this is the open-source frontend, and a price
 * written here would both publish a commercial decision and drift from the
 * catalog the moment it changed.
 */
export function formatCents(cents: number, currency = "USD"): string {
    return new Intl.NumberFormat("en-US", {
        style: "currency",
        currency,
        // Whole-dollar prices read better without the trailing zeroes; part
        // dollars (a small overage charge) need them.
        maximumFractionDigits: cents % 100 === 0 ? 0 : 2,
    }).format(cents / 100);
}

/**
 * How a cadence reads in a price.
 *
 * The API names the cadence (`MONTHLY`); this is only the English for it. A
 * plan billed yearly must not read "/ month" because the frontend assumed one.
 */
const INTERVAL_NOUNS: Record<string, string> = {
    DAILY: "day",
    WEEKLY: "week",
    MONTHLY: "month",
    QUARTERLY: "quarter",
    YEARLY: "year",
    ANNUAL: "year",
};

export function intervalNoun(interval: string | undefined | null): string {
    if (!interval) return "period";
    return INTERVAL_NOUNS[interval.toUpperCase()] ?? interval.toLowerCase();
}

/**
 * "$25 per seat / month", assembled entirely from what the plan says.
 *
 * Price, currency, unit and cadence all come from the catalog. Nothing about
 * the commercial offer is written into this repo, which is both the rule for
 * an open-source frontend and what stops this drifting from the catalog.
 *
 * A zero-price plan prices as "$0 / month" rather than "Free": this string sits
 * in a price column beside the plan's name, and the catalog's free plan is
 * itself called "Free" -- which rendered as "Free  Free".
 */
export function isContactSales(plan: Plan): boolean {
    return plan.features.billing_mode === "contact_sales";
}

export function formatPlanPrice(plan: Plan): string {
    // A contracted plan has no list price. Its unit is what the catalog calls
    // it ("custom"); inventing "$0" here would advertise it as free.
    if (isContactSales(plan)) {
        const unit = plan.features.price_unit ?? "";
        return unit ? unit.charAt(0).toUpperCase() + unit.slice(1) : "";
    }
    const price = formatCents(plan.price_cents, plan.currency);
    const per = intervalNoun(plan.features.billing_interval);
    return plan.features.price_unit
        ? `${price} per ${plan.features.price_unit} / ${per}`
        : `${price} / ${per}`;
}

/**
 * What a per-seat plan costs this buyer, when that is not the price on the card.
 *
 * The card prices one seat, because that is how the plan is sold. A fourteen-
 * person team reading "$200 / seat / month" and then meeting $2,800 at the
 * checkout has been told the truth and surprised anyway -- the multiplication
 * was never shown. Saying "for 14 seats" also states plainly that this is a
 * team plan priced per person.
 *
 * Null when there is nothing to add: a plan not sold per unit, a single seat
 * (where the total is the number already above it), or a plan whose price is
 * not a price -- a contracted plan carries 0 as a placeholder, and "$0 / month
 * for 14 seats" advertises a negotiated plan as free.
 */
export function planTotalForSeats(
    plan: Plan,
    seats: number | undefined | null,
): string | null {
    const unit = plan.features.price_unit;
    if (!unit || isContactSales(plan) || plan.price_cents <= 0) return null;
    // `Number.isInteger` and not just `isFinite`: seats are people, and 2.5 of
    // them priced as "$500 / month for 2.5 seats" would be a number we invented.
    if (typeof seats !== "number" || !Number.isInteger(seats) || seats <= 1) {
        return null;
    }
    const total = formatCents(plan.price_cents * seats, plan.currency);
    const per = intervalNoun(plan.features.billing_interval);
    return `${total} / ${per} for ${seats} ${unit}s`;
}

/** Just the cadence half, for a price already rendered large. */
export function planPriceSuffix(plan: Plan): string {
    const per = intervalNoun(plan.features.billing_interval);
    return plan.features.price_unit
        ? `/ ${plan.features.price_unit} / ${per}`
        : `/ ${per}`;
}

/** A settlement period is always one calendar month, so its start names it. */
export function formatPeriod(start: string): string {
    return new Intl.DateTimeFormat("en-US", {
        month: "short",
        year: "numeric",
        timeZone: "UTC",
    }).format(new Date(start));
}

export function formatDate(value: string | null): string | null {
    if (!value) return null;
    // UTC, as `formatPeriod` already does. These are billing timestamps, and a
    // period that ends at 2026-10-01T00:00:00Z reads as 30 Sept to anyone west
    // of UTC -- so a renewal date, a cancellation date and a cycle range could
    // each show the day before the one the invoice is dated.
    return new Intl.DateTimeFormat("en-US", {
        day: "numeric",
        month: "short",
        year: "numeric",
        timeZone: "UTC",
    }).format(new Date(value));
}

export interface StatusCopy {
    label: string;
    /** What the reader should understand is true of their account right now. */
    detail: string | null;
    tone: "positive" | "attention" | "neutral";
}

/**
 * What a status means for the person reading it.
 *
 * `past_due` and `paused` are the pair that needs care, and they are not the
 * same thing. A failed payment starts a grace window in which the plan keeps
 * working and the customer is emailed; only when that window closes unpaid does
 * the subscription pause, and pausing is what actually withdraws the
 * allowances. Telling a past-due customer they are already on free limits is
 * both untrue and the wrong thing to make them feel.
 */
export function describeStatus(
    status: SubscriptionStatus,
    periodEnd: string | null,
    cancelAtPeriodEnd: boolean,
): StatusCopy {
    switch (status) {
        case "active":
            return cancelAtPeriodEnd
                ? {
                      label: "Ending",
                      detail: periodEnd
                          ? `Stays active until ${formatDate(periodEnd)}.`
                          : "Stays active until the end of the period.",
                      tone: "attention",
                  }
                : { label: "Active", detail: null, tone: "positive" };
        case "pending":
            return {
                label: "Awaiting payment",
                detail: "Your plan starts once the payment clears.",
                tone: "attention",
            };
        case "past_due":
            return {
                label: "Payment failed",
                detail:
                    "Your plan is still running. Update your payment method to " +
                    "keep it that way.",
                tone: "attention",
            };
        case "paused":
            return {
                label: "Paused",
                detail:
                    "A payment did not go through, so this plan is paused and " +
                    "you are on free limits. Paying resumes it — nothing has " +
                    "been cancelled.",
                tone: "attention",
            };
        case "cancelled":
            return {
                label: "Cancelled",
                detail: "You are on free limits.",
                tone: "neutral",
            };
        case "expired":
            return {
                label: "Expired",
                detail: "You are on free limits.",
                tone: "neutral",
            };
        default:
            return { label: status, detail: null, tone: "neutral" };
    }
}
