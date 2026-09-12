"use client";

import Link from "next/link";

import { Skeleton } from "@/components/shared/loading";
import { SettingsPanel, SettingsHelpText } from "@/components/settings/settings-kit";
import { formatCents, formatDate } from "@/lib/billing/format";
import type { SubscriptionWithPlan } from "@/lib/billing/types";

/**
 * What the plan's included credits have been spent on this cycle.
 *
 * The dollar figure is `system_cost_usd` from the usage ledger -- only work
 * drawn on Lemma's own keys counts, which is the same boundary the plan's
 * included credits are measured against. Anything run on a customer's own
 * provider key is free and is deliberately absent here.
 */
export function UsageCycleCard({
    subscription,
    spentUsd,
    loading,
    usageHref,
}: {
    subscription: SubscriptionWithPlan | null | undefined;
    spentUsd: number | undefined;
    loading: boolean;
    usageHref: string;
}) {
    const seats = subscription?.seat_count ?? 1;
    // Per-unit plans include credits per unit, so the allowance scales with the
    // count the plan is billed on.
    const includedCents =
        (subscription?.plan.features.included_llm_credits_cents ?? 0) *
        (subscription?.plan.features.price_unit ? Math.max(seats, 1) : 1);
    const currency = subscription?.plan.currency ?? "USD";
    const spentCents = spentUsd === undefined ? undefined : Math.round(spentUsd * 100);

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
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                        <p className="flex items-baseline gap-1.5">
                            <span className="text-3xl text-[var(--text-primary)] tabular-nums">
                                {spentCents === undefined
                                    ? "—"
                                    : formatCents(spentCents, currency)}
                            </span>
                            <span className="text-sm text-[var(--text-tertiary)]">
                                used
                            </span>
                        </p>
                        {includedCents > 0 ? (
                            <span className="text-sm text-[var(--text-tertiary)] tabular-nums">
                                {formatCents(includedCents, currency)} included
                            </span>
                        ) : null}
                    </div>

                    {includedCents > 0 && spentCents !== undefined ? (
                        <UsageBar spent={spentCents} included={includedCents} currency={currency} />
                    ) : (
                        <SettingsHelpText>
                            {includedCents > 0
                                ? "Usage for this cycle is still loading."
                                : "This plan has no included credit allowance."}
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

function UsageBar({
    spent,
    included,
    currency,
}: {
    spent: number;
    included: number;
    currency: string;
}) {
    const rawPercent = (spent / included) * 100;
    const remaining = Math.max(0, included - spent);
    // Over the allowance is a real state with a real consequence -- the excess
    // is settled against the payment method -- so it says so rather than
    // pinning the bar at 100% and going quiet.
    const over = spent > included;

    return (
        <div className="space-y-1.5">
            {/* The same native `progress` the allowance meters use: it owns the
                geometry, so no element here needs an inline width. */}
            <progress
                aria-label="Included credits used"
                max={100}
                value={Math.min(100, Math.max(0, rawPercent))}
                className={`h-2 w-full overflow-hidden rounded-full border-0 bg-[var(--surface-2)] [&::-webkit-progress-bar]:bg-[var(--surface-2)] [&::-webkit-progress-value]:rounded-full [&::-webkit-progress-value]:bg-current [&::-moz-progress-bar]:bg-current ${
                    over ? "text-[var(--state-warning)]" : "text-[var(--action-primary)]"
                }`}
            />
            <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="text-xs text-[var(--text-tertiary)]">
                    {over
                        ? `${formatCents(spent - included, currency)} above your included credits`
                        : `${formatCents(remaining, currency)} remaining`}
                </span>
                <span className="text-xs text-[var(--text-tertiary)] tabular-nums">
                    {Math.round(rawPercent)}%
                </span>
            </div>
        </div>
    );
}
