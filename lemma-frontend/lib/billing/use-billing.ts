"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
    BillingUnavailableError,
    NoSubscriptionError,
    cancelOrganizationSubscription,
    cancelPersonalSubscription,
    fetchBillingHistory,
    fetchOrganizationSubscription,
    fetchPersonalSubscription,
    fetchPersonalSubscriptionStatus,
    fetchPlans,
    fetchSeatInfo,
    startPersonalSubscription,
    startTeamSubscription,
} from "./api";
import type { PlanType } from "./types";

const billingKeys = {
    availability: ["billing", "availability"] as const,
    plans: (planType?: PlanType) => ["billing", "plans", planType ?? "all"] as const,
    personal: ["billing", "personal", "subscription"] as const,
    organization: (id: string) => ["billing", "organization", id, "subscription"] as const,
    seats: (id: string) => ["billing", "organization", id, "seats"] as const,
    history: (id: string) => ["billing", "organization", id, "history"] as const,
};

/**
 * Whether this deployment has billing at all.
 *
 * A self-hosted install runs the open-source backend, which has no `/billing`
 * router, so the probe 404s and every billing entry point hides itself with
 * nothing for the operator to configure. Gating on `isLocalDeployment()`
 * instead would be wrong twice over: it describes the desktop build, not which
 * backend answers, and a hosted install pointed at the OSS backend would still
 * show a surface that cannot work.
 */
export function useBillingAvailable() {
    const query = useQuery({
        queryKey: billingKeys.availability,
        queryFn: fetchPersonalSubscriptionStatus,
        // The answer changes only when the deployment does.
        staleTime: 30 * 60 * 1000,
        // A 404 is a verdict, so stop. Anything else is a blip worth another
        // go: this can fire before the session cookie has settled, and a
        // permanent "undecided" would hide billing on a deployment that has it.
        retry: (failureCount, error) =>
            !(error instanceof BillingUnavailableError) && failureCount < 2,
    });

    const unavailable = query.error instanceof BillingUnavailableError;
    return {
        // Undecided until the probe answers, so nothing flashes into view and
        // back out on a slow network.
        available: query.isSuccess ? true : unavailable ? false : undefined,
        status: query.data,
        isLoading: query.isLoading,
        refetch: query.refetch,
    };
}

export function useBillingPlans(planType?: PlanType, options?: { enabled?: boolean }) {
    return useQuery({
        queryKey: billingKeys.plans(planType),
        queryFn: () => fetchPlans(planType),
        enabled: options?.enabled ?? true,
        retry: false,
    });
}

/**
 * The caller's personal subscription, or `null` when they have none.
 *
 * "No subscription" is an ordinary state -- the backend enrols a user in the
 * free plan on demand -- so it resolves rather than throwing, and only real
 * failures reach `error`.
 */
export function usePersonalSubscription(options?: { enabled?: boolean }) {
    return useQuery({
        queryKey: billingKeys.personal,
        queryFn: async () => {
            try {
                return await fetchPersonalSubscription();
            } catch (error) {
                if (error instanceof NoSubscriptionError) return null;
                throw error;
            }
        },
        enabled: options?.enabled ?? true,
        retry: false,
    });
}

export function useOrganizationSubscription(
    organizationId: string,
    options?: { enabled?: boolean },
) {
    return useQuery({
        queryKey: billingKeys.organization(organizationId),
        queryFn: async () => {
            try {
                return await fetchOrganizationSubscription(organizationId);
            } catch (error) {
                if (error instanceof NoSubscriptionError) return null;
                throw error;
            }
        },
        enabled: (options?.enabled ?? true) && Boolean(organizationId),
        retry: false,
    });
}

export function useSeatInfo(organizationId: string, options?: { enabled?: boolean }) {
    return useQuery({
        queryKey: billingKeys.seats(organizationId),
        queryFn: () => fetchSeatInfo(organizationId),
        enabled: (options?.enabled ?? true) && Boolean(organizationId),
        retry: false,
    });
}

export function useBillingHistory(organizationId: string, options?: { enabled?: boolean }) {
    return useQuery({
        queryKey: billingKeys.history(organizationId),
        queryFn: () => fetchBillingHistory(organizationId),
        enabled: (options?.enabled ?? true) && Boolean(organizationId),
        retry: false,
    });
}

export function useStartPersonalSubscription() {
    return useMutation({
        mutationFn: startPersonalSubscription,
    });
}

export function useStartTeamSubscription(organizationId: string) {
    return useMutation({
        mutationFn: (body: { plan_id: string; success_url: string; cancel_url: string }) =>
            startTeamSubscription(organizationId, body),
    });
}

export function useCancelPersonalSubscription() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: cancelPersonalSubscription,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: billingKeys.personal });
            queryClient.invalidateQueries({ queryKey: billingKeys.availability });
        },
    });
}

export function useCancelOrganizationSubscription(organizationId: string) {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: () => cancelOrganizationSubscription(organizationId),
        onSuccess: () => {
            queryClient.invalidateQueries({
                queryKey: billingKeys.organization(organizationId),
            });
            queryClient.invalidateQueries({ queryKey: billingKeys.seats(organizationId) });
        },
    });
}

/**
 * Poll a subscription until the provider's webhook lands.
 *
 * Checkout redirects the customer back before DodoPayments has told us
 * anything: activation arrives by webhook, seconds later. Showing the new plan
 * on the strength of the redirect alone would be a lie the page could not
 * back up, so the return state polls until the status actually moves.
 */
export function useAwaitActivation(
    scope: { kind: "personal" } | { kind: "organization"; organizationId: string },
    enabled: boolean,
) {
    const queryClient = useQueryClient();
    const queryKey =
        scope.kind === "personal"
            ? billingKeys.personal
            : billingKeys.organization(scope.organizationId);

    return useQuery({
        queryKey: [...queryKey, "awaiting"] as const,
        queryFn: async () => {
            const subscription =
                scope.kind === "personal"
                    ? await fetchPersonalSubscription().catch((error) => {
                          if (error instanceof NoSubscriptionError) return null;
                          throw error;
                      })
                    : await fetchOrganizationSubscription(scope.organizationId).catch(
                          (error) => {
                              if (error instanceof NoSubscriptionError) return null;
                              throw error;
                          },
                      );
            if (subscription?.status === "active") {
                queryClient.invalidateQueries({ queryKey });
            }
            return subscription;
        },
        enabled,
        retry: false,
        // Stop as soon as it is active; give up after roughly a minute so a
        // webhook that never arrives does not poll forever.
        refetchInterval: (query) =>
            query.state.data?.status === "active" || query.state.dataUpdateCount > 20
                ? false
                : 3000,
    });
}
