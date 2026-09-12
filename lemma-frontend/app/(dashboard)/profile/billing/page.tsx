"use client";

import { Suspense, useCallback, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { ProtectedRoute } from "@/components/auth/protected-route";
import { PlainPageShell } from "@/components/dashboard/plain-page-shell";
import { ProductIcon } from "@/components/pod/product-icon";
import { CheckoutReturn } from "@/components/billing/checkout-return";
import { PlanOptions } from "@/components/billing/plan-options";
import { PlanSummaryCard } from "@/components/billing/plan-summary-card";
import { UsageCycleCard } from "@/components/billing/usage-cycle-card";
import { Button } from "@/components/ui/button";
import { SettingsHelpText, SettingsStack } from "@/components/settings/settings-kit";
import { SettingsPageHeading } from "@/components/settings/settings-page-heading";
import { useUsageSummary } from "@/lib/hooks/use-usage";
import {
    useBillingAvailable,
    useBillingPlans,
    useCancelPersonalSubscription,
    usePersonalSubscription,
    useStartPersonalSubscription,
} from "@/lib/billing/use-billing";
import type { Plan } from "@/lib/billing/types";

function PersonalBilling() {
    const router = useRouter();
    const params = useSearchParams();
    const checkoutParam = params.get("checkout");
    const outcome =
        checkoutParam === "success"
            ? "success"
            : checkoutParam === "cancelled"
              ? "cancelled"
              : null;

    const { available } = useBillingAvailable();
    const enabled = available === true;
    const subscription = usePersonalSubscription({ enabled });
    const plans = useBillingPlans("PERSONAL", { enabled });
    const start = useStartPersonalSubscription();
    const cancel = useCancelPersonalSubscription();
    const [busyPlanId, setBusyPlanId] = useState<string | null>(null);

    // The cycle the plan is actually billed on, so the spend shown lines up
    // with the allowance it is measured against rather than a rolling window.
    const usage = useUsageSummary(undefined, { days: 30 }, { enabled, self: true });

    const dismissReturn = useCallback(() => {
        router.replace("/profile/billing");
    }, [router]);

    const choose = useCallback(
        async (plan: Plan) => {
            setBusyPlanId(plan.id);
            try {
                // Come back into the app rather than the provider's own result
                // page, so they land where they can see what they bought.
                const base = `${window.location.origin}/profile/billing`;
                const result = await start.mutateAsync({
                    plan_id: plan.id,
                    success_url: `${base}?checkout=success`,
                    cancel_url: `${base}?checkout=cancelled`,
                });
                if (result.checkout_url) {
                    window.location.assign(result.checkout_url);
                    return;
                }
                await subscription.refetch();
            } finally {
                setBusyPlanId(null);
            }
        },
        [start, subscription],
    );

    const shell = (children: React.ReactNode) => (
        <PlainPageShell
            title="Billing"
            icon={<ProductIcon kind="settings" size="sm" />}
            backHref="/profile"
            backLabel="Profile"
            meta="Account"
            contentWidthClassName="max-w-6xl"
            contentAlign="left"
            contentClassName="pb-16 sm:pb-20"
        >
            {children}
        </PlainPageShell>
    );

    if (available === false) {
        return shell(
            <SettingsHelpText>
                This installation of Lemma does not handle billing.
            </SettingsHelpText>,
        );
    }

    const loadingPlan = available === undefined || subscription.isLoading;
    const startError = start.error instanceof Error ? start.error.message : null;

    return shell(
        <SettingsStack className="office-arrive">
            <SettingsPageHeading
                title="Billing & plans"
                description="Manage your plan, AI usage, and payment details."
            />

            <CheckoutReturn
                key={outcome ?? "none"}
                outcome={outcome}
                scope={{ kind: "personal" }}
                onDismiss={dismissReturn}
            />

            <div className="grid gap-4 lg:grid-cols-2">
                <PlanSummaryCard
                    subscription={subscription.data}
                    loading={loadingPlan}
                    onCancel={() => cancel.mutate()}
                    cancelling={cancel.isPending}
                    cancelError={
                        cancel.error instanceof Error ? cancel.error.message : null
                    }
                />
                <UsageCycleCard
                    subscription={subscription.data}
                    spentUsd={usage.data?.system_cost_usd ?? undefined}
                    loading={loadingPlan || usage.isLoading}
                    usageHref="/profile/usage"
                />
            </div>

            {subscription.error ? (
                <div role="alert" className="space-y-2">
                    <SettingsHelpText>Your plan could not be loaded.</SettingsHelpText>
                    <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => subscription.refetch()}
                    >
                        Try again
                    </Button>
                </div>
            ) : null}

            <PlanOptions
                plans={plans.data?.items}
                loading={available === undefined || plans.isLoading}
                currentPlanId={subscription.data?.plan_id}
                busyPlanId={busyPlanId}
                onSelect={choose}
            />

            {startError ? (
                <p role="alert" className="text-xs text-[var(--state-error)]">
                    {startError}
                </p>
            ) : null}
        </SettingsStack>,
    );
}

export default function PersonalBillingPage() {
    return (
        <ProtectedRoute>
            <Suspense>
                <PersonalBilling />
            </Suspense>
        </ProtectedRoute>
    );
}
