import { useEffect, useRef } from "react";
import { AppState } from "react-native";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCacheKey } from "../../../data/hooks";
import { adminKey, useAdminSource } from "../hooks";
import type { Payer, SpendScope } from "./source";
import { worthShowing } from "@web/usage/allowance";
import { checkoutPollMs } from "./words";
import { isMissing } from "../words";

/** This part's reads and writes, through react-query. Key every query with
 *  `adminKey(useCacheKey(), "billing", …)`. */
export function useBillingSource() {
    return useAdminSource().billing;
}

/** Your allowance. Read again every half minute while it is near its cap, as
 *  the web does, because it counts money held for runs still going: the
 *  figure climbs during a run and settles after. Every five minutes otherwise. */
export function useMyLimits(orgId: string | null) {
    const source = useBillingSource();
    const key = useCacheKey();
    return useQuery({
        queryKey: adminKey(key, "billing", "limits", orgId),
        queryFn: () => source.limits(orgId),
        staleTime: 20_000,
        refetchInterval: (query) => (worthShowing(query.state.data) ? 30_000 : 5 * 60_000),
        retry: false,
    });
}

/** Totals over the last `days`. Null scope: nothing to ask yet. */
export function useSpend(scope: SpendScope | null, days: number) {
    const source = useBillingSource();
    const key = useCacheKey();
    return useQuery({
        queryKey: adminKey(key, "billing", "spend", scope?.whose, scope?.orgId ?? null, days),
        queryFn: () => source.spend(scope!, days),
        enabled: Boolean(scope),
        staleTime: 60_000,
        retry: false,
    });
}

/** Each day's spending over the last `days`. */
export function useDaily(scope: SpendScope | null, days: number) {
    const source = useBillingSource();
    const key = useCacheKey();
    return useQuery({
        queryKey: adminKey(key, "billing", "daily", scope?.whose, scope?.orgId ?? null, days),
        queryFn: () => source.daily(scope!, days),
        enabled: Boolean(scope),
        staleTime: 60_000,
        retry: false,
    });
}

export function useContactsCap(orgId: string | null) {
    const source = useBillingSource();
    const key = useCacheKey();
    return useQuery({
        queryKey: adminKey(key, "billing", "contacts-cap", orgId),
        queryFn: () => source.contactsCap(orgId!),
        enabled: Boolean(orgId),
        staleTime: 60_000,
        retry: false,
    });
}

export function useSetContactsCap(orgId: string) {
    const source = useBillingSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (limit: number | null) => source.setContactsCap(orgId, limit),
        onSuccess: (saved) => cache.setQueryData(adminKey(key, "billing", "contacts-cap", orgId), saved),
    });
}

/** The plans on offer. Slow to change and the same for everybody, so read
 *  rarely: a price list that moved mid-decision would be worse than a stale one. */
export function usePlans() {
    const source = useBillingSource();
    const key = useCacheKey();
    return useQuery({ queryKey: adminKey(key, "billing", "plans"), queryFn: () => source.plans(), staleTime: 10 * 60_000, gcTime: 30 * 60_000 });
}

/** Whether this server has billing at all: a server built without it
 *  answers its plans with not found, and then Plan and Billing say so once
 *  rather than failing part by part. */
export function useNoBilling(): boolean {
    const plans = usePlans();
    return plans.isError && isMissing(plans.error);
}

/** The checkout poll (`words.ts` `checkoutPollMs`): the clock starts when
 *  watching starts and is put away when it stops. */
function useCheckoutPoll(watch: boolean): () => number | false {
    const since = useRef<number | null>(null);
    return () => {
        if (!watch) {
            since.current = null;
            return false;
        }
        since.current ??= Date.now();
        return checkoutPollMs(Date.now() - since.current);
    };
}

/** What a payer is on: you (null) or an organization. `watch` while a
 *  payment is under way: nothing about a plan changes until the provider
 *  says so, and the only way to learn it has is to keep asking. Its price is
 *  what moves, not its status: the free plan is active too. */
export function useSubscription(payer: Payer, watch: boolean, enabled = true) {
    const source = useBillingSource();
    const key = useCacheKey();
    const poll = useCheckoutPoll(watch);
    return useQuery({
        queryKey: adminKey(key, "billing", "subscription", payer ?? "me"),
        queryFn: () => source.subscription(payer),
        enabled,
        staleTime: watch ? 0 : 60_000,
        refetchInterval: poll,
        retry: false,
    });
}

/** Seats bought and people present. */
export function useSeats(orgId: string | null) {
    const source = useBillingSource();
    const key = useCacheKey();
    return useQuery({ queryKey: adminKey(key, "billing", "seats", orgId), queryFn: () => source.seats(orgId!), enabled: Boolean(orgId), staleTime: 60_000, retry: false });
}

/** An organization's invoices: asked only for those allowed to read them. */
export function useInvoices(orgId: string | null, enabled: boolean) {
    const source = useBillingSource();
    const key = useCacheKey();
    return useQuery({ queryKey: adminKey(key, "billing", "invoices", orgId), queryFn: () => source.invoices(orgId!), enabled: Boolean(orgId) && enabled, staleTime: 5 * 60_000, retry: false });
}

/** Everything a plan change touches is read again: the allowance is worked
 *  out from the plan, so a new plan above last plan's caps would be a lie.
 *  Usage and billing share this part's key, so one call does both. */
export function useSettle() {
    const cache = useQueryClient();
    const key = useCacheKey();
    return () => void cache.invalidateQueries({ queryKey: adminKey(key, "billing") });
}

/** Start paying: answers the checkout, writes nothing until the money lands. */
export function useStart(payer: Payer) {
    const source = useBillingSource();
    const settle = useSettle();
    return useMutation({ mutationFn: (planId: string) => source.start(payer, planId), onSuccess: settle });
}

/** Move between paid plans on the card already on file. */
export function useChange(payer: Payer) {
    const source = useBillingSource();
    const settle = useSettle();
    return useMutation({ mutationFn: (planId: string) => source.change(payer, planId), onSuccess: settle });
}

/** Stop renewing. There is no call that takes it back. */
export function useCancel(payer: Payer) {
    const source = useBillingSource();
    const settle = useSettle();
    return useMutation({ mutationFn: () => source.cancel(payer), onSuccess: settle });
}

/** Call `onReturn` each time the app comes back to the front: from the
 *  phone's browser, after a payment, say. A browser tab opened on Android
 *  answers at once rather than when it closes, so this is how the screen
 *  hears the person is back. */
export function useOnReturn(onReturn: () => void) {
    const latest = useRef(onReturn);
    latest.current = onReturn;
    useEffect(() => {
        let was = AppState.currentState;
        const listening = AppState.addEventListener("change", (next) => {
            if (was !== "active" && next === "active") latest.current();
            was = next;
        });
        return () => listening.remove();
    }, []);
}
