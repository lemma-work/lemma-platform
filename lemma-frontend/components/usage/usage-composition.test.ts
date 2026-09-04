/** The arithmetic behind the token bar, which is the part that can be wrong.
 *
 * `input_tokens` is the inclusive parent: the cache buckets are subsets of it,
 * never additions. Reading them as siblings and summing all three double-counts
 * every cached token — and draws a bar wider than the total it sits against,
 * which is how the mistake announces itself.
 */

import { describe, expect, it } from 'vitest';

import { byoKeyCost, costSourceLabel, hasComposition, usageComposition } from './usage-composition';

const tokensOf = (summary: Parameters<typeof usageComposition>[0]) =>
    Object.fromEntries(usageComposition(summary).segments.map((s) => [s.key, s.tokens]));

describe('usageComposition', () => {
    it('treats the cache buckets as parts of input, not additions to it', () => {
        const composition = usageComposition({
            total_input_tokens: 1000,
            total_output_tokens: 200,
            total_cached_input_tokens: 600,
            total_cache_write_tokens: 100,
        });

        expect(tokensOf({
            total_input_tokens: 1000,
            total_output_tokens: 200,
            total_cached_input_tokens: 600,
            total_cache_write_tokens: 100,
        })).toEqual({ uncached: 300, cached: 600, cacheWrite: 100, output: 200 });
        // The bar is drawn against this, so the segments must exactly fill it.
        expect(composition.total).toBe(1200);
        expect(composition.segments.reduce((sum, s) => sum + s.tokens, 0)).toBe(1200);
    });

    it('never lets the uncached remainder go negative', () => {
        // A provider reporting more cached tokens than input is either wrong or
        // using a convention we do not know. The backend clamps identically
        // before pricing; the bar must not disagree with the bill.
        const composition = usageComposition({
            total_input_tokens: 100,
            total_output_tokens: 0,
            total_cached_input_tokens: 500,
            total_cache_write_tokens: 500,
        });

        expect(composition.segments.every((segment) => segment.tokens >= 0)).toBe(true);
        expect(composition.segments.reduce((sum, s) => sum + s.tokens, 0)).toBe(100);
    });

    it('measures the cached share against input, not against the total', () => {
        // Dividing by the total would shrink the figure by however much the
        // model wrote back, which has nothing to do with caching.
        const composition = usageComposition({
            total_input_tokens: 1000,
            total_output_tokens: 1000,
            total_cached_input_tokens: 500,
            total_cache_write_tokens: 0,
        });

        expect(composition.cachedShareOfInput).toBe(0.5);
    });

    it('divides by nothing when there is nothing', () => {
        const composition = usageComposition(undefined);

        expect(hasComposition(composition)).toBe(false);
        expect(composition.segments.every((segment) => segment.share === 0)).toBe(true);
        expect(composition.cachedShareOfInput).toBe(0);
    });

    it('ignores figures that are absent, negative or not numbers', () => {
        const composition = usageComposition({
            total_input_tokens: 500,
            total_output_tokens: null,
            total_cached_input_tokens: -20,
            total_cache_write_tokens: undefined,
        });

        expect(tokensOf({
            total_input_tokens: 500,
            total_output_tokens: null,
            total_cached_input_tokens: -20,
            total_cache_write_tokens: undefined,
        })).toEqual({ uncached: 500, cached: 0, cacheWrite: 0, output: 0 });
        expect(composition.total).toBe(500);
    });

    it('gives every segment its own colour, so the bar and the legend agree', () => {
        const colors = usageComposition({ total_input_tokens: 1 }).segments.map((s) => s.colorVar);

        expect(new Set(colors).size).toBe(colors.length);
    });
});

describe('byoKeyCost', () => {
    it('reports only the part of the bill this deployment did not pay for', () => {
        expect(byoKeyCost({ system_cost_usd: 1.0, total_cost_usd: 4.5 })).toBeCloseTo(3.5);
    });

    it('says nothing when every key is the deployment’s own', () => {
        // Otherwise a single-scope deployment carries a row that always reads
        // zero, which teaches people to stop looking at the panel.
        expect(byoKeyCost({ system_cost_usd: 2.0, total_cost_usd: 2.0 })).toBeNull();
    });

    it('does not mistake floating-point noise for somebody else’s key', () => {
        expect(byoKeyCost({ system_cost_usd: 0.1 + 0.2, total_cost_usd: 0.3 })).toBeNull();
    });

    it('says nothing rather than zero when the figures have not arrived', () => {
        expect(byoKeyCost(undefined)).toBeNull();
        expect(byoKeyCost({ system_cost_usd: 1.0 })).toBeNull();
    });
});

describe('costSourceLabel', () => {
    it('leaves a configured rate unremarkable and flags an estimate', () => {
        // A badge on every row is a badge nobody reads, so the common case
        // carries the label the caller then declines to render.
        expect(costSourceLabel('REGISTERED')?.label).toBe('Rated');
        expect(costSourceLabel('ESTIMATED')?.label).toBe('Estimated');
        expect(costSourceLabel('UNKNOWN')?.label).toBe('Unpriced');
    });

    it('says an unpriced row is unknown rather than free', () => {
        expect(costSourceLabel('UNKNOWN')?.title).toMatch(/unknown rather than zero/);
    });

    it('answers nothing for a value it does not recognise', () => {
        expect(costSourceLabel(undefined)).toBeNull();
        expect(costSourceLabel('something-new')).toBeNull();
    });
});
