"use client";

import Link from "next/link";

import { Skeleton } from "@/components/shared/loading";
import { SettingsPanel, SettingsHelpText } from "@/components/settings/settings-kit";
import { formatDate } from "@/lib/billing/format";
import type { SubscriptionWithPlan } from "@/lib/billing/types";

/**
 * How much of this cycle's limit has been used.
 *
 * A percentage, and nothing else. Usage is never quantified in money anywhere a
 * customer reads it: not the allowance, and not the spend against it. What a
 * plan includes may be retuned and what any one request costs depends on the
 * model it routes to, so a figure in dollars invites someone to plan against a
 * number that is neither fixed nor ours to guarantee -- and reading "$3.40
 * used" prompts exactly the arithmetic we are trying not to promise.
 *
 * This card has shrunk twice for that reason: first losing "$150 included",
 * then the "$0 used" headline it was measured against. `usedPercent` comes from
 * the usage API, which reports a percentage consumed and no dollars at all.
 *
 * What is charged -- the plan's price, an invoice total -- is money and stays
 * money. That is a fact about a transaction, not a promise about usage.
 */
export function UsageCycleCard({
    subscription,
    usedPercent,
    loading,
    usageHref,
}: {
    subscription: SubscriptionWithPlan | null | undefined;
    usedPercent: number | null | undefined;
    loading: boolean;
    usageHref: string;
}) {
    // `null` is an uncapped window, which is a different statement from 0% used.
    const hasAllowance = usedPercent !== null && usedPercent !== undefined;

    const periodLabel =
        subscription?.current_period_start && subscription.current_period_end
            ? `${formatDate(subscription.current_period_start)} – ${formatDate(subscription.current_period_end)}`
            : undefined;

    return (
        <SettingsPanel
            className="h-full"
            title="Usage this cycle"
            action={
                periodLabel ? (
                    <span className="text-xs text-[var(--text-tertiary)]">
                        {periodLabel}
                    </span>
                ) : undefined
            }
        >
            {loading ? (
                <div aria-label="Loading usage" className="space-y-3">
                    <Skeleton className="h-8 w-40" />
                    <Skeleton className="h-2 w-full" />
                </div>
            ) : (
                <div className="space-y-3">
                    <p className="flex items-baseline gap-1.5">
                        <span className="text-3xl text-[var(--text-primary)] tabular-nums">
                            {hasAllowance ? `${Math.round(usedPercent as number)}%` : "—"}
                        </span>
                        <span className="text-sm text-[var(--text-tertiary)]">
                            of your limit used
                        </span>
                    </p>

                    {hasAllowance ? (
                        <UsageBar usedPercent={usedPercent as number} />
                    ) : (
                        <SettingsHelpText>
                            This plan has no usage limit.
                        </SettingsHelpText>
                    )}

                    <Link
                        href={usageHref}
                        className="inline-block text-sm text-[var(--action-primary)]"
                    >
                        View usage →
                    </Link>
                </div>
            )}
        </SettingsPanel>
    );
}

function UsageBar({ usedPercent }: { usedPercent: number }) {
    // Over the limit is a real state with a real consequence, so it says so
    // rather than pinning at 100% and going quiet -- as a percentage, never as
    // an amount of money owed.
    const over = usedPercent > 100;

    return (
        <div className="space-y-1.5">
            {/* The same native `progress` the allowance meters use: it owns the
                geometry, so no element here needs an inline width. */}
            <progress
                aria-label="Share of your usage limit used"
                max={100}
                value={Math.min(100, Math.max(0, usedPercent))}
                className={`h-2 w-full overflow-hidden rounded-full border-0 bg-[var(--surface-2)] [&::-webkit-progress-bar]:bg-[var(--surface-2)] [&::-webkit-progress-value]:rounded-full [&::-webkit-progress-value]:bg-current [&::-moz-progress-bar]:bg-current ${
                    over ? "text-[var(--state-warning)]" : "text-[var(--action-primary)]"
                }`}
            />
            <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="text-xs text-[var(--text-tertiary)]">
                    {over
                        ? "Over your limit for this cycle"
                        : `${Math.max(0, Math.round(100 - usedPercent))}% of your limit left`}
                </span>
                <span className="text-xs text-[var(--text-tertiary)] tabular-nums">
                    {Math.round(usedPercent)}%
                </span>
            </div>
        </div>
    );
}
