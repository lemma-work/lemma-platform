import type { UsageRecord } from "@/lib/types";

/**
 * One row's share of the period, as a percentage.
 *
 * This replaced `formatUsageCost`, which rendered the same number in dollars.
 * Usage is not quantified in money anywhere a customer reads it: what a plan
 * includes may be retuned and what any one request costs depends on the model
 * it routes to, so a dollar figure invites planning against a number that is
 * neither fixed nor ours to guarantee. A share answers what the page is
 * actually for -- which model, which activity, which day is using it up --
 * and stays true however the underlying rate moves.
 */
export function formatUsageShare(
  value: number | null | undefined,
  total: number | null | undefined,
): string {
  if (value == null || !Number.isFinite(value)) return "Unavailable";
  if (!total || !Number.isFinite(total) || total <= 0) return "—";
  const share = (value / total) * 100;
  if (share > 0 && share < 0.1) return "<0.1%";
  return `${share.toFixed(share < 10 ? 1 : 0)}%`;
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
      return "Not yet accounted";
    default:
      return record.cost_usd == null ? "Not yet accounted" : "Recorded";
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
