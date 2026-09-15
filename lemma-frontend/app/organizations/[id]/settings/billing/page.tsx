"use client";

import { Suspense, use, useCallback, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { ProtectedRoute } from "@/components/auth/protected-route";
import { PlainPageShell } from "@/components/dashboard/plain-page-shell";
import { ProductIcon } from "@/components/pod/product-icon";
import { CheckoutReturn } from "@/components/billing/checkout-return";
import { PlanOptions } from "@/components/billing/plan-options";
import { PlanSummaryCard } from "@/components/billing/plan-summary-card";
import { UsageCharges } from "@/components/billing/usage-charges";
import { UsageCycleCard } from "@/components/billing/usage-cycle-card";
import { Button } from "@/components/ui/button";
import {
    SettingsHelpText,
    SettingsPanel,
    SettingsStack,
} from "@/components/settings/settings-kit";
import { SettingsPageHeading } from "@/components/settings/settings-page-heading";
import { useOrganizationDetails } from "@/lib/hooks/use-organizations";
import { useUsageSummary } from "@/lib/hooks/use-usage";
import {
    useBillingAvailable,
    useBillingHistory,
    useBillingPlans,
    useCancelOrganizationSubscription,
    useOrganizationSubscription,
    useSeatInfo,
    useStartTeamSubscription,
} from "@/lib/billing/use-billing";
import type { Plan } from "@/lib/billing/types";

function OrganizationBilling({ organizationId }: { organizationId: string }) {
    const router = useRouter();
    const params = useSearchParams();
    const checkoutParam = params.get("checkout");
    const outcome =
        checkoutParam === "success"
            ? "success"
            : checkoutParam === "cancelled"
              ? "cancelled"
              : null;

    const { data: organization } = useOrganizationDetails(organizationId);
    const { available } = useBillingAvailable();
    const enabled = available === true;

    const subscription = useOrganizationSubscription(organizationId, { enabled });
    const plans = useBillingPlans("TEAM", { enabled });
    const seats = useSeatInfo(organizationId, { enabled });
    const history = useBillingHistory(organizationId, {
        enabled: enabled && Boolean(subscription.data),
    });
    const start = useStartTeamSubscription(organizationId);
    const cancel = useCancelOrganizationSubscription(organizationId);
    const [busyPlanId, setBusyPlanId] = useState<string | null>(null);
    const usage = useUsageSummary(organizationId, { days: 30 }, { enabled });

    const dismissReturn = useCallback(() => {
        router.replace(`/organizations/${organizationId}/settings/billing`);
    }, [router, organizationId]);

    const choose = useCallback(
        async (plan: Plan) => {
            setBusyPlanId(plan.id);
            try {
                const base = `${window.location.origin}/organizations/${organizationId}/settings/billing`;
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
        [start, subscription, organizationId],
    );

    const shell = (children: React.ReactNode) => (
        <PlainPageShell
            title="Billing"
            icon={<ProductIcon kind="settings" size="sm" />}
            backHref="/home"
            backLabel="Home"
            meta={organization?.name || "Organization"}
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
                description="Manage your plan, usage, and what this organization is charged."
            />

            <CheckoutReturn
                key={outcome ?? "none"}
                outcome={outcome}
                scope={{ kind: "organization", organizationId }}
                onDismiss={dismissReturn}
            />

            <div className="grid gap-4 lg:grid-cols-2">
                <PlanSummaryCard
                    subscription={subscription.data}
                    loading={loadingPlan}
                    seatCount={seats.data?.current_seats}
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
                    usageHref={`/organizations/${organizationId}/settings/usage`}
                />
            </div>

            {subscription.error ? (
                <div role="alert" className="space-y-2">
                    <SettingsHelpText>
                        This organization&rsquo;s plan could not be loaded.
                    </SettingsHelpText>
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

            {subscription.data ? (
                <SettingsPanel
                    title="Usage charges"
                    description="Metered work above the credits your seats include."
                >
                    <UsageCharges
                        invoices={history.data?.items}
                        loading={history.isLoading}
                    />
                </SettingsPanel>
            ) : null}
        </SettingsStack>,
    );
}

export default function OrganizationBillingPage({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    const { id } = use(params);
    return (
        <ProtectedRoute>
            <Suspense>
                <OrganizationBilling organizationId={id} />
            </Suspense>
        </ProtectedRoute>
    );
}
