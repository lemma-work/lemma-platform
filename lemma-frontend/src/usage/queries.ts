import { useQuery } from "@tanstack/react-query";
import type {
    MyUsageLimitsResponse, UsageListResponse, UsageStatsResponse, UsageSummaryResponse,
} from "lemma-sdk";
import { source } from "@/data";
import { lemma } from "@/session/client";
import { worthShowing } from "./allowance";

/** Reading what the account has spent, and what it is allowed to spend.
 *
 *  None of this is in a namespace — the SDK ships the response types but not
 *  the calls — so the paths are written out here once rather than at each call
 *  site. They are the `/usage/me/*` family, which answers for the signed-in
 *  person and needs no organization role; the `/usage/organizations/*` family
 *  is the same shape behind a membership check.
 */

/** What every usage endpoint accepts. Same set for summary, events and stats.
 *
 *  `days` is how far back to look when no explicit window is given, and the
 *  server refuses a span over a year. `conversation_id` is what makes "what
 *  did this thread cost" answerable at all.
 */
export interface UsageWindow {
    organizationId?: string;
    start?: string;
    end?: string;
    days?: number;
    limit?: number;
    conversationId?: string;
    agentRunId?: string;
}

const DEFAULT_DAYS = 30;
/** The server's own ceiling; asking for more is a 422 rather than a clamp. */
export const MAX_DAYS = 365;

function params(window: UsageWindow): Record<string, string | number | undefined> {
    return {
        organization_id: window.organizationId,
        start: window.start,
        end: window.end,
        days: window.days ?? DEFAULT_DAYS,
        limit: window.limit,
        conversation_id: window.conversationId,
        agent_run_id: window.agentRunId,
    };
}

/** Sample mode has no account behind it, so nothing here can be asked.
 *
 *  Not a guess about the environment: the same reasoning as the auth state,
 *  where firing these would put a token refresh and a 401 behind a view that
 *  is deliberately signed out.
 */
export function live(): boolean {
    return source.label !== "sample";
}

/** The allowance, which is the only usage call whose answer changes what
 *  happens next.
 *
 *  Polled, because `used_percent` counts money reserved for runs still in
 *  flight: the number climbs during a run and settles after it, so a figure
 *  read once is stale by the time anybody acts on it. How often depends on
 *  whether it matters — a bar at 4% does not need a request every half minute,
 *  and one at 95% does.
 */
export function useMyLimits(organizationId?: string | null) {
    return useQuery({
        queryKey: ["usage", "my-limits", organizationId ?? null],
        queryFn: () => lemma().request<MyUsageLimitsResponse>("GET", "/usage/me/limits", {
            /* Undefined rather than null: with no organization the server
               answers for your own windows and leaves the organization's out,
               which is the right answer for somebody not looking at one. */
            params: { organization_id: organizationId ?? undefined },
        }),
        enabled: live(),
        staleTime: 20_000,
        refetchInterval: (query) => (worthShowing(query.state.data) ? 30_000 : 5 * 60_000),
        refetchOnWindowFocus: true,
        refetchOnReconnect: true,
        retry: false,
    });
}

/** Totals over a window, with the by-model and by-kind maps behind them. */
export function useMySummary(window: UsageWindow = {}, enabled = true) {
    return useQuery({
        queryKey: ["usage", "my-summary", window],
        queryFn: () => lemma().request<UsageSummaryResponse>("GET", "/usage/me/summary", { params: params(window) }),
        enabled: live() && enabled,
        staleTime: 60_000,
    });
}

/** One bucket per day. The endpoint reports daily and ungrouped whatever it is
 *  asked for, so there is no granularity to offer and none is pretended. */
export function useMyStats(window: UsageWindow = {}, enabled = true) {
    return useQuery({
        queryKey: ["usage", "my-stats", window],
        queryFn: () => lemma().request<UsageStatsResponse>("GET", "/usage/me/stats", { params: params(window) }),
        enabled: live() && enabled,
        staleTime: 60_000,
    });
}

/** The individual records, newest first, for when a total needs explaining. */
export function useMyEvents(window: UsageWindow = {}, enabled = true) {
    return useQuery({
        queryKey: ["usage", "my-events", window],
        queryFn: () => lemma().request<UsageListResponse>("GET", "/usage/me/events", { params: params(window) }),
        enabled: live() && enabled,
        staleTime: 60_000,
    });
}

/** The organization's own spending, which is a different question from yours
 *  and answerable only by somebody who belongs to it — the endpoint checks the
 *  membership role and refuses otherwise. */
export function useOrgSummary(organizationId: string | undefined, window: UsageWindow = {}, enabled = true) {
    return useQuery({
        queryKey: ["usage", "org-summary", organizationId, window],
        queryFn: () => lemma().request<UsageSummaryResponse>(
            "GET", "/usage/organizations/" + encodeURIComponent(organizationId!) + "/summary",
            { params: params(window) },
        ),
        enabled: live() && Boolean(organizationId) && enabled,
        staleTime: 60_000,
        retry: false,
    });
}

export function useOrgStats(organizationId: string | undefined, window: UsageWindow = {}, enabled = true) {
    return useQuery({
        queryKey: ["usage", "org-stats", organizationId, window],
        queryFn: () => lemma().request<UsageStatsResponse>(
            "GET", "/usage/organizations/" + encodeURIComponent(organizationId!) + "/stats",
            { params: params(window) },
        ),
        enabled: live() && Boolean(organizationId) && enabled,
        staleTime: 60_000,
        retry: false,
    });
}
