/** What a token total is made of, and what each part cost.
 *
 * `input_tokens` is the inclusive parent count every provider reports: the two
 * cache buckets are *subsets* of it, never additions. Reading them as siblings
 * and adding all three is the mistake this module exists to make impossible —
 * it double-counts every cached token and produces a bar wider than the total
 * it is drawn against.
 *
 * The split is what makes a cost explicable. Cached input bills at a fraction
 * of the full rate, so two windows of identical token count can differ tenfold
 * in spend with nothing on screen to say why. A total alone cannot answer that
 * question; these four segments can.
 *
 * Pure, and separated from the panel that draws it, because the arithmetic is
 * the part worth testing and the SVG around it is not.
 */

/** One band of the bar, and one row of the legend. */
export interface UsageSegment {
    key: 'uncached' | 'cached' | 'cacheWrite' | 'output';
    label: string;
    tokens: number;
    /** Share of `total`, 0–1. Zero when there is nothing to divide by. */
    share: number;
    /** A `--chart-N` custom property, so light and dark both work. */
    colorVar: string;
    /** Why this band is priced the way it is, for a tooltip or a caption. */
    note: string;
}

export interface UsageComposition {
    segments: UsageSegment[];
    total: number;
    inputTokens: number;
    outputTokens: number;
    /** Cached reads as a share of *input* — not of the total, which would
     *  shrink the figure by however much the model wrote back. */
    cachedShareOfInput: number;
}

interface CompositionInput {
    total_input_tokens?: number | null;
    total_output_tokens?: number | null;
    total_cached_input_tokens?: number | null;
    total_cache_write_tokens?: number | null;
}

function whole(value: number | null | undefined): number {
    return typeof value === 'number' && Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}

/** The four buckets, clamped so they always sum to the total they are drawn against.
 *
 * A provider that reports more cached tokens than input tokens is either wrong
 * or using a convention we do not know; either way the uncached remainder must
 * not go negative, so the cache buckets are clamped inside the parent exactly
 * as the backend's `UsageTokens.normalized` clamps them before pricing.
 */
export function usageComposition(summary?: CompositionInput | null): UsageComposition {
    const inputTokens = whole(summary?.total_input_tokens);
    const outputTokens = whole(summary?.total_output_tokens);
    const cached = Math.min(whole(summary?.total_cached_input_tokens), inputTokens);
    const cacheWrite = Math.min(whole(summary?.total_cache_write_tokens), inputTokens - cached);
    const uncached = Math.max(0, inputTokens - cached - cacheWrite);
    const total = inputTokens + outputTokens;

    const share = (tokens: number) => (total > 0 ? tokens / total : 0);

    const segments: UsageSegment[] = [
        {
            key: 'uncached',
            label: 'Uncached input',
            tokens: uncached,
            share: share(uncached),
            colorVar: '--chart-1',
            note: 'Billed at the full input rate.',
        },
        {
            key: 'cached',
            label: 'Cached input',
            tokens: cached,
            share: share(cached),
            colorVar: '--chart-2',
            note: 'Read back from the provider’s cache, at a fraction of the input rate.',
        },
        {
            key: 'cacheWrite',
            label: 'Cache write',
            tokens: cacheWrite,
            share: share(cacheWrite),
            colorVar: '--chart-3',
            note: 'Written into the cache. Some providers charge a premium for this; others nothing.',
        },
        {
            key: 'output',
            label: 'Output',
            tokens: outputTokens,
            share: share(outputTokens),
            colorVar: '--chart-4',
            note: 'What the model wrote. Priced separately, and usually highest.',
        },
    ];

    return {
        segments,
        total,
        inputTokens,
        outputTokens,
        cachedShareOfInput: inputTokens > 0 ? cached / inputTokens : 0,
    };
}

/** Whether there is anything to draw. An all-zero bar is worse than an empty state. */
export function hasComposition(composition: UsageComposition): boolean {
    return composition.total > 0;
}

/** Bring-your-own-key spend: the part of the bill that is not Lemma's to charge.
 *
 * `system_cost_usd` is what a plan limit measures — spend on this deployment's
 * credentials. `total_cost_usd` additionally includes runtime profiles somebody
 * added with their own key, which bill their provider directly. Showing only
 * the difference, and only when there is one, keeps a single-scope deployment
 * from carrying a row that always reads zero.
 */
export function byoKeyCost(summary?: {
    system_cost_usd?: number | null;
    total_cost_usd?: number | null;
} | null): number | null {
    const system = summary?.system_cost_usd;
    const total = summary?.total_cost_usd;
    if (typeof system !== 'number' || typeof total !== 'number') return null;
    const difference = total - system;
    // A hair above zero is floating-point noise from summing many small rows,
    // not somebody's own key.
    return difference > 0.000001 ? difference : null;
}

/** How a cost was arrived at, in words a person can act on. */
export function costSourceLabel(source?: string | null): { label: string; title: string } | null {
    switch ((source || '').toUpperCase()) {
        case 'REGISTERED':
            return {
                label: 'Rated',
                title: 'Priced from a rate this deployment configured.',
            };
        case 'ESTIMATED':
            return {
                label: 'Estimated',
                title: 'Priced from a public dataset, because no rate is configured for this model. Worth checking before anybody is invoiced for it.',
            };
        case 'UNKNOWN':
            return {
                label: 'Unpriced',
                title: 'Nothing could price this model, so the cost is unknown rather than zero. The run was still recorded.',
            };
        default:
            return null;
    }
}
