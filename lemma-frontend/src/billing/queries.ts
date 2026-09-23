import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiUrl, lemma } from "@/session/client";
import { isMissing } from "@/session/auth-state";
import { live } from "@/usage/queries";
import type {
    ChangeAcknowledged, Cancellation, InvoiceList, Plan, PlanList,
    SeatInfo, StartedCheckout, Subscription,
} from "./plan";

/** Reading and changing what somebody pays.
 *
 *  Written out the way `usage/queries.ts` is, and for a harder version of the
 *  same reason: the SDK ships neither the calls nor the types for `/billing`,
 *  because billing lives in lemma-cloud and the client is generated from the
 *  open-source schema. So the paths are here once rather than at each call
 *  site, and `plan.ts` carries the shapes.
 *
 *  Two families, authorized completely differently. `/billing/personal/*`
 *  belongs to the caller and the session is the authorization. Everything under
 *  `/billing/organizations/{id}/*` is role-checked, and not uniformly — reading
 *  the billing history is not the same permission as starting one.
 */

/** Where the provider sends somebody when the payment flow ends.
 *
 *  The API's own result pages, and deliberately not this app's. The backend
 *  refuses a return URL that is not its `frontend_url` or its `api_url`
 *  (`_own_origin` in the billing schemas), which is an open-redirect guard
 *  worth having: a checkout started with somebody else's `success_url` would
 *  land a customer who has just genuinely paid on a page of the attacker's
 *  choosing, primed to ask for the card again.
 *
 *  The API origin is always one of the two. This app's own origin is one only
 *  where a deployment has registered it, so pointing at ourselves would work in
 *  production and 422 on somebody's laptop — and the failure would land on the
 *  one button whose job is to take money. The API's page already says to come
 *  back here, and this app notices on its own when the plan moves.
 */
function returnUrls(): { success_url: string; cancel_url: string } {
    const api = apiUrl().replace(/\/$/, "");
    return {
        success_url: api + "/billing/payment/success",
        cancel_url: api + "/billing/payment/cancel",
    };
}

const org = (id: string) => "/billing/organizations/" + encodeURIComponent(id);

/** The sample catalogue, fetched only when it is the one being used.
 *
 *  Imported dynamically for the reason `data/index.ts` gives for its fixtures:
 *  sample content has no business in the bundle a live deployment ships. The
 *  two reads below are the only ones that answer in sample mode — an
 *  organization's billing needs an organization, and the sample workspace does
 *  not have one.
 */
async function sample<T>(pick: (module: typeof import("./sample")) => T): Promise<T> {
    return pick(await import("./sample"));
}

/** A read where "there is no such thing" is an answer rather than a fault.
 *
 *  A 404 from `…/subscription` is the ordinary state of a new account: the free
 *  subscription is written on demand by the usage limiter, so somebody who has
 *  not run an agent yet genuinely has none. Reporting that as an error would
 *  put "your plan could not be read" in front of every new customer.
 */
async function orNone<T>(read: Promise<T>): Promise<T | null> {
    try {
        return await read;
    } catch (problem) {
        if (isMissing(problem)) return null;
        throw problem;
    }
}

/** The plans on offer. Slow-moving and the same for everybody, so it is cached
 *  hard — a price list that refetches on focus is a price list that can change
 *  under somebody mid-decision. */
export function usePlans(enabled = true) {
    return useQuery({
        queryKey: ["billing", "plans"],
        queryFn: async (): Promise<PlanList> => live()
            ? lemma().request<PlanList>("GET", "/billing/plans", {
                params: { only_active: true, limit: 100 },
            })
            : { items: await sample((module) => module.samplePlans), next_page_token: null },
        enabled,
        staleTime: 10 * 60_000,
        gcTime: 30 * 60_000,
        select: (data) => data.items as Plan[],
    });
}

/** What the caller is on.
 *
 *  `watch` is the checkout poll. Nothing about a plan changes until the
 *  provider's webhook lands, so there is no client-side moment to react to —
 *  the only way to learn that a payment went through is to keep asking. It is
 *  off by default because that is a request every few seconds and almost
 *  nobody is in a checkout.
 *
 *  Note what is *not* used as the signal: `/personal/subscription/status`
 *  answers `is_active: true` for the free subscription, so a poll on activity
 *  would report success the instant it started. The plan's price is what moves.
 */
export function useMyPlan(watch = false) {
    return useQuery({
        queryKey: ["billing", "my-plan"],
        queryFn: async (): Promise<Subscription | null> => live()
            ? orNone(lemma().request<Subscription>("GET", "/billing/personal/subscription"))
            : sample((module) => module.sampleSubscription),
        enabled: true,
        staleTime: watch ? 0 : 60_000,
        refetchInterval: watch ? 3_000 : false,
        refetchOnWindowFocus: true,
        retry: false,
    });
}

/** What the organization is on. 403 rather than 404 for somebody outside it,
 *  which the caller says out loud instead of retrying. */
export function useOrgPlan(orgId: string | undefined, watch = false) {
    return useQuery({
        queryKey: ["billing", "org-plan", orgId ?? null],
        queryFn: () => orNone(lemma().request<Subscription>("GET", org(orgId!) + "/subscription")),
        enabled: live() && Boolean(orgId),
        staleTime: watch ? 0 : 60_000,
        refetchInterval: watch ? 3_000 : false,
        refetchOnWindowFocus: true,
        retry: false,
    });
}

/** Seats bought and people present. Both, because they are different questions
 *  and the gap between them is the thing worth showing. */
export function useSeats(orgId: string | undefined, enabled = true) {
    return useQuery({
        queryKey: ["billing", "seats", orgId ?? null],
        queryFn: () => lemma().request<SeatInfo>("GET", org(orgId!) + "/seats"),
        enabled: live() && Boolean(orgId) && enabled,
        staleTime: 60_000,
        retry: false,
    });
}

export function useInvoices(orgId: string | undefined, enabled = true) {
    return useQuery({
        queryKey: ["billing", "invoices", orgId ?? null],
        queryFn: () => lemma().request<InvoiceList>("GET", org(orgId!) + "/billing-history", {
            params: { limit: 24 },
        }),
        enabled: live() && Boolean(orgId) && enabled,
        staleTime: 5 * 60_000,
        retry: false,
        select: (data) => data.items,
    });
}

/** Everything a plan change touches.
 *
 *  The allowance windows are computed from the plan, so a screen that refreshed
 *  the subscription and left `usage/me/limits` alone would show the new plan
 *  above last plan's caps — and the sidebar card, which reads the limits, would
 *  keep offering to upgrade somebody who just did.
 */
function useSettle() {
    const queryClient = useQueryClient();
    return () => {
        void queryClient.invalidateQueries({ queryKey: ["billing"] });
        void queryClient.invalidateQueries({ queryKey: ["usage"] });
    };
}

/** Start paying, personally. Answers with a checkout URL and writes nothing:
 *  until the money lands the customer is on the plan they were already on. */
export function useStartPersonal() {
    const settle = useSettle();
    return useMutation({
        mutationFn: (planId: string) => lemma().request<StartedCheckout>(
            "POST", "/billing/personal/subscription",
            { body: { plan_id: planId, ...returnUrls() } },
        ),
        onSuccess: settle,
    });
}

/** Move between paid tiers. The card on file settles the difference, so there
 *  is no checkout — and no new plan in the answer either, because the provider
 *  can decline and our row moves when its event says so. */
export function useChangePersonal() {
    const settle = useSettle();
    return useMutation({
        mutationFn: (planId: string) => lemma().request<ChangeAcknowledged>(
            "POST", "/billing/personal/subscription/change-plan", { body: { plan_id: planId } },
        ),
        onSuccess: settle,
    });
}

export function useCancelPersonal() {
    const settle = useSettle();
    return useMutation({
        mutationFn: () => lemma().request<Cancellation>("POST", "/billing/personal/subscription/cancel"),
        onSuccess: settle,
    });
}

export function useStartTeam(orgId: string) {
    const settle = useSettle();
    return useMutation({
        mutationFn: (planId: string) => lemma().request<StartedCheckout>(
            "POST", org(orgId) + "/team-billing",
            { body: { plan_id: planId, ...returnUrls() } },
        ),
        onSuccess: settle,
    });
}

export function useChangeTeam(orgId: string) {
    const settle = useSettle();
    return useMutation({
        mutationFn: (planId: string) => lemma().request<ChangeAcknowledged>(
            "POST", org(orgId) + "/subscription/change-plan", { body: { plan_id: planId } },
        ),
        onSuccess: settle,
    });
}

export function useCancelTeam(orgId: string) {
    const settle = useSettle();
    return useMutation({
        mutationFn: () => lemma().request<Cancellation>("POST", org(orgId) + "/subscription/cancel"),
        onSuccess: settle,
    });
}
