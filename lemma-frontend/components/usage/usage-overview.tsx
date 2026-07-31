'use client';

import { useMemo, useState } from 'react';
import { AlertCircle, BarChart3, Loader2, ReceiptText } from '@/components/ui/icons';

import { Badge } from '@/components/ui/badge';
import { ResourceMetric, ResourceMetricStrip } from '@/components/pod/resource-layout';
import { InlineEmptyState } from '@/components/shared/empty-state';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';
import { useRecentUsage, useUsageLimits, useUsageStats, useUsageSummary } from '@/lib/hooks/use-usage';
import type { UsageLimits, UsageRecord, UsageStats, UsageSummary } from '@/lib/types';

type UsageScope = 'organization' | 'pod';

interface UsageOverviewProps {
    organizationId: string;
    podId?: string;
    title?: string;
    scope: UsageScope;
}

const DAY_OPTIONS = [
    { value: '7', label: '7 days' },
    { value: '30', label: '30 days' },
    { value: '90', label: '90 days' },
];

export function UsageOverview({ organizationId, podId, scope }: UsageOverviewProps) {
    const [days, setDays] = useState('30');
    const dayCount = Number(days);
    const filters = useMemo(
        () => ({
            days: dayCount,
            limit: 12,
            podId: scope === 'pod' ? podId : undefined,
        }),
        [dayCount, podId, scope]
    );
    const summary = useUsageSummary(organizationId, filters, { enabled: scope !== 'pod' || Boolean(podId) });
    const stats = useUsageStats(organizationId, { ...filters, granularity: 'day' }, { enabled: scope !== 'pod' || Boolean(podId) });
    const recent = useRecentUsage(organizationId, filters, { enabled: scope !== 'pod' || Boolean(podId) });
    const limits = useUsageLimits(organizationId);

    const isLoading = summary.isLoading || stats.isLoading || recent.isLoading || limits.isLoading;
    const error = summary.error || stats.error || recent.error || limits.error;

    return (
        <div className="space-y-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <UsageMetricStrip summary={summary.data} limits={limits.data} scope={scope} />
                <Select value={days} onValueChange={setDays}>
                    <SelectTrigger className="w-full sm:w-[8.5rem]" aria-label="Usage period">
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        {DAY_OPTIONS.map((option) => (
                            <SelectItem key={option.value} value={option.value}>
                                {option.label}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            </div>

            {error ? <UsageError error={error} /> : null}

            {isLoading ? (
                <div className="surface-panel flex min-h-[18rem] items-center justify-center">
                    <Loader2 className="h-5 w-5 animate-spin text-[var(--text-tertiary)]" />
                </div>
            ) : (
                <>
                    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.25fr)_minmax(21rem,0.75fr)]">
                        <UsageTrend stats={stats.data} />
                        <UsageBreakdown summary={summary.data} />
                    </div>
                    <RecentUsageList records={recent.data?.items || []} />
                </>
            )}
        </div>
    );
}

function UsageMetricStrip({
    summary,
    limits,
    scope,
}: {
    summary?: UsageSummary;
    limits?: UsageLimits;
    scope: UsageScope;
}) {
    const limitScope = limits?.org_monthly;
    const userLimitScope = limits?.user_weekly;
    const remaining = scope === 'organization' ? limitScope?.remaining_usd : userLimitScope?.remaining_usd;
    const resetAt = scope === 'organization' ? limitScope?.reset_at : userLimitScope?.reset_at;

    return (
        <ResourceMetricStrip className="lemma-index-tabs-left p-0">
            <ResourceMetric label="Cost" value={formatCurrency(summary?.system_cost_usd)} active />
            <ResourceMetric label="Tokens" value={formatCompact(summary?.total_tokens)} />
            <ResourceMetric
                label={scope === 'organization' ? 'Org monthly left' : 'Your weekly left'}
                value={remaining == null ? 'No cap' : formatCurrency(remaining)}
            />
            <ResourceMetric
                label="Window"
                value={summary?.period_days ? `${summary.period_days} days` : 'No data'}
            />
            {resetAt ? <ResourceMetric label="Resets" value={formatDate(resetAt)} /> : null}
        </ResourceMetricStrip>
    );
}

function UsageTrend({ stats }: { stats?: UsageStats }) {
    const buckets = stats?.items || [];
    const maxCost = Math.max(...buckets.map((bucket) => bucket.system_cost_usd), 0);

    return (
        <section className="surface-panel p-4">
            <div className="mb-4 flex items-start justify-between gap-3">
                <div>
                    <h3 className="text-sm font-semibold text-[var(--text-primary)]">Usage trend</h3>
                    <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">Daily spend buckets from the selected window.</p>
                </div>
                <Badge variant="outline">{stats?.granularity || 'day'}</Badge>
            </div>

            {buckets.length === 0 ? (
                <InlineEmptyState
                    icon={<BarChart3 className="h-4 w-4" />}
                    title="No trend yet"
                    description="Usage will appear here after this scope has activity in the selected window."
                    className="surface-panel-dashed min-h-[9rem] items-center px-4 py-5"
                />
            ) : (
                <div className="space-y-3">
                    {buckets.slice(-12).map((bucket) => {
                        const width = maxCost > 0 ? Math.max(4, (bucket.system_cost_usd / maxCost) * 100) : 4;
                        return (
                            <div key={`${bucket.bucket}-${bucket.group || 'all'}`} className="grid gap-2 sm:grid-cols-[6.5rem_minmax(0,1fr)_5.5rem] sm:items-center">
                                <div className="truncate text-xs text-[var(--text-tertiary)]">{formatBucket(bucket.bucket)}</div>
                                <div className="h-2 rounded-full bg-[var(--surface-2)]">
                                    <div
                                        className={`h-2 rounded-full bg-[var(--action-primary)] ${barWidthClass(width)}`}
                                    />
                                </div>
                                <div className="text-right text-xs font-medium text-[var(--text-secondary)]">
                                    {formatCurrency(bucket.system_cost_usd)}
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}
        </section>
    );
}

function UsageBreakdown({ summary }: { summary?: UsageSummary }) {
    const byModel = flattenTotals(summary?.total_by_model);
    const byKind = flattenTotals(summary?.total_by_kind);

    return (
        <section className="surface-panel p-4">
            <div className="mb-4">
                <h3 className="text-sm font-semibold text-[var(--text-primary)]">Breakdown</h3>
                <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">Grouped totals returned by the billing API.</p>
            </div>

            <div className="space-y-5">
                <BreakdownGroup title="By model" rows={byModel} />
                <BreakdownGroup title="By kind" rows={byKind} />
            </div>
        </section>
    );
}

function BreakdownGroup({ title, rows }: { title: string; rows: BreakdownRow[] }) {
    return (
        <div>
            <p className="mb-2 type-eyebrow">{title}</p>
            {rows.length === 0 ? (
                <p className="surface-panel-dashed px-3 py-3 text-sm text-[var(--text-tertiary)]">
                    No grouped usage returned.
                </p>
            ) : (
                <div className="space-y-2">
                    {rows.slice(0, 5).map((row) => (
                        <div key={`${title}-${row.label}`} className="surface-panel-muted px-3 py-2">
                            <div className="flex items-center justify-between gap-3">
                                <p className="truncate text-sm font-medium text-[var(--text-primary)]">{humanize(row.label)}</p>
                                <p className="shrink-0 text-xs text-[var(--text-secondary)]">{row.primary}</p>
                            </div>
                            {row.detail ? <p className="mt-1 truncate text-xs text-[var(--text-tertiary)]">{row.detail}</p> : null}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}

function RecentUsageList({ records }: { records: UsageRecord[] }) {
    return (
        <section>
            <div className="lemma-index-tabs lemma-index-tabs-left flex-wrap">
                <ResourceMetric label="Recent usage" value={records.length} active />
            </div>

            {records.length === 0 ? (
                <InlineEmptyState
                    icon={<ReceiptText className="h-4 w-4" />}
                    title="No usage events yet"
                    description="Recent model and tool activity will appear here."
                    className="px-1 py-3"
                />
            ) : (
                <div className="lemma-index-list">
                    {records.map((record) => (
                        <div key={record.id} className="lemma-index-row group flex items-center gap-2">
                            <ReceiptText className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" />
                            <div className="flex min-w-0 flex-1 items-baseline gap-2">
                                <p className="truncate text-sm font-medium text-[var(--text-primary)]">{record.model_name || 'Unknown model'}</p>
                                <p className="hidden truncate text-xs text-[var(--text-secondary)] md:block">
                                    {humanize(record.usage_kind)} · {formatDateTime(record.occurred_at || record.created_at)}
                                </p>
                            </div>
                            {record.status ? (
                                <Badge variant={record.status.toLowerCase() === 'success' ? 'success' : 'outline'} className="hidden shrink-0 md:inline-flex">
                                    {humanize(record.status)}
                                </Badge>
                            ) : null}
                            <span className="hidden shrink-0 text-xs text-[var(--text-secondary)] sm:inline">{formatCompact(record.total_tokens)} tokens</span>
                            <span className="shrink-0 text-sm font-medium text-[var(--text-primary)]">{formatCurrency(record.cost_usd)}</span>
                        </div>
                    ))}
                </div>
            )}
        </section>
    );
}

function UsageError({ error }: { error: unknown }) {
    return (
        <div className="state-surface-error flex gap-3 rounded-lg p-4 text-sm text-[var(--text-primary)]">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--state-error)]" />
            <div>
                <p className="font-medium">Usage could not fully load.</p>
                <p className="mt-1 text-[var(--text-secondary)]">{error instanceof Error ? error.message : 'The usage API returned an error.'}</p>
            </div>
        </div>
    );
}

type BreakdownRow = {
    label: string;
    primary: string;
    detail: string;
};

function flattenTotals(source: Record<string, Record<string, number>> | undefined): BreakdownRow[] {
    return Object.entries(source || {})
        .map(([label, values]) => {
            const entries = Object.entries(values || {}).filter(([, value]) => typeof value === 'number');
            const costEntry = entries.find(([key]) => key.toLowerCase().includes('cost'));
            const tokenEntry = entries.find(([key]) => key.toLowerCase().includes('token'));
            const primaryEntry = costEntry || tokenEntry || entries[0];

            return {
                label,
                primary: primaryEntry ? formatMetric(primaryEntry[0], primaryEntry[1]) : 'No value',
                detail: entries
                    .filter(([key]) => key !== primaryEntry?.[0])
                    .slice(0, 3)
                    .map(([key, value]) => formatMetric(key, value))
                    .join(' · '),
            };
        })
        .sort((left, right) => right.primary.localeCompare(left.primary));
}

function formatMetric(key: string, value: number) {
    const label = humanize(key);
    if (key.toLowerCase().includes('cost')) return `${label}: ${formatCurrency(value)}`;
    return `${label}: ${formatCompact(value)}`;
}

function formatCurrency(value: number | null | undefined) {
    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: 'USD',
        maximumFractionDigits: value && value < 1 ? 4 : 2,
    }).format(value || 0);
}

function formatCompact(value: number | null | undefined) {
    return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(value || 0);
}

function formatDate(value: string) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' }).format(date);
}

function formatDateTime(value: string) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat('en-US', {
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
    }).format(date);
}

function formatBucket(value: string) {
    if (/^\d{4}-\d{2}-\d{2}/.test(value)) return formatDate(value);
    return value;
}

function barWidthClass(width: number) {
    if (width >= 96) return 'w-full';
    if (width >= 88) return 'w-11/12';
    if (width >= 80) return 'w-10/12';
    if (width >= 72) return 'w-9/12';
    if (width >= 64) return 'w-8/12';
    if (width >= 56) return 'w-7/12';
    if (width >= 48) return 'w-6/12';
    if (width >= 40) return 'w-5/12';
    if (width >= 32) return 'w-4/12';
    if (width >= 24) return 'w-3/12';
    if (width >= 16) return 'w-2/12';
    return 'w-1/12';
}

function humanize(value: string) {
    return value
        .replace(/[_-]+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim()
        .replace(/\b\w/g, (letter) => letter.toUpperCase());
}
