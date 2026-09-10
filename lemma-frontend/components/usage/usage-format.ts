import type { UsageRecord } from "@/lib/types";

export function formatUsageCost(
  value: number | null | undefined,
  detailed = false,
): string {
  if (value == null || !Number.isFinite(value)) return "Unavailable";
  if (!detailed && value > 0 && value < 0.0001) return "<$0.0001";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: detailed ? 9 : value < 1 ? 4 : 2,
  }).format(value);
}

export function formatUsagePercent(value: number, allowed: boolean): string {
  if (value > 0 && value < 1) return "<1%";
  if (allowed && value > 99) return ">99%";
  return `${Math.floor(value)}%`;
}

export function usageAccountingLabel(record: UsageRecord): string {
  switch (record.metadata?.metering_state) {
    case "PENDING":
      return "Pending";
    case "UNCONFIRMED":
      return "Awaiting usage";
    case "UNPRICED":
      return "Cost unavailable";
    default:
      return record.cost_usd == null ? "Cost unavailable" : "Recorded";
  }
}

export function usageBreakdown(
  source: Record<string, Record<string, unknown>> | undefined,
) {
  return Object.entries(source ?? {})
    .map(([label, values]) => ({
      label,
      cost:
        typeof values.system_cost_usd === "number"
          ? values.system_cost_usd
          : null,
      tokens: typeof values.total_tokens === "number" ? values.total_tokens : 0,
    }))
    .sort((a, b) => (b.cost ?? 0) - (a.cost ?? 0));
}
