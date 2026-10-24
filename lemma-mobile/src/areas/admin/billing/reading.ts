import type { ContactsCapResponse, UsageStatsResponse, UsageSummaryResponse } from "lemma-sdk";
import type { ContactsCap, DaySpend, Spend } from "./source";
import { breakdown } from "@web/usage/allowance";

/** The server's usage answers, read into the phone's shapes. Pure, so
 *  `tests/` can hold it. */

/** A summary's totals and its by-model and by-kind maps, largest spend first.
 *  A cost the server could not give stays null, never a zero. */
export function readSpend(summary: UsageSummaryResponse): Spend {
    const cost = summary.system_cost_usd;
    return {
        cost: typeof cost === "number" && Number.isFinite(cost) ? cost : null,
        tokensIn: summary.total_input_tokens ?? 0,
        tokensOut: summary.total_output_tokens ?? 0,
        tokens: summary.total_tokens ?? 0,
        byModel: breakdown(summary.total_by_model),
        byKind: breakdown(summary.total_by_kind),
    };
}

/** The daily buckets, oldest first, each as its date and its dollars. */
export function readDays(stats: Pick<UsageStatsResponse, "items">): DaySpend[] {
    return (stats.items ?? [])
        .map((bucket) => ({ day: bucket.bucket.slice(0, 10), cost: bucket.system_cost_usd ?? 0 }))
        .sort((a, b) => a.day.localeCompare(b.day));
}

export function readCap(wire: Pick<ContactsCapResponse, "monthly_limit_usd" | "spent_this_month_usd">): ContactsCap {
    return { limit: wire.monthly_limit_usd ?? null, spent: wire.spent_this_month_usd ?? 0 };
}
