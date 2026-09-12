/**
 * Billing API client.
 *
 * Plain `fetch` on `buildApiUrl` with the session cookie, never `lemma-sdk`:
 * the SDK is generated from this repo's OpenAPI spec, which excludes
 * `/billing` because those routes only exist in the closed-source cloud
 * deployment. See `lib/billing/types.ts`.
 *
 * A self-hosted install runs the open-source backend, where every route below
 * 404s. `BillingUnavailableError` is how callers tell "this deployment has no
 * billing" apart from "the request failed", so the UI can hide itself rather
 * than render an error nobody can act on.
 */

import { buildApiUrl } from "@/components/auth/portal/auth/config";
import type {
    BillingHistoryResponse,
    CancelSubscriptionResponse,
    PlanListResponse,
    PlanType,
    SeatInfo,
    StartSubscriptionResponse,
    SubscriptionStatusResponse,
    SubscriptionWithPlan,
} from "./types";

/** The route is not mounted: this deployment has no billing. */
export class BillingUnavailableError extends Error {
    constructor() {
        super("Billing is not available on this deployment");
        this.name = "BillingUnavailableError";
    }
}

/** The caller has no subscription yet -- a normal state, not a failure. */
export class NoSubscriptionError extends Error {
    constructor() {
        super("No subscription");
        this.name = "NoSubscriptionError";
    }
}

export class BillingRequestError extends Error {
    readonly status: number;

    constructor(status: number, message: string) {
        super(message);
        this.name = "BillingRequestError";
        this.status = status;
    }
}

async function billingFetch<T>(
    path: string,
    init?: RequestInit & { treat404As?: "unavailable" | "missing" },
): Promise<T> {
    const { treat404As = "unavailable", ...requestInit } = init ?? {};
    const response = await fetch(buildApiUrl(path), {
        credentials: "include",
        cache: "no-store",
        ...requestInit,
        headers: {
            "Content-Type": "application/json",
            ...(requestInit.headers ?? {}),
        },
    });

    if (response.status === 404) {
        // On routes that answer for every authenticated caller, a 404 can only
        // mean the router is absent. On routes that legitimately 404 for a
        // caller with nothing yet, it means exactly that.
        throw treat404As === "missing"
            ? new NoSubscriptionError()
            : new BillingUnavailableError();
    }

    if (!response.ok) {
        let detail = response.statusText;
        try {
            const body = await response.json();
            if (typeof body?.detail === "string") detail = body.detail;
        } catch {
            // A non-JSON error body is not worth failing over; the status
            // carries enough for the message the user sees.
        }
        throw new BillingRequestError(response.status, detail);
    }

    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
}

/**
 * The capability probe.
 *
 * This route answers 200 with `has_subscription: false` for an authenticated
 * user who has never subscribed, so it never 404s when billing is mounted.
 * That makes a 404 unambiguous: the deployment has no billing module.
 */
export function fetchPersonalSubscriptionStatus(): Promise<SubscriptionStatusResponse> {
    return billingFetch<SubscriptionStatusResponse>(
        "/billing/personal/subscription/status",
    );
}

export function fetchPlans(planType?: PlanType): Promise<PlanListResponse> {
    const query = planType ? `?plan_type=${planType}&only_active=true` : "?only_active=true";
    return billingFetch<PlanListResponse>(`/billing/plans${query}`);
}

export function fetchPersonalSubscription(): Promise<SubscriptionWithPlan> {
    return billingFetch<SubscriptionWithPlan>("/billing/personal/subscription", {
        treat404As: "missing",
    });
}

export function startPersonalSubscription(body: {
    plan_id: string;
    success_url: string;
    cancel_url: string;
}): Promise<StartSubscriptionResponse> {
    return billingFetch<StartSubscriptionResponse>("/billing/personal/subscription", {
        method: "POST",
        body: JSON.stringify(body),
    });
}

export function cancelPersonalSubscription(): Promise<CancelSubscriptionResponse> {
    return billingFetch<CancelSubscriptionResponse>(
        "/billing/personal/subscription/cancel",
        { method: "POST" },
    );
}

export function fetchOrganizationSubscription(
    organizationId: string,
): Promise<SubscriptionWithPlan> {
    return billingFetch<SubscriptionWithPlan>(
        `/billing/organizations/${organizationId}/subscription`,
        { treat404As: "missing" },
    );
}

/**
 * Start a per-seat team subscription.
 *
 * Returns a checkout URL: the organization stays `pending` until the payment
 * provider's webhook activates it, so nothing here should show a team as paid
 * on the strength of this call alone.
 */
export function startTeamSubscription(
    organizationId: string,
    body: { plan_id: string; success_url: string; cancel_url: string },
): Promise<StartSubscriptionResponse> {
    return billingFetch<StartSubscriptionResponse>(
        `/billing/organizations/${organizationId}/team-billing`,
        { method: "POST", body: JSON.stringify(body) },
    );
}

export function cancelOrganizationSubscription(
    organizationId: string,
): Promise<CancelSubscriptionResponse> {
    return billingFetch<CancelSubscriptionResponse>(
        `/billing/organizations/${organizationId}/subscription/cancel`,
        { method: "POST" },
    );
}

export function fetchSeatInfo(organizationId: string): Promise<SeatInfo> {
    return billingFetch<SeatInfo>(`/billing/organizations/${organizationId}/seats`);
}

export function fetchBillingHistory(
    organizationId: string,
): Promise<BillingHistoryResponse> {
    return billingFetch<BillingHistoryResponse>(
        `/billing/organizations/${organizationId}/billing-history`,
    );
}
