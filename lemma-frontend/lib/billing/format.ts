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
    return new Intl.DateTimeFormat("en-US", {
        day: "numeric",
        month: "short",
        year: "numeric",
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
 * `past_due` is the one that needs care. It is not a lockout: the backend
 * resolves a non-active subscription to free-tier limits, so the honest
 * sentence is that the plan's allowances have stopped, not that anything has
 * been taken away.
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
                detail: "You are on free limits until a payment goes through.",
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
