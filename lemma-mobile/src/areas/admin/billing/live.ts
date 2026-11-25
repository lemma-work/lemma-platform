import type { MyUsageLimitsResponse, UsageStatsResponse, UsageSummaryResponse } from "lemma-sdk";
import type { LiveContext } from "../../context";
import { isMissing } from "../words";
import { readCap, readDays, readSpend } from "./reading";
import type { BillingSource, Payer, SpendScope } from "./source";
import type { Cancellation, ChangeAcknowledged, InvoiceList, PlanList, SeatInfo, StartedCheckout, Subscription } from "@web/billing/plan";

/** The same calls the web makes (`usage/queries.ts`, `billing/queries.ts`):
 *  `/usage/me/*` answers for the signed-in person and needs no role,
 *  `/usage/organizations/*` is the organization's behind an owner-or-editor
 *  check, `/billing/personal/*` is yours, `/billing/organizations/{id}/*`
 *  role-checked. The SDK has no namespace for any of these but the contacts
 *  cap, so they go through `request`. */
export function billingLive(context: LiveContext): BillingSource {
    const api = () => context.client();
    const org = (orgId: string) => "/billing/organizations/" + encodeURIComponent(orgId);

    /** Where the provider sends somebody when the payment ends: the API's own
     *  result pages, which the backend accepts as a return address (it
     *  refuses any other origin), and which say to come back to the app. */
    const returnUrls = () => {
        const base = context.server.api.replace(/\/+$/, "");
        return { success_url: base + "/billing/payment/success", cancel_url: base + "/billing/payment/cancel" };
    };

    /** A read where "there is no such thing" is an answer: a new account has
     *  no subscription until its first run writes the free one. */
    async function orNone<T>(read: Promise<T>): Promise<T | null> {
        try {
            return await read;
        } catch (problem) {
            if (isMissing(problem)) return null;
            throw problem;
        }
    }

    const usagePath = (scope: SpendScope, what: "summary" | "stats") =>
        scope.whose === "mine" ? `/usage/me/${what}` : `/usage/organizations/${encodeURIComponent(scope.orgId)}/${what}`;
    /* Undefined rather than null: with no organization the server answers
       for your own spending everywhere. The organization's paths carry it. */
    const usageParams = (scope: SpendScope, days: number) =>
        scope.whose === "mine" ? { organization_id: scope.orgId ?? undefined, days } : { days };

    return {
        async limits(orgId) {
            return api().request<MyUsageLimitsResponse>("GET", "/usage/me/limits", { params: { organization_id: orgId ?? undefined } });
        },
        async spend(scope, days) {
            return readSpend(await api().request<UsageSummaryResponse>("GET", usagePath(scope, "summary"), { params: usageParams(scope, days) }));
        },
        async daily(scope, days) {
            return readDays(await api().request<UsageStatsResponse>("GET", usagePath(scope, "stats"), { params: usageParams(scope, days) }));
        },
        async contactsCap(orgId) {
            return readCap(await api().contacts.cap(orgId));
        },
        async setContactsCap(orgId, limit) {
            return readCap(await api().contacts.setCap(orgId, limit));
        },

        async plans() {
            const listed = await api().request<PlanList>("GET", "/billing/plans", { params: { only_active: true, limit: 100 } });
            return listed.items ?? [];
        },
        async subscription(payer: Payer) {
            const path = payer === null ? "/billing/personal/subscription" : org(payer) + "/subscription";
            return orNone(api().request<Subscription>("GET", path));
        },
        async seats(orgId) {
            return api().request<SeatInfo>("GET", org(orgId) + "/seats");
        },
        async invoices(orgId) {
            const listed = await api().request<InvoiceList>("GET", org(orgId) + "/billing-history", { params: { limit: 24 } });
            return listed.items ?? [];
        },
        async start(payer, planId) {
            const path = payer === null ? "/billing/personal/subscription" : org(payer) + "/team-billing";
            return api().request<StartedCheckout>("POST", path, { body: { plan_id: planId, ...returnUrls() } });
        },
        async change(payer, planId) {
            const path = payer === null ? "/billing/personal/subscription/change-plan" : org(payer) + "/subscription/change-plan";
            return api().request<ChangeAcknowledged>("POST", path, { body: { plan_id: planId } });
        },
        async cancel(payer) {
            const path = payer === null ? "/billing/personal/subscription/cancel" : org(payer) + "/subscription/cancel";
            return api().request<Cancellation>("POST", path);
        },
    };
}
