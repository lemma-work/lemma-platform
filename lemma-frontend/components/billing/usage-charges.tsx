"use client";

import { Skeleton } from "@/components/shared/loading";
import { EmptyState } from "@/components/shared/empty-state";
import { SettingsList, SettingsRow } from "@/components/settings/settings-kit";
import { formatCents, formatDate, formatPeriod } from "@/lib/billing/format";
import type { BillingInvoice } from "@/lib/billing/types";

/**
 * Charges for metered work above the plan's included credits.
 *
 * Rows in the same list language as members and invitations, rather than a
 * table: one line per month is not tabular data, and a settings page should not
 * change shape halfway down.
 *
 * Seats are absent on purpose -- the payment provider bills those on its own
 * cycle and this ledger never sees them. What it holds is the number only Lemma
 * can know: what a team's agents cost beyond what their seats included.
 */
export function UsageCharges({
    invoices,
    loading,
}: {
    invoices: BillingInvoice[] | undefined;
    loading: boolean;
}) {
    if (loading) {
        return (
            <div aria-label="Loading charges" className="space-y-2">
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
            </div>
        );
    }

    const charges = (invoices ?? []).filter((invoice) => invoice.total_cents > 0);

    if (!charges.length) {
        return (
            <EmptyState
                variant="inline"
                title="No usage charges yet"
                description="Work above your included credits is settled monthly and appears here."
            />
        );
    }

    return (
        <SettingsList>
            {charges.map((invoice) => (
                <SettingsRow key={invoice.id}>
                    <div className="min-w-0">
                        <p className="text-sm font-medium text-[var(--text-primary)]">
                            {formatPeriod(invoice.period_start)}
                        </p>
                        <p className="text-xs text-[var(--text-tertiary)]">
                            {invoice.status === "paid"
                                ? invoice.paid_at
                                    ? `Paid ${formatDate(invoice.paid_at)}`
                                    : "Paid"
                                : invoice.status === "unpaid"
                                  ? "Due at next settlement"
                                  : invoice.status}
                        </p>
                    </div>
                    <p className="text-sm font-medium text-[var(--text-primary)]">
                        {formatCents(invoice.total_cents, invoice.currency)}
                    </p>
                </SettingsRow>
            ))}
        </SettingsList>
    );
}
