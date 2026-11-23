"use client";

import { useState } from "react";
import { RefreshIcon } from "@/ui/icons";
import { live, useMyLimits, useMyStats, useMySummary } from "./queries";
import {
    allowanceOf, breakdown, computeHint, computeOf, formatComputeUnits, formatCost, formatPercent, planLabel, resetsIn,
    type Slice,
} from "./allowance";

/** The windows a total can be read over. */
export const RANGES = [7, 30, 90];

/** Which window a total covers: chips from the kit, the one in use in ink. */
export function UsageRanges({ days, onDays }: { days: number; onDays: (days: number) => void }) {
    return (
        <div className="kset-range" role="group" aria-label="Range">
            {RANGES.map((range) => (
                <button key={range} type="button" className="kchip" aria-pressed={days === range} onClick={() => onDays(range)}>
                    {range} days
                </button>
            ))}
        </div>
    );
}

type Summary = {
    system_cost_usd?: number | null;
    total_input_tokens?: number | null;
    total_output_tokens?: number | null;
    total_tokens?: number | null;
    total_by_kind?: Record<string, Record<string, unknown>> | null;
};

type Bucket = { bucket: string; system_cost_usd?: number | null };

/** What a window cost, as one big figure, with the tokens and machine time
 *  under it and a bar a day beneath. Shared by your usage and the
 *  organization's, which ask the same question of different spenders. */
export function UsageTotal({ summary, buckets, days }: { summary: Summary; buckets: Bucket[]; days: number }) {
    const compute = computeOf(summary.total_by_kind);
    const peak = buckets.reduce((high, b) => Math.max(high, b.system_cost_usd ?? 0), 0);
    return (
        <>
            <p className="kfigure">
                <b>{formatCost(summary.system_cost_usd)}</b>
                <span>in the last {days} days</span>
            </p>
            <p className="kset-figures">
                <span>{(summary.total_input_tokens ?? 0).toLocaleString()} tokens in</span>
                <span>{(summary.total_output_tokens ?? 0).toLocaleString()} tokens out</span>
                {compute && (
                    /* Machine time beside the spend, in its own unit. Its
                       cost is already inside the figure above. */
                    <span title={computeHint(compute)}>{formatComputeUnits(compute.units)} of compute</span>
                )}
            </p>
            {/* One bar per day, scaled to the busiest. A day with no work is
                a gap rather than a missing column, so the shape of a week
                reads correctly. */}
            {buckets.length > 0 && peak > 0 && (
                <div className="kset-days" aria-hidden="true">
                    {buckets.map((bucket) => (
                        <i
                            key={bucket.bucket}
                            style={{ height: Math.max(2, Math.round(100 * (bucket.system_cost_usd ?? 0) / peak)) + "%" }}
                            title={bucket.bucket.slice(0, 10) + " · " + formatCost(bucket.system_cost_usd)}
                        />
                    ))}
                </div>
            )}
            {summary.total_tokens === 0 && !compute && <p className="kset-note">Nothing recorded in this window.</p>}
        </>
    );
}

/** A breakdown as rows: what it is, its share of the largest, what it cost. */
export function UsageRows({ title, slices }: { title: string; slices: Slice[] }) {
    if (slices.length === 0) return null;
    const most = slices.reduce((high, slice) => Math.max(high, slice.cost ?? 0), 0);
    return (
        <>
            <p className="ksub">{title}</p>
            <div className="klist">
                {slices.map((slice) => (
                    <div className="krow" key={slice.label}>
                        <span className="krow__v"><b>{slice.label}</b></span>
                        <span className="krow__end">
                            {most > 0 && slice.cost !== null && (
                                <span className="kset-bar" aria-hidden="true"><i style={{ width: (100 * slice.cost / most) + "%" }} /></span>
                            )}
                            {/* A model with no rate card reports tokens and no
                                price; saying $0.00 would call that free. */}
                            <span className="kset-fig">{slice.cost === null ? "Not priced" : formatCost(slice.cost)}</span>
                        </span>
                    </div>
                ))}
            </div>
        </>
    );
}

/** What the account has spent, and what it is allowed to spend.
 *
 *  Two questions on one screen because people arrive with either, but they are
 *  answered separately: the totals are about the work already done and the
 *  allowance about the next run. Where no limit exists the allowance says so
 *  plainly rather than drawing an empty bar.
 */
export function UsagePanel({ orgId }: { orgId?: string | null }) {
    const [days, setDays] = useState(30);
    /* Sample mode has no account to have spent anything. Its queries are
       disabled, and a disabled query is pending forever — so without this the
       panel sits on "Adding it up…" for as long as anybody watches it. */
    const nothingToAsk = !live();
    const limits = useMyLimits(orgId);
    const summary = useMySummary({ organizationId: orgId ?? undefined, days });
    const stats = useMyStats({ organizationId: orgId ?? undefined, days });

    const state = allowanceOf(limits.data);
    const windows = limits.data?.windows ?? [];
    const byModel = breakdown(summary.data?.total_by_model);

    if (nothingToAsk) {
        return <p className="kset-note">The sample workspace does not spend anything, so there is nothing to report here.</p>;
    }

    return (
        <div className="kset-stack">
            <UsageRanges days={days} onDays={setDays} />
            {summary.isPending && <p className="kset-note" role="status">Adding it up…</p>}
            {summary.isError && (
                <p className="kset-said kset-said--bad" role="alert">
                    Your usage couldn’t be read.
                    <button type="button" className="b b--s b--secondary" onClick={() => void summary.refetch()}><RefreshIcon /> Try again</button>
                </p>
            )}
            {summary.data && <>
                <UsageTotal summary={summary.data} buckets={stats.data?.items ?? []} days={days} />
                <UsageRows title="By model" slices={byModel.slice(0, 6)} />
            </>}

            <p className="ksub">{planLabel(limits.data)} <span>What you may still use</span></p>
            {limits.isPending && <p className="kset-note" role="status">Reading your allowance…</p>}
            {limits.isError && (
                <p className="kset-said kset-said--bad" role="alert">
                    Your allowance couldn’t be read.
                    <button type="button" className="b b--s b--secondary" onClick={() => void limits.refetch()}><RefreshIcon /> Try again</button>
                </p>
            )}
            {!limits.isPending && !limits.isError && state.kind === "uncapped" && (
                /* Not zero of something — none of anything. A deployment
                   states limits or it does not, and this one has not. */
                <p className="kset-note">No spending limit applies to this account. Usage is still recorded above.</p>
            )}
            {windows.length > 0 && (
                <div className="klist">
                    {windows.map((window) => (
                        <div className="krow" key={window.key}>
                            <span className="krow__v">
                                <b>{window.label}</b>
                                <span>{resetsIn(window.reset_at)}</span>
                            </span>
                            <span className="krow__end">
                                {/* The bar is capped at full: past the limit the
                                    number keeps climbing, and a bar that kept
                                    pace would leave its row. */}
                                <span className="kset-bar" aria-hidden="true">
                                    <i style={{ width: Math.min(100, Math.max(0, window.used_percent)) + "%" }} />
                                </span>
                                {!window.allowed && <span className="kstatus kstatus--bad">Spent</span>}
                                <span className="kset-fig">{formatPercent(window.used_percent, window.allowed)}</span>
                            </span>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
