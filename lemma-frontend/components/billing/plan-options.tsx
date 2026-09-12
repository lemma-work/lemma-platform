"use client";

import { Check } from "@/components/ui/icons";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/shared/loading";
import { SettingsHelpText } from "@/components/settings/settings-kit";
import { cn } from "@/lib/utils";
import {
    formatCents,
    formatPlanPrice,
    isContactSales,
    planPriceSuffix,
} from "@/lib/billing/format";
import type { Plan } from "@/lib/billing/types";

/**
 * The plans on offer, side by side.
 *
 * A card each, because choosing a plan means comparing three or four of them on
 * price and on what they include -- a job a single-column list makes you do from
 * memory. Names, prices and features all arrive from the catalog over the wire;
 * nothing about the commercial offer is written into this repo.
 *
 * Exactly one card carries a `primary` button (design.md §8): the dearest plan
 * above the one in force, which is the only action this section exists to
 * propose. Every other card offers a real alternative, so they read `secondary`.
 */
export function PlanOptions({
    plans,
    loading,
    currentPlanId,
    busyPlanId,
    onSelect,
}: {
    plans: Plan[] | undefined;
    loading: boolean;
    currentPlanId?: string | null;
    busyPlanId?: string | null;
    onSelect: (plan: Plan) => void;
}) {
    if (loading) {
        return (
            <section className="space-y-3">
                <PlansHeading />
                <div aria-label="Loading plans" className="grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(14rem,1fr))]">
                    <Skeleton className="h-56 w-full" />
                    <Skeleton className="h-56 w-full" />
                    <Skeleton className="h-56 w-full" />
                </div>
            </section>
        );
    }

    if (!plans?.length) {
        return (
            <section className="space-y-3">
                <PlansHeading />
                <SettingsHelpText>No plans are available right now.</SettingsHelpText>
            </section>
        );
    }

    const current = plans.find((plan) => plan.id === currentPlanId);
    const currentPrice = current?.price_cents ?? -1;
    // The dearest plan above the one in force. Undefined once they are already
    // on the top plan, which is when nothing here should be pushing.
    const suggested = plans
        .filter((plan) => plan.price_cents > currentPrice)
        .sort((a, b) => b.price_cents - a.price_cents)[0];

    return (
        <section className="space-y-3">
            <PlansHeading />
            <div className="grid items-stretch gap-4 [grid-template-columns:repeat(auto-fit,minmax(14rem,1fr))]">
                {plans.map((plan) => (
                    <PlanCard
                        key={plan.id}
                        plan={plan}
                        isCurrent={plan.id === currentPlanId}
                        isUpgrade={plan.price_cents > currentPrice}
                        isSuggested={plan.id === suggested?.id}
                        busy={busyPlanId === plan.id}
                        disabled={Boolean(busyPlanId)}
                        onSelect={() => onSelect(plan)}
                    />
                ))}
            </div>
        </section>
    );
}

function PlansHeading() {
    return (
        <div className="space-y-0.5">
            <h2 className="text-sm font-medium text-[var(--text-primary)]">
                Find your next plan
            </h2>
            <SettingsHelpText>More capacity for the way you work.</SettingsHelpText>
        </div>
    );
}

function PlanCard({
    plan,
    isCurrent,
    isUpgrade,
    isSuggested,
    busy,
    disabled,
    onSelect,
}: {
    plan: Plan;
    isCurrent: boolean;
    /** Dearer than the plan in force, so the move reads as "upgrade". */
    isUpgrade: boolean;
    isSuggested: boolean;
    busy: boolean;
    disabled: boolean;
    onSelect: () => void;
}) {
    const highlights = Array.isArray(plan.features.highlights)
        ? plan.features.highlights
        : [];
    const contactSales = isContactSales(plan);

    return (
        <div
            className={cn(
                "flex h-full flex-col gap-4 rounded-lg border p-5",
                isSuggested
                    ? "border-[color:var(--action-primary)] bg-[var(--action-primary-soft)]"
                    : "border-[color:var(--border-subtle)] bg-[var(--surface-1)]",
            )}
        >
            <div className="space-y-1.5">
                <div className="flex items-start justify-between gap-2">
                    <h3 className="text-sm font-medium text-[var(--text-primary)]">
                        {plan.name}
                    </h3>
                    {isCurrent ? (
                        <span className="shrink-0 text-xs text-[var(--text-tertiary)]">
                            Current
                        </span>
                    ) : null}
                </div>

                <p className="flex items-baseline gap-1.5">
                    <span className="text-2xl text-[var(--text-primary)] tabular-nums">
                        {contactSales
                            ? formatPlanPrice(plan)
                            : formatCents(plan.price_cents, plan.currency)}
                    </span>
                    {contactSales ? null : (
                        <span className="text-xs text-[var(--text-tertiary)]">
                            {planPriceSuffix(plan)}
                        </span>
                    )}
                </p>

                {plan.description ? (
                    <p className="text-xs leading-5 text-[var(--text-tertiary)]">
                        {plan.description}
                    </p>
                ) : null}
            </div>

            {highlights.length ? (
                <ul className="space-y-1.5">
                    {highlights.map((highlight) => (
                        <li
                            key={highlight}
                            className="flex items-start gap-2 text-xs leading-5 text-[var(--text-secondary)]"
                        >
                            <Check
                                aria-hidden
                                className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]"
                                strokeWidth={3}
                            />
                            <span>{highlight}</span>
                        </li>
                    ))}
                </ul>
            ) : null}

            <div className="mt-auto">
                {contactSales ? null : (
                <Button
                    variant={isSuggested ? "primary" : "secondary"}
                    size="sm"
                    className="w-full"
                    disabled={isCurrent || disabled}
                    onClick={onSelect}
                >
                    {isCurrent
                        ? "Current plan"
                        : busy
                          ? "Opening checkout…"
                          : isUpgrade
                            ? `Upgrade to ${plan.name}`
                            : `Switch to ${plan.name}`}
                </Button>
                )}
            </div>
        </div>
    );
}
