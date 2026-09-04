"use client";

import { EmptyState } from "@/components/shared/empty-state";
import { Layers3 } from "@/components/ui/icons";
import type { UsageSummary } from "@/lib/types";

import {
  byoKeyCost,
  hasComposition,
  usageComposition,
} from "./usage-composition";

/** What the tokens were, so the cost above them can be explained.
 *
 * The panel this replaces showed one number — "Tokens" — and a cost. Those two
 * cannot explain each other: cached input bills at a fraction of the full rate,
 * so two windows of identical token count differ tenfold in spend and the
 * screen said nothing about why. Every part is now named, counted and sized.
 *
 * An inline SVG rather than a charting library: none is installed, and
 * `bundle:budget:ci` is strict against a committed baseline. The bar is drawn
 * from `--chart-N` custom properties so it follows the viewer's theme, and it
 * is `aria-hidden` with the same numbers repeated as real text beneath — a
 * screen reader gets the figures, not a description of a rectangle.
 */
export function UsageTokenBreakdown({ summary }: { summary?: UsageSummary }) {
  const composition = usageComposition(summary);
  const byo = byoKeyCost(summary);

  return (
    <section className="surface-panel p-4">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-[var(--text-primary)]">
            What the tokens were
          </h3>
          <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">
            Input is the whole of what was sent; the cache buckets are parts of
            it, not extras.
          </p>
        </div>
      </div>

      {!hasComposition(composition) ? (
        <EmptyState
          variant="inline"
          icon={<Layers3 className="h-4 w-4" />}
          title="No tokens yet"
          description="The split appears here once this scope has model activity in the selected window."
          className="surface-panel-dashed min-h-[9rem] items-center px-4 py-5"
        />
      ) : (
        <>
          <svg
            viewBox="0 0 100 6"
            preserveAspectRatio="none"
            className="h-3 w-full overflow-hidden rounded-full"
            aria-hidden="true"
            focusable="false"
          >
            {
              composition.segments.reduce<{
                x: number;
                bands: React.ReactNode[];
              }>(
                (acc, segment) => {
                  const width = segment.share * 100;
                  if (width <= 0) return acc;
                  acc.bands.push(
                    <rect
                      key={segment.key}
                      x={acc.x}
                      y={0}
                      width={width}
                      height={6}
                      fill={`var(${segment.colorVar})`}
                    />,
                  );
                  return { x: acc.x + width, bands: acc.bands };
                },
                { x: 0, bands: [] },
              ).bands
            }
          </svg>

          <dl className="mt-4 space-y-2">
            {composition.segments.map((segment) => (
              <div
                key={segment.key}
                className="grid grid-cols-[0.6rem_minmax(0,1fr)_5rem_3rem] items-center gap-2"
                title={segment.note}
              >
                <svg
                  viewBox="0 0 10 10"
                  className="h-2.5 w-2.5"
                  aria-hidden="true"
                  focusable="false"
                >
                  {/* A swatch, not a shape. Drawn rather than
                                        styled because the colour comes from the
                                        same `--chart-N` the band above uses, and
                                        an inline `style` for a non-geometric
                                        property is what the design audit is
                                        there to stop. */}
                  <circle
                    cx={5}
                    cy={5}
                    r={5}
                    fill={`var(${segment.colorVar})`}
                  />
                </svg>
                <dt className="truncate text-xs text-[var(--text-secondary)]">
                  {segment.label}
                </dt>
                <dd className="text-right text-xs font-medium text-[var(--text-primary)]">
                  {formatTokens(segment.tokens)}
                </dd>
                <dd className="text-right text-xs text-[var(--text-tertiary)]">
                  {Math.round(segment.share * 100)}%
                </dd>
              </div>
            ))}
          </dl>

          <p className="mt-4 border-t border-[var(--border-subtle)] pt-3 text-xs leading-5 text-[var(--text-secondary)]">
            {Math.round(composition.cachedShareOfInput * 100)}% of input was
            served from cache, which bills at a fraction of the full rate.
            {byo != null
              ? ` A further ${formatUsd(byo)} was spent on keys this deployment does not own.`
              : ""}
          </p>
        </>
      )}
    </section>
  );
}

function formatTokens(value: number): string {
  return new Intl.NumberFormat(undefined, {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

function formatUsd(value: number): string {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: value < 1 ? 4 : 2,
  }).format(value);
}
