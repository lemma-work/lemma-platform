/** What a plan is, what somebody is on, and what moving between them costs.
 *
 *  The shapes are written out here rather than imported. Billing lives in
 *  lemma-cloud and the SDK is generated from the open-source OpenAPI, so
 *  `lemma-sdk` ships types for usage and none for this — the same situation
 *  `usage/queries.ts` is in for its paths, one layer deeper. They mirror
 *  `billing/api/schemas/subscription_schemas.py`; money is integer cents on the
 *  wire and stays that way until it is printed.
 *
 *  No React in here on purpose: the test runner strips types from `.ts` and
 *  cannot load `.tsx` at all, so the decisions live where they can be tested
 *  and the components render them.
 */

/** Statuses as published. `paused` is the one a naive list forgets — it is
 *  what the dunning sweep produces when a grace window closes unpaid, and
 *  leaving it out of a union here would put an unhandled state in front of
 *  exactly the customer who most needs to read it. */
export type SubscriptionStatus =
    | "pending" | "active" | "past_due" | "paused" | "cancelled" | "expired";

export type PlanAudience = "PERSONAL" | "TEAM";

export interface Plan {
    id: string;
    name: string;
    description: string | null;
    plan_type: PlanAudience;
    price_cents: number;
    currency: string;
    features: Record<string, unknown>;
    seat_limit: number | null;
    usage_limits: Record<string, unknown>;
    is_active: boolean;
}

export interface Subscription {
    id: string;
    user_id: string | null;
    organization_id: string | null;
    plan_id: string;
    /** Present on the `…/subscription` reads, absent on the ones that only
     *  acknowledge. Optional rather than required for that reason. */
    plan?: Plan | null;
    status: SubscriptionStatus;
    dodo_subscription_id: string | null;
    current_period_start: string | null;
    current_period_end: string | null;
    seat_count: number;
    cancel_at_period_end: boolean;
}

export interface PlanList { items: Plan[]; next_page_token: string | null }

/** A started checkout. `subscription` is null for anything with a price on it:
 *  since lemma-app #391 a checkout writes nothing until the payment lands, so
 *  there is no row to return and reading one would be reporting a plan change
 *  that has not happened. */
export interface StartedCheckout {
    subscription: Subscription | null;
    checkout_url: string | null;
    message: string;
}

/** The acknowledgement a tier change gets, and all it gets. The provider can
 *  decline the difference owed, so the new plan is deliberately not in here. */
export interface ChangeAcknowledged { message: string }

export interface Cancellation {
    subscription_id: string;
    status: SubscriptionStatus;
    message: string;
    effective_date: string | null;
}

/** Seats bought and people present, which are two questions.
 *
 *  They agree in the ordinary case and differ in the two that matter: an
 *  organization with no plan has paid for nothing and still has people, and a
 *  team that grew mid-period has more people than it has paid for. */
export interface SeatInfo {
    organization_id: string;
    has_available_seats: boolean;
    seat_limit: number | null;
    current_seats: number;
    member_count: number;
    seats_remaining: number | null;
}

export interface Invoice {
    id: string;
    status: "draft" | "unpaid" | "paid" | "failed" | "void";
    period_start: string;
    period_end: string;
    currency: string;
    seat_count: number;
    total_cents: number;
    checkout_url: string | null;
    paid_at: string | null;
}

export interface InvoiceList { items: Invoice[]; next_page_token: string | null }

/* ── what somebody is on ──────────────────────────────────────────────── */

/** Whether this plan is one anybody pays for.
 *
 *  The single predicate the whole surface turns on, and it reads the price
 *  rather than the name. "Free" is a row in the database like any other and a
 *  deployment may well rename it; a plan that costs nothing is a fact about
 *  the plan. */
export function isPaid(plan: Plan | null | undefined): boolean {
    return Boolean(plan && plan.price_cents > 0);
}

/** Whether the customer behind this subscription is paying us.
 *
 *  Null covers both "never asked" and the 404 a brand-new account gets: a user
 *  who has not run an agent yet has no subscription row at all, because the
 *  free one is written on demand by the usage limiter. Not paying, either way.
 *
 *  Deliberately not `status === "active"`. The free subscription is ACTIVE —
 *  that is the whole reason it blocked purchases until lemma-app #391 — so
 *  status answers "does this entitle them to work", which is a different
 *  question from "have they given us a card". */
export function paying(subscription: Subscription | null | undefined): boolean {
    return Boolean(subscription && isPaid(subscription.plan));
}

/** Whether a tier change can be asked for, or a checkout is the only way.
 *
 *  Mirrors `_validate_move_to` rather than guessing at it: the provider has
 *  nothing to move for a plan that was never bought, so free to paid is a
 *  checkout and only paid to paid is a change. A live subscription with no
 *  provider record — an admin-assigned plan — is in the same position. */
export function canChangeTier(subscription: Subscription | null | undefined): boolean {
    return paying(subscription) && Boolean(subscription?.dodo_subscription_id);
}

/** Which call moves this customer onto that plan. */
export type Move = "current" | "checkout" | "change" | "contact";

export function moveTo(current: Subscription | null | undefined, target: Plan): Move {
    if (isContactSales(target)) return "contact";
    if (current && current.plan_id === target.id) return "current";
    return canChangeTier(current) ? "change" : "checkout";
}

/* ── reading a plan ───────────────────────────────────────────────────── */

function feature(plan: Plan, key: string): unknown {
    return (plan.features ?? {})[key];
}

/** A plan bought by talking to somebody. It has no price and no product at the
 *  provider, so every self-serve path has to refuse it rather than open a
 *  checkout for nothing — the backend says so too, and saying it here keeps a
 *  button that cannot work off the screen. */
export function isContactSales(plan: Plan): boolean {
    return feature(plan, "billing_mode") === "contact_sales";
}

/** Whether the price is per seat. Team plans are, and a price printed without
 *  it understates a five-person team by a factor of five. */
export function isPerSeat(plan: Plan): boolean {
    return feature(plan, "price_unit") === "seat";
}

export function highlights(plan: Plan): string[] {
    const listed = feature(plan, "highlights");
    return Array.isArray(listed) ? listed.filter((line): line is string => typeof line === "string") : [];
}

/** The price, printed.
 *
 *  Whole dollars lose their `.00` — the catalogue is $10, $25 and $200, and
 *  "$10.00/mo" on a plan card is a receipt's precision applied to an
 *  advertisement. Cents are shown where a deployment has actually priced in
 *  them, because dropping those would misquote the price. */
export function formatPrice(cents: number, currency = "USD"): string {
    return new Intl.NumberFormat("en-US", {
        style: "currency",
        currency,
        maximumFractionDigits: cents % 100 === 0 ? 0 : 2,
    }).format(cents / 100);
}

/** The price with its unit, which is how it has to be read to be true. */
export function priceLine(plan: Plan): string {
    if (isContactSales(plan)) return "Talk to us";
    if (plan.price_cents === 0) return "Free";
    const money = formatPrice(plan.price_cents, plan.currency);
    return isPerSeat(plan) ? money + " per seat / month" : money + " / month";
}

/** Where a contact-sales plan sorts.
 *
 *  Last, and not by its price. Enterprise is priced at zero because it has no
 *  price, so sorting on the number would put it in front of the cheapest tier
 *  as though it were the cheapest thing on offer.
 */
function rank(plan: Plan): number {
    return isContactSales(plan) ? Number.MAX_SAFE_INTEGER : plan.price_cents;
}

/** The plans this audience is shown, cheapest first and Enterprise last.
 *
 *  Free is left out rather than drawn as a card nobody can click. It is a real
 *  row and comes back from `/billing/plans` like any other, but it is where
 *  most people already are — the current-plan line names it, and a card
 *  reading "Free · Current plan" is furniture.
 *
 *  A contact-sales plan stays in despite costing nothing, because it is a tier
 *  somebody may want and hiding it answers "is there something above this?"
 *  with silence. Its card has no button; see `moveTo`.
 */
export function offered(plans: Plan[] | undefined, audience: PlanAudience): Plan[] {
    return (plans ?? [])
        .filter((plan) => plan.is_active && plan.plan_type === audience)
        .filter((plan) => plan.price_cents > 0 || isContactSales(plan))
        .sort((a, b) => rank(a) - rank(b));
}

/* ── saying where a subscription stands ───────────────────────────────── */

/** A date, said the way a person would write it. Empty for anything unreadable
 *  rather than `Invalid Date`, which is a rendering fault wearing a date's
 *  clothes. */
export function onDate(value: string | null | undefined): string {
    if (!value) return "";
    const at = Date.parse(value);
    if (Number.isNaN(at)) return "";
    return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long" }).format(new Date(at));
}

/** What is true about this subscription beyond its plan's name, if anything.
 *
 *  Only the states that change what somebody should do. An ordinary active
 *  subscription gets nothing: "Active" under a plan name is a word that has
 *  never told anybody something they could act on.
 */
export function standing(subscription: Subscription | null | undefined): string {
    if (!subscription) return "";
    if (subscription.status === "past_due") {
        return "A payment did not go through. Access continues for now — update the card to keep it.";
    }
    if (subscription.status === "paused") {
        return "Paused: the payment was never completed. Choosing a plan starts it again.";
    }
    if (subscription.status === "pending") return "Waiting for the payment to settle.";
    if (subscription.cancel_at_period_end) {
        const ends = onDate(subscription.current_period_end);
        return ends ? "Will not renew. Access runs to " + ends + "." : "Will not renew.";
    }
    return "";
}

/** Whether that standing is bad news, for the one bit of colour it earns. */
export function isTrouble(subscription: Subscription | null | undefined): boolean {
    return subscription?.status === "past_due" || subscription?.status === "paused";
}
