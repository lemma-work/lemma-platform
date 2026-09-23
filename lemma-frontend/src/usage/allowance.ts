import type { MyUsageLimitsResponse, UsageAllowanceResponse, UsageRecordResponse } from "lemma-sdk";

/** What an allowance means, and when it is worth saying anything about it.
 *
 *  Spending is metered on every run whether or not anything caps it. Limits
 *  are a separate, optional layer: a deployment states them in settings, or a
 *  plan module supplies them per subscription, or neither and nothing is
 *  capped. So the two questions — "what has this cost" and "will the next
 *  message run" — have different answers and different failure modes, and the
 *  second one is the only one that changes what somebody should do next.
 *
 *  Three windows can apply at once (your week, your month, the organization's
 *  month) and any one of them refuses the run on its own. The server decides
 *  which windows exist and at what fraction to start warning; nothing here
 *  invents either.
 */

/** No windows is not zero usage — it is a deployment that caps nothing.
 *
 *  This distinction is the whole reason the type exists. `used_percent`
 *  defaults to 0 in an empty list, and rendering that as an empty progress bar
 *  tells somebody they have spent none of a limit that was never there.
 */
export type Allowance =
    | { kind: "uncapped" }
    | { kind: "within"; window: UsageAllowanceResponse; percent: number; warn: boolean }
    | { kind: "blocked"; window: UsageAllowanceResponse };

/** The window that matters, which is the one nearest to stopping you.
 *
 *  Not an average and not the first: three windows at 10%, 12% and 99% is a
 *  person about to be refused, and any summary that reports 40% has described
 *  a situation nobody is in.
 */
export function bindingWindow(
    limits: MyUsageLimitsResponse | undefined | null,
): UsageAllowanceResponse | null {
    const windows = limits?.windows ?? [];
    if (windows.length === 0) return null;
    let worst = windows[0];
    for (const window of windows) {
        /* A window that has already refused outranks any percentage: it is the
           reason the next run fails, whatever the others read. */
        if (!window.allowed && worst.allowed) { worst = window; continue; }
        if (window.allowed && !worst.allowed) continue;
        if (window.used_percent > worst.used_percent) worst = window;
    }
    return worst;
}

/** What to say about the account's allowance right now, if anything. */
export function allowanceOf(limits: MyUsageLimitsResponse | undefined | null): Allowance {
    const window = bindingWindow(limits);
    if (!window) return { kind: "uncapped" };
    if (!window.allowed) return { kind: "blocked", window };
    /* The threshold is the server's, not ours. A deployment that wants to warn
       at half its cap says so in `usage_limit_warn_fraction`, and a UI holding
       its own 80% would quietly disagree with the backend that emits the
       warning. Falls back only when the field is missing or nonsensical. */
    const threshold = typeof limits?.warning_percent === "number"
        && limits.warning_percent > 0 && limits.warning_percent <= 100
        ? limits.warning_percent
        : 80;
    const percent = window.used_percent;
    return { kind: "within", window, percent, warn: percent >= threshold };
}

/** Whether the shell should show anything at all.
 *
 *  Quiet until it matters. An allowance bar sitting at 4% all month is chrome
 *  that teaches people to stop looking, so there is nothing to see until the
 *  server's own warning threshold is crossed.
 */
export function worthShowing(limits: MyUsageLimitsResponse | undefined | null): boolean {
    const state = allowanceOf(limits);
    return state.kind === "blocked" || (state.kind === "within" && state.warn);
}

/** Money, at the precision the number deserves.
 *
 *  Sub-cent amounts are ordinary here — a single short run costs a fraction of
 *  a cent — and rounding them to `$0.00` reads as free rather than as small.
 */
export function formatCost(value: number | null | undefined, detailed = false): string {
    if (value === null || value === undefined || !Number.isFinite(value)) return "Unavailable";
    if (!detailed && value > 0 && value < 0.0001) return "<$0.0001";
    return new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: detailed ? 9 : value < 1 ? 4 : 2,
    }).format(value);
}

/** A percentage that never rounds to a lie.
 *
 *  Floor rather than round, so 99.7% of a cap does not display as 100% and
 *  read as refused while the run is still allowed — and so a little spending
 *  never shows as none.
 */
export function formatPercent(value: number, allowed: boolean): string {
    if (!Number.isFinite(value)) return "—";
    if (value > 0 && value < 1) return "<1%";
    if (allowed && value > 99) return ">99%";
    return Math.floor(value) + "%";
}

/** Whose money this is, in words. */
export function planLabel(limits: MyUsageLimitsResponse | undefined | null): string {
    const owner = limits?.plan_type === "TEAM" ? "Organization plan"
        : limits?.plan_type === "PERSONAL" ? "Your plan"
        : "Usage limits";
    return limits?.plan_name ? owner + " · " + limits.plan_name : owner;
}

/** Whether a recorded cost is final.
 *
 *  Metering runs ahead of pricing: a record is written when the work happens
 *  and priced afterwards, so a finished run can hold `cost_usd: null` for a
 *  while and a model with no rate card may never be priced at all. Reporting
 *  those as `$0.00` would be a lie about spending, which is the one number
 *  nobody forgives being wrong.
 */
export function costState(record: Pick<UsageRecordResponse, "cost_usd" | "metadata">): string {
    const state = (record.metadata ?? {})["metering_state"];
    if (state === "PENDING") return "Pending";
    if (state === "UNCONFIRMED") return "Awaiting usage";
    if (state === "UNPRICED") return "Cost unavailable";
    return record.cost_usd === null || record.cost_usd === undefined ? "Cost unavailable" : "Recorded";
}

export interface Slice {
    label: string;
    cost: number | null;
    tokens: number;
}

/** A summary's `total_by_*` map, largest spend first.
 *
 *  The maps come back as loose records, so each value is checked rather than
 *  cast: an unpriced model contributes tokens with no cost, and treating that
 *  missing number as zero would sort a real expense to the bottom.
 */
export function breakdown(source: Record<string, Record<string, unknown>> | undefined | null): Slice[] {
    return Object.entries(source ?? {})
        .map(([label, values]) => ({
            label,
            cost: typeof values?.["system_cost_usd"] === "number" ? (values["system_cost_usd"] as number) : null,
            tokens: typeof values?.["total_tokens"] === "number" ? (values["total_tokens"] as number) : 0,
        }))
        .sort((a, b) => (b.cost ?? 0) - (a.cost ?? 0));
}

/** When a window starts over, said the way a person would say it. */
export function resetsIn(resetAt: string | null | undefined, now = new Date()): string {
    if (!resetAt) return "";
    const at = Date.parse(resetAt);
    if (Number.isNaN(at)) return "";
    const ms = at - now.getTime();
    if (ms <= 0) return "resets now";
    const hours = Math.floor(ms / 3_600_000);
    if (hours < 1) return "resets in under an hour";
    if (hours < 24) return "resets in " + hours + (hours === 1 ? " hour" : " hours");
    const days = Math.round(hours / 24);
    return "resets in " + days + (days === 1 ? " day" : " days");
}
