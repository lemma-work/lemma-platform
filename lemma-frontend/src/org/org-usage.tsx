"use client";

import { useState } from "react";
import { isForbidden } from "@/session/auth-state";
import { live, useOrgStats, useOrgSummary } from "@/usage/queries";
import { breakdown, formatCost } from "@/usage/allowance";

/** What the whole organization has spent.
 *
 *  A different question from the one on the profile, and not a wider version
 *  of it: that one is yours wherever you work, this one is everybody's here.
 *  The endpoint checks membership and refuses otherwise, so being unable to
 *  read it is an ordinary answer rather than a fault — and it is said that way.
 */
const RANGES = [7, 30, 90];

export function OrgUsageSection({ orgId }: { orgId: string }) {
    const [days, setDays] = useState(30);
    const summary = useOrgSummary(orgId, { days });
    const stats = useOrgStats(orgId, { days });

    /* As above: disabled queries never resolve, so say so instead of spinning. */
    if (!live()) {
        return <p className="usage-quiet">The sample workspace does not spend anything, so there is nothing to report here.</p>;
    }

    if (isForbidden(summary.error)) {
        return (
            <p className="usage-quiet">
                Only members of this organization can see what it has spent. Your own usage is on your profile.
            </p>
        );
    }

    const byModel = breakdown(summary.data?.total_by_model);
    const byKind = breakdown(summary.data?.total_by_kind);
    const buckets = stats.data?.items ?? [];
    const peak = buckets.reduce((high, b) => Math.max(high, b.system_cost_usd ?? 0), 0);

    return (
        <div className="usage-panel">
            <section className="usage-block">
                <div className="usage-block__head">
                    <h4>Spending across the organization</h4>
                    <div className="usage-ranges" role="group" aria-label="Range">
                        {RANGES.map((range) => (
                            <button key={range} className="usage-range" aria-pressed={days === range} onClick={() => setDays(range)}>
                                {range}d
                            </button>
                        ))}
                    </div>
                </div>

                {summary.isPending && <p className="usage-quiet" role="status">Adding it up…</p>}
                {summary.isError && !isForbidden(summary.error) && (
                    <p className="usage-quiet">
                        Couldn’t load organization usage. <button className="btn" onClick={() => void summary.refetch()}>Try again</button>
                    </p>
                )}

                {summary.data && <>
                    <dl className="usage-totals">
                        <div><dt>Cost</dt><dd>{formatCost(summary.data.system_cost_usd)}</dd></div>
                        <div><dt>Tokens in</dt><dd>{(summary.data.total_input_tokens ?? 0).toLocaleString()}</dd></div>
                        <div><dt>Tokens out</dt><dd>{(summary.data.total_output_tokens ?? 0).toLocaleString()}</dd></div>
                    </dl>

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

                    {summary.data.total_tokens === 0 && <p className="usage-quiet">Nothing recorded in this window.</p>}
                </>}
            </section>

            {byModel.length > 0 && (
                <section className="usage-block">
                    <h4>By model</h4>
                    <ul className="usage-breakdown">
                        {byModel.slice(0, 8).map((slice) => (
                            <li key={slice.label}>
                                <span>{slice.label}</span>
                                <span className="usage-breakdown__cost">
                                    {slice.cost === null ? "Not priced" : formatCost(slice.cost)}
                                </span>
                            </li>
                        ))}
                    </ul>
                </section>
            )}

            {byKind.length > 0 && (
                <section className="usage-block">
                    <h4>By kind of work</h4>
                    <ul className="usage-breakdown">
                        {byKind.slice(0, 8).map((slice) => (
                            <li key={slice.label}>
                                <span>{slice.label}</span>
                                <span className="usage-breakdown__cost">
                                    {slice.cost === null ? "Not priced" : formatCost(slice.cost)}
                                </span>
                            </li>
                        ))}
                    </ul>
                </section>
            )}
        </div>
    );
}
