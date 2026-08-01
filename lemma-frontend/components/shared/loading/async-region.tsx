'use client';

import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';
import { useLoadingGate, type LoadingGateOptions } from './use-loading-gate';

export type AsyncRegionProps = {
    /** First load — there is no data behind this yet. */
    isLoading: boolean;
    /** Settled, and there is nothing to show. Ignored while `isLoading`. */
    isEmpty?: boolean;
    /**
     * A refresh over data we already have — a key change, a background refetch.
     * The old content stays and dims; it never falls back to the skeleton,
     * because replacing real content with a placeholder is a downgrade.
     */
    isRefreshing?: boolean;
    /** Measured off the settled content, not drawn by eye. */
    skeleton: ReactNode;
    empty?: ReactNode;
    children: ReactNode;
    /**
     * The floor the box holds in every state — pass a `min-h-*` class here when
     * the skeleton and the settled content can disagree. The region should never
     * collapse and re-expand around a load.
     */
    label?: string;
    className?: string;
    gate?: LoadingGateOptions;
};

/**
 * One region, three fills.
 *
 * The rule this enforces is that loading, empty, and loaded are things that
 * happen *inside* a box whose size, borders, and grid were decided by the
 * settled layout — not three different boxes that replace one another. Screens
 * used to answer "what shows while this loads" on their own, and every answer
 * had a different height, so a single page load was two or three layout
 * replacements instead of one paint.
 *
 * Where the surrounding layout is delicate — a flex column that must stay
 * `min-h-0`, a grid whose children are addressed directly — use
 * {@link useLoadingGate} instead and keep the branch inline. This component
 * adds one wrapper element, and that is the wrong trade in those places.
 */
export function AsyncRegion({
    isLoading,
    isEmpty = false,
    isRefreshing = false,
    skeleton,
    empty,
    children,
    label = 'Loading',
    className,
    gate,
}: AsyncRegionProps) {
    const showSkeleton = useLoadingGate(isLoading, gate);

    // While the gate is shut the region is deliberately blank: the box is
    // already the right size, and a placeholder that lives 80ms is a flicker.
    const state = isLoading
        ? (showSkeleton ? 'loading' : 'settling')
        : isEmpty && empty
            ? 'empty'
            : 'ready';

    const fill = state === 'loading'
        ? skeleton
        : state === 'settling'
            ? null
            : state === 'empty'
                ? empty
                : children;

    return (
        <div
            className={cn('lemma-async-region', className)}
            data-state={state}
            data-refreshing={isRefreshing && state === 'ready' ? 'true' : undefined}
            role={isLoading ? 'status' : undefined}
            aria-label={isLoading ? label : undefined}
            aria-busy={isLoading || isRefreshing || undefined}
        >
            {fill}
        </div>
    );
}
