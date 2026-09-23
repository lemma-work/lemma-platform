"use client";

import { useState } from "react";
import { live, useMyLimits, useMyStats, useMySummary } from "./queries";
import { allowanceOf, breakdown, formatCost, formatPercent, planLabel, resetsIn } from "./allowance";

/** What the account has spent, and what it is allowed to spend.
 *
 *  Two questions on one screen because people arrive with either, but they are
 *  answered separately: the allowance is about the next run and the totals are
 *  about the ones already made. Where no limit exists the allowance half says
 *  so plainly rather than drawing an empty bar.
 */
const RANGES = [7, 30, 90];

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
    const buckets = stats.data?.items ?? [];
    const peak = buckets.reduce((high, b) => Math.max(high, b.system_cost_usd ?? 0), 0);

    if (nothingToAsk) {
        return <p className="usage-quiet">The sample workspace does not spend anything, so there is nothing to report here.</p>;
    }

    return (
        <div className="usage-panel">
            <section className="usage-block">
                <h4>{planLabel(limits.data)}</h4>
                {limits.isPending && <p className="usage-quiet" role="status">Reading your allowance…</p>}
                {limits.isError && <p className="usage-quiet">Your allowance could not be read.</p>}
                {!limits.isPending && !limits.isError && state.kind === "uncapped" && (
                    /* Not zero of something — none of anything. A deployment
                       states limits or it does not, and this one has not. */
                    <p className="usage-quiet">No spending limit applies to this account. Usage is still recorded below.</p>
                )}
                {windows.map((window) => (
                    <div className="usage-window" key={window.key} data-blocked={!window.allowed}>
                        <div className="usage-window__line">
                            <span>{window.label}</span>
                            <span className="usage-window__much">{formatPercent(window.used_percent, window.allowed)}</span>
                        </div>
                        <span className="usage-window__bar" aria-hidden="true">
                            <i style={{ width: Math.min(100, Math.max(0, window.used_percent)) + "%" }} />
                        </span>
                        <small>{window.allowed ? resetsIn(window.reset_at) : "Spent · " + resetsIn(window.reset_at)}</small>
                    </div>
                ))}
            </section>

            <section className="usage-block">
                <div className="usage-block__head">
                    <h4>What you have spent</h4>
                    <div className="usage-ranges" role="group" aria-label="Range">
                        {RANGES.map((range) => (
                            <button
                                key={range}
                                className="usage-range"
                                aria-pressed={days === range}
                                onClick={() => setDays(range)}
                            >{range}d</button>
                        ))}
                    </div>
                </div>

                {summary.isPending && <p className="usage-quiet" role="status">Adding it up…</p>}
                {summary.isError && <p className="usage-quiet">Your usage could not be read.</p>}
                {summary.data && (
                    <>
                        <dl className="usage-totals">
                            <div><dt>Cost</dt><dd>{formatCost(summary.data.system_cost_usd)}</dd></div>
                            <div><dt>Tokens in</dt><dd>{(summary.data.total_input_tokens ?? 0).toLocaleString()}</dd></div>
                            <div><dt>Tokens out</dt><dd>{(summary.data.total_output_tokens ?? 0).toLocaleString()}</dd></div>
                        </dl>

                        {/* One bar per day, scaled to the busiest. A day with no
                            work is a gap rather than a missing column, so the
                            shape of a week reads correctly. */}
                        {buckets.length > 0 && peak > 0 && (
                            <div className="usage-days" aria-hidden="true">
                                {buckets.map((bucket) => (
                                    <i
                                        key={bucket.bucket}
                                        style={{ height: Math.max(2, Math.round(100 * (bucket.system_cost_usd ?? 0) / peak)) + "%" }}
                                        title={bucket.bucket.slice(0, 10) + " · " + formatCost(bucket.system_cost_usd)}
                                    />
                                ))}
                            </div>
                        )}

                        {byModel.length > 0 && (
                            <ul className="usage-breakdown">
                                {byModel.slice(0, 6).map((slice) => (
                                    <li key={slice.label}>
                                        <span>{slice.label}</span>
                                        <span className="usage-breakdown__cost">
                                            {/* A model with no rate card reports
                                                tokens and no price; saying $0.00
                                                would call that free. */}
                                            {slice.cost === null ? "Not priced" : formatCost(slice.cost)}
                                        </span>
                                    </li>
                                ))}
                            </ul>
                        )}

                        {summary.data.total_tokens === 0 && (
                            <p className="usage-quiet">Nothing recorded in this window.</p>
                        )}
                    </>
                )}
            </section>
        </div>
    );
}
