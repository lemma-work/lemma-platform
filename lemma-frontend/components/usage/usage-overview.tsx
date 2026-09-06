"use client";

import { useState, type ReactNode } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  ResourceMetric,
  ResourceMetricStrip,
} from "@/components/pod/resource-layout";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/shared/loading";
import {
  useRecentUsage,
  useMyUsageLimits,
  useUsageStats,
  useUsageSummary,
} from "@/lib/hooks/use-usage";
import { useAccessiblePods } from "@/lib/hooks/use-pods";
import type { UsageRecord } from "@/lib/types";
import { UsageAllowances } from "./usage-allowances";
import {
  formatUsageCost,
  usageAccountingLabel,
  usageBreakdown,
} from "./usage-format";

type UsageScope = "organization" | "pod" | "personal";

export function UsageOverview({
  organizationId,
  podId,
  scope,
}: {
  organizationId?: string;
  podId?: string;
  title?: string;
  scope: UsageScope;
}) {
  const navigation = useAccessiblePods({ enabled: scope !== "personal" });
  const role = navigation.data.organizations.find(
    (org) => org.id === organizationId,
  )?.role;
  const canReadOrganization = role === "ORG_OWNER" || role === "ORG_EDITOR";
  const self = scope === "personal";
  const enabled =
    self || (canReadOrganization && (scope !== "pod" || Boolean(podId)));
  const [days, setDays] = useState("30");
  const [limit, setLimit] = useState(50);
  const [conversationId, setConversationId] = useState("");
  const [agentRunId, setAgentRunId] = useState("");
  const [applied, setApplied] = useState({
    conversationId: "",
    agentRunId: "",
  });
  const filters = {
    days: Number(days),
    podId: scope === "pod" ? podId : undefined,
    ...applied,
  };
  const summary = useUsageSummary(organizationId, filters, { enabled, self });
  const stats = useUsageStats(
    organizationId,
    { ...filters, granularity: "day" },
    { enabled, self },
  );
  const recent = useRecentUsage(
    organizationId,
    { ...filters, limit },
    { enabled, self },
  );
  const limits = useMyUsageLimits(organizationId);
  const buckets = [...(stats.data?.items ?? [])].sort((a, b) =>
    a.bucket.localeCompare(b.bucket),
  );
  const maxCost = Math.max(
    0,
    ...buckets.map((bucket) => bucket.system_cost_usd),
  );
  return (
    <div className="space-y-6">
      <section className="surface-panel p-5">
        <UsageAllowances
          data={limits.data}
          loading={limits.isPending}
          error={limits.error}
          retry={() => void limits.refetch()}
        />
      </section>
      {!self && !canReadOrganization ? (
        <QuerySection
          loading={navigation.isLoading}
          error={navigation.error}
          retry={() => void navigation.refetch()}
        >
          <p className="text-sm text-[var(--text-secondary)]">
            Organization spending details are available to owners and editors.
          </p>
          <Link
            href={`/profile/usage?organizationId=${encodeURIComponent(organizationId ?? "")}`}
            className="text-sm text-[var(--action-primary)]"
          >
            View your own usage →
          </Link>
        </QuerySection>
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <h2 className="text-base font-semibold">
                {self
                  ? "Your activity"
                  : scope === "pod"
                    ? "Pod activity"
                    : "Organization activity"}
              </h2>
              <p className="mt-1 text-sm text-[var(--text-secondary)]">
                Recorded spend and tokens in the selected period. Pending or
                unavailable costs are not included in spend.
              </p>
            </div>
            <Select
              value={days}
              onValueChange={(value) => {
                setDays(value);
                setLimit(50);
              }}
            >
              <SelectTrigger className="w-32" aria-label="Usage period">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[7, 30, 90].map((day) => (
                  <SelectItem key={day} value={String(day)}>
                    {day} days
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <details>
            <summary className="cursor-pointer text-sm text-[var(--text-secondary)]">
              Filter by conversation or run
            </summary>
            <form
              className="mt-3 flex flex-wrap items-end gap-3"
              onSubmit={(event) => {
                event.preventDefault();
                setApplied({
                  conversationId: conversationId.trim(),
                  agentRunId: agentRunId.trim(),
                });
                setLimit(50);
              }}
            >
              <label className="space-y-1 text-xs text-[var(--text-secondary)]">
                Conversation ID
                <Input
                  value={conversationId}
                  onChange={(event) => setConversationId(event.target.value)}
                  placeholder="All conversations"
                />
              </label>
              <label className="space-y-1 text-xs text-[var(--text-secondary)]">
                Run ID
                <Input
                  value={agentRunId}
                  onChange={(event) => setAgentRunId(event.target.value)}
                  placeholder="All runs"
                />
              </label>
              <Button type="submit" variant="secondary" size="sm">
                Apply filters
              </Button>
            </form>
          </details>
          <QuerySection
            loading={summary.isPending}
            error={summary.error}
            retry={() => void summary.refetch()}
          >
            <ResourceMetricStrip>
              <ResourceMetric
                label="Recorded spend"
                value={formatUsageCost(summary.data?.system_cost_usd)}
              />
              <ResourceMetric
                label="Tokens"
                value={summary.data?.total_tokens.toLocaleString() ?? "—"}
              />
            </ResourceMetricStrip>
            <div className="mt-4 grid gap-5 sm:grid-cols-2">
              {(["total_by_model", "total_by_kind"] as const).map(
                (key, index) => (
                  <div key={key}>
                    <h3 className="mb-3 text-sm font-medium">
                      {index === 0 ? "By model" : "By activity"}
                    </h3>
                    {usageBreakdown(summary.data?.[key]).length === 0 ? (
                      <p className="text-sm text-[var(--text-tertiary)]">
                        No activity in this period.
                      </p>
                    ) : (
                      usageBreakdown(summary.data?.[key]).map((row) => (
                        <div
                          key={row.label}
                          className="flex items-baseline justify-between gap-3 border-b border-[var(--border-subtle)] py-2 text-sm"
                        >
                          <span className="min-w-0 truncate" title={row.label}>
                            {row.label}
                          </span>
                          <span className="shrink-0 text-[var(--text-secondary)]">
                            {formatUsageCost(row.cost)} ·{" "}
                            {row.tokens.toLocaleString()} tokens
                          </span>
                        </div>
                      ))
                    )}
                  </div>
                ),
              )}
            </div>
          </QuerySection>
          <QuerySection
            loading={stats.isPending}
            error={stats.error}
            retry={() => void stats.refetch()}
          >
            <h3 className="mb-4 text-sm font-medium">Daily recorded spend</h3>
            {buckets.length === 0 ? (
              <p className="text-sm text-[var(--text-tertiary)]">
                No activity in this period.
              </p>
            ) : (
              <div className="max-h-80 space-y-3 overflow-y-auto">
                {buckets.map((bucket) => (
                  <div
                    key={bucket.bucket}
                    className="grid grid-cols-[5rem_minmax(0,1fr)_6rem] items-center gap-3 text-xs"
                  >
                    <span className="text-[var(--text-secondary)]">
                      {new Date(bucket.bucket).toLocaleDateString(undefined, {
                        month: "short",
                        day: "numeric",
                      })}
                    </span>
                    <meter
                      aria-label={`Recorded spend ${bucket.bucket}`}
                      min={0}
                      max={maxCost || 1}
                      value={bucket.system_cost_usd}
                      className="h-2 w-full [&::-webkit-meter-bar]:border-0 [&::-webkit-meter-bar]:bg-[var(--surface-2)] [&::-webkit-meter-optimum-value]:bg-[var(--action-primary)]"
                    />
                    <span className="text-right tabular-nums">
                      {formatUsageCost(bucket.system_cost_usd)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </QuerySection>
          <QuerySection
            loading={recent.isPending}
            error={recent.error}
            retry={() => void recent.refetch()}
          >
            <h3 className="mb-3 text-sm font-medium">Recent activity</h3>
            {!recent.data?.items.length ? (
              <p className="text-sm text-[var(--text-tertiary)]">
                No activity in this period.
              </p>
            ) : (
              <div className="divide-y divide-[var(--border-subtle)]">
                {recent.data.items.map((record) => (
                  <UsageActivity key={record.id} record={record} />
                ))}
              </div>
            )}
            {recent.data?.items.length === limit && limit < 1000 ? (
              <Button
                variant="secondary"
                size="sm"
                className="mt-4"
                onClick={() => setLimit(Math.min(1000, limit * 2))}
              >
                Load more
              </Button>
            ) : null}
            {recent.data?.items.length === 1000 ? (
              <p className="mt-3 text-xs text-[var(--text-tertiary)]">
                Showing the most recent 1,000 records. Narrow the period or
                filter by conversation or run.
              </p>
            ) : null}
          </QuerySection>
        </>
      )}
    </div>
  );
}

function QuerySection({
  loading,
  error,
  retry,
  children,
}: {
  loading: boolean;
  error: unknown;
  retry: () => void;
  children: ReactNode;
}) {
  return (
    <section className="surface-panel p-5">
      {error ? (
        <div role="status" className="space-y-3">
          <p className="text-sm text-[var(--text-secondary)]">
            This usage section could not load.
          </p>
          <Button variant="secondary" size="sm" onClick={retry}>
            Try again
          </Button>
        </div>
      ) : loading ? (
        <Skeleton aria-label="Loading usage" className="h-20 w-full" />
      ) : (
        children
      )}
    </section>
  );
}

function UsageActivity({ record }: { record: UsageRecord }) {
  return (
    <details className="py-3">
      <summary className="flex cursor-pointer flex-wrap items-center justify-between gap-2 text-sm">
        <span className="min-w-0 truncate font-medium">
          {record.model_name}
        </span>
        <span className="text-xs text-[var(--text-secondary)]">
          {new Date(record.occurred_at).toLocaleString()}
        </span>
        <span className="tabular-nums">{formatUsageCost(record.cost_usd)}</span>
      </summary>
      <div className="mt-3 grid gap-2 text-xs text-[var(--text-secondary)] sm:grid-cols-2">
        <p>Accounting: {usageAccountingLabel(record)}</p>
        <p>Run: {record.status ?? "In progress"}</p>
        <p>
          Input: {record.input_tokens.toLocaleString()} · Output:{" "}
          {record.output_tokens.toLocaleString()}
        </p>
        <p>Recorded cost: {formatUsageCost(record.cost_usd, true)}</p>
        <p>
          Cached input:{" "}
          {record.cached_input_tokens?.toLocaleString() ?? "Unavailable"}
        </p>
        <p>
          Cache writes:{" "}
          {record.cache_write_tokens?.toLocaleString() ?? "Unavailable"}
        </p>
        {record.agent_run_id ? (
          <p className="break-all">Run ID: {record.agent_run_id}</p>
        ) : null}
        {record.conversation_id && record.pod_id ? (
          <Link
            className="text-[var(--action-primary)]"
            href={`/pod/${encodeURIComponent(record.pod_id)}/conversations/${encodeURIComponent(record.conversation_id)}`}
          >
            Open conversation →
          </Link>
        ) : null}
      </div>
    </details>
  );
}
