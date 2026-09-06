"use client";

import type { MyUsageLimitsResponse } from "lemma-sdk";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/shared/loading";
import { formatUsagePercent } from "./usage-format";

export function usagePlanLabel(data: MyUsageLimitsResponse): string {
  const owner =
    data.payer === "organization"
      ? "Organization plan"
      : data.payer === "personal"
        ? "Your plan"
        : "Usage limits";
  return data.plan_name ? `${owner} · ${data.plan_name}` : owner;
}

export function UsageAllowances({
  data,
  loading,
  error,
  retry,
}: {
  data?: MyUsageLimitsResponse;
  loading: boolean;
  error: unknown;
  retry: () => void;
}) {
  if (error)
    return (
      <div
        role="status"
        className="space-y-2 text-sm text-[var(--text-secondary)]"
      >
        <p>Usage is unavailable right now.</p>
        <Button variant="secondary" size="sm" onClick={retry}>
          Try again
        </Button>
      </div>
    );
  if (loading || !data)
    return (
      <div aria-label="Loading usage" className="space-y-4">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-2 w-full" />
      </div>
    );
  return (
    <div className="space-y-5">
      <div>
        <h3 className="text-sm font-medium text-[var(--text-primary)]">
          {usagePlanLabel(data)}
        </h3>
        {data.payer === "organization" ? (
          <p className="mt-1 text-xs text-[var(--text-secondary)]">
            This organization funds your usage here.
          </p>
        ) : data.payer === "personal" ? (
          <p className="mt-1 text-xs text-[var(--text-secondary)]">
            Shared across workspaces using your plan.
          </p>
        ) : null}
      </div>
      {data.windows.length === 0 ? (
        <p className="text-sm text-[var(--text-secondary)]">
          No usage limit configured.
        </p>
      ) : (
        data.windows.map((window) => {
          const color = !window.allowed
            ? "text-[var(--state-error)]"
            : window.used_percent >= data.warning_percent
              ? "text-[var(--state-warning)]"
              : "text-[var(--action-primary)]";
          return (
            <div key={window.key} className="space-y-2">
              <div className="flex items-baseline justify-between gap-3 text-sm">
                <span>{window.label}</span>
                <span className="shrink-0 whitespace-nowrap tabular-nums text-[var(--text-secondary)]">
                  {formatUsagePercent(window.used_percent, window.allowed)} used
                </span>
              </div>
              <progress
                aria-label={window.label}
                max={100}
                value={Math.min(100, Math.max(0, window.used_percent))}
                className={`h-1.5 w-full overflow-hidden rounded-full border-0 bg-[var(--surface-2)] [&::-webkit-progress-bar]:bg-[var(--surface-2)] [&::-webkit-progress-value]:rounded-full [&::-webkit-progress-value]:bg-current [&::-moz-progress-bar]:bg-current ${color}`}
              />
              <p className="text-xs text-[var(--text-tertiary)]">
                {!window.allowed ? "Limit reached · " : ""}Resets{" "}
                <time
                  dateTime={window.reset_at}
                  title={new Date(window.reset_at).toLocaleString()}
                >
                  {new Date(window.reset_at).toLocaleString(undefined, {
                    weekday: "short",
                    hour: "numeric",
                    minute: "2-digit",
                    month: "short",
                    day: "numeric",
                  })}
                </time>
              </p>
            </div>
          );
        })
      )}
    </div>
  );
}
