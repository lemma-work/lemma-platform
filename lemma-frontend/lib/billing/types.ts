/**
 * Billing types, hand-written on purpose.
 *
 * `/billing/*` lives in the closed-source cloud deployment, so it is absent
 * from the OpenAPI spec this repo generates `lemma-sdk` from -- the generator
 * script prunes billing explicitly. Anything added to the SDK for billing
 * disappears on the next regen, which is why these mirror the cloud DTOs by
 * hand instead. Keep them in step with
 * `lemma_cloud/modules/billing/api/schemas/subscription_schemas.py`.
 */

export type PlanType = "PERSONAL" | "TEAM";

export type SubscriptionStatus =
    | "pending"
    | "active"
    | "cancelled"
    | "expired"
    | "past_due";

export type BillingInvoiceStatus =
    | "draft"
    | "unpaid"
    | "paid"
    | "failed"
    | "void";

/**
 * The plan catalog's `features` blob.
 *
 * Deliberately loose: the cloud catalog types it as a plain dict, and no price
 * or plan name may be hardcoded in this repo -- everything a customer reads
 * comes back from the API, which is also what stops this page drifting from
 * the catalog.
 */
export interface PlanFeatures {
    slug?: string;
    audience?: string;
    billing_mode?: string;
    /** Cadence of the plan's price, e.g. "MONTHLY". Set by the catalog. */
    billing_interval?: string;
    price_unit?: string;
    included_llm_credits_cents?: number;
    remove_lemma_branding?: boolean;
    highlights?: string[];
    [key: string]: unknown;
}

export interface Plan {
    id: string;
    name: string;
    description: string | null;
    plan_type: PlanType;
    price_cents: number;
    currency: string;
    features: PlanFeatures;
    seat_limit: number | null;
    usage_limits: Record<string, unknown>;
    is_active: boolean;
    created_at: string;
    updated_at: string;
}

export interface PlanListResponse {
    items: Plan[];
    next_page_token: string | null;
}

export interface Subscription {
    id: string;
    user_id: string | null;
    organization_id: string | null;
    plan_id: string;
    plan?: Plan | null;
    billing_interval: string;
    status: SubscriptionStatus;
    dodo_subscription_id: string | null;
    current_period_start: string | null;
    current_period_end: string | null;
    seat_count: number;
    cancel_at_period_end: boolean;
    created_at: string;
    updated_at: string;
}

export interface SubscriptionWithPlan extends Subscription {
    plan: Plan;
}

/**
 * The one endpoint that answers for a user with no subscription at all, rather
 * than 404ing. That makes it the capability probe: a 404 here means the route
 * is not mounted, which means this deployment has no billing.
 */
export interface SubscriptionStatusResponse {
    has_subscription: boolean;
    status: SubscriptionStatus | null;
    is_active: boolean;
    plan_name: string | null;
}

export interface StartSubscriptionResponse {
    subscription: Subscription | null;
    /** Null only for a zero-price plan, which activates without checkout. */
    checkout_url: string | null;
    message: string;
}

export interface SeatInfo {
    organization_id: string;
    has_available_seats: boolean;
    seat_limit: number | null;
    current_seats: number;
    seats_remaining: number | null;
}

/**
 * A charge for metered work above the plan's included credits.
 *
 * Team seats are billed by the payment provider on its own cycle and never
 * appear here; this ledger exists because only Lemma knows what a team's
 * agents actually cost.
 */
export interface BillingInvoice {
    id: string;
    user_id: string | null;
    organization_id: string | null;
    plan_id: string;
    subscription_id: string | null;
    status: BillingInvoiceStatus;
    period_start: string;
    period_end: string;
    currency: string;
    seat_count: number;
    amount_cents: number;
    llm_credit_cents: number;
    llm_overage_cents: number;
    total_cents: number;
    dodo_payment_id: string | null;
    checkout_url: string | null;
    due_at: string | null;
    paid_at: string | null;
    metadata: Record<string, unknown>;
    created_at: string;
    updated_at: string;
}

export interface BillingHistoryResponse {
    items: BillingInvoice[];
    next_page_token: string | null;
}

export interface CancelSubscriptionResponse {
    subscription_id: string;
    status: SubscriptionStatus;
    message: string;
    effective_date: string | null;
}
