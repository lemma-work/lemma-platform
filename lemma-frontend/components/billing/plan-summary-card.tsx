"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/shared/loading";
import { SettingsPanel, SettingsHelpText } from "@/components/settings/settings-kit";
import { cn } from "@/lib/utils";
import {
    describeStatus,
    formatCents,
    formatDate,
    intervalNoun,
} from "@/lib/billing/format";
import type { SubscriptionWithPlan } from "@/lib/billing/types";

/**
 * The plan in force, at a glance.
 *
 * Price is set large because it is the fact people come to this page to check.
 * Everything else on the card qualifies it: the status, when money next moves,
 * and the one way out.
 */
export function PlanSummaryCard({
    subscription,
    loading,
    seatCount,
    onCancel,
    cancelling,
    cancelError,
}: {
    subscription: SubscriptionWithPlan | null | undefined;
    loading: boolean;
    seatCount?: number;
    onCancel?: () => void;
    cancelling?: boolean;
    cancelError?: string | null;
}) {
    const [confirming, setConfirming] = useState(false);

    const canCancel =
        Boolean(onCancel) &&
        subscription?.status === "active" &&
        !subscription.cancel_at_period_end;

    return (
        <SettingsPanel
            className="h-full"
            title="Current plan"
            action={
                canCancel && !confirming ? (
                    <Button variant="quiet" size="sm" onClick={() => setConfirming(true)}>
                        Cancel plan
                    </Button>
                ) : undefined
            }
        >
            {loading ? (
                <div aria-label="Loading plan" className="space-y-3">
                    <Skeleton className="h-8 w-48" />
                    <Skeleton className="h-4 w-64" />
                </div>
            ) : !subscription ? (
                <div className="space-y-1">
                    <p className="text-2xl text-[var(--text-primary)]">Free limits</p>
                    <SettingsHelpText>
                        No paid plan. Pick one below to raise your allowances.
                    </SettingsHelpText>
                </div>
            ) : (
                <PlanFacts
                    subscription={subscription}
                    seatCount={seatCount}
                    confirming={confirming}
                    onCancel={onCancel}
                    onKeep={() => setConfirming(false)}
                    cancelling={cancelling}
                    cancelError={cancelError}
                />
            )}
        </SettingsPanel>
    );
}

function PlanFacts({
    subscription,
    seatCount,
    confirming,
    onCancel,
    onKeep,
    cancelling,
    cancelError,
}: {
    subscription: SubscriptionWithPlan;
    seatCount?: number;
    confirming: boolean;
    onCancel?: () => void;
    onKeep: () => void;
    cancelling?: boolean;
    cancelError?: string | null;
}) {
    const status = describeStatus(
        subscription.status,
        subscription.current_period_end,
        subscription.cancel_at_period_end,
    );
    const seats = seatCount ?? subscription.seat_count;
    const unit = subscription.plan.features.price_unit;
    const per = intervalNoun(subscription.plan.features.billing_interval);
    // Per-unit plans price the whole subscription, not one unit of it.
    const total = unit
        ? subscription.plan.price_cents * Math.max(seats, 1)
        : subscription.plan.price_cents;
    const nextDate = formatDate(subscription.current_period_end);

    return (
        <div className="space-y-4">
            <div className="space-y-1.5">
                <div className="flex flex-wrap items-center gap-2">
                    <p className="text-lg font-medium text-[var(--text-primary)]">
                        {subscription.plan.name}
                    </p>
                    <span
                        className={cn(
                            "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs",
                            status.tone === "positive"
                                ? "state-surface-success"
                                : status.tone === "attention"
                                  ? "state-surface-warning"
                                  : "state-surface-info",
                        )}
                    >
                        {status.label}
                    </span>
                </div>

                <p className="flex items-baseline gap-1.5">
                    <span className="text-3xl text-[var(--text-primary)] tabular-nums">
                        {formatCents(total, subscription.plan.currency)}
                    </span>
                    <span className="text-sm text-[var(--text-tertiary)]">/ {per}</span>
                </p>

                {unit ? (
                    <SettingsHelpText>
                        {formatCents(subscription.plan.price_cents, subscription.plan.currency)}{" "}
                        per {unit} &middot; {seats === 1 ? `1 ${unit}` : `${seats} ${unit}s`},
                        counted at each renewal.
                    </SettingsHelpText>
                ) : null}

                {status.detail ? (
                    <SettingsHelpText>{status.detail}</SettingsHelpText>
                ) : nextDate ? (
                    <SettingsHelpText>
                        {subscription.cancel_at_period_end
                            ? `Runs until ${nextDate}.`
                            : `Your next payment is ${formatCents(total, subscription.plan.currency)} on ${nextDate}.`}
                    </SettingsHelpText>
                ) : null}
            </div>

            {confirming ? (
                <div className="space-y-2">
                    <SettingsHelpText>
                        Cancelling drops this account to free limits
                        {nextDate ? ` after ${nextDate}` : " at the end of the period"}.
                    </SettingsHelpText>
                    <div className="flex gap-2">
                        <Button
                            variant="destructive"
                            size="sm"
                            onClick={onCancel}
                            disabled={cancelling}
                        >
                            {cancelling ? "Cancelling…" : "Cancel plan"}
                        </Button>
                        <Button
                            variant="quiet"
                            size="sm"
                            onClick={onKeep}
                            disabled={cancelling}
                        >
                            Keep it
                        </Button>
                    </div>
                </div>
            ) : null}

            {cancelError ? (
                <p role="alert" className="text-xs text-[var(--state-error)]">
                    {cancelError}
                </p>
            ) : null}
        </div>
    );
}
