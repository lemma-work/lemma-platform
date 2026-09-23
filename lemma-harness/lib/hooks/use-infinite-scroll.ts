import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';

interface UseInfiniteScrollOptions {
    /** Whether there is another page to fetch. */
    hasMore: boolean;
    /** True while a page (initial or subsequent) is loading. Pauses auto-loading. */
    isLoading: boolean;
    /** Called when the sentinel scrolls into view and another page should load. */
    onLoadMore: () => void;
    /** Scroll container to observe within. Falls back to the viewport when omitted. */
    rootRef?: RefObject<HTMLElement | null>;
    /** How early to trigger before the sentinel is fully visible. */
    rootMargin?: string;
}

/**
 * Returns a callback ref to attach to a sentinel element at the end of a
 * scrollable list. When the sentinel scrolls into view (and there is more to
 * load), `onLoadMore` fires. Used to give history lists "scroll to load more"
 * behaviour without each caller re-implementing IntersectionObserver wiring.
 *
 * A callback ref (rather than a plain ref) is used so the observer is wired up
 * the moment the sentinel mounts — important when the list lives inside a tab
 * that mounts lazily, where the data may have finished loading before the
 * sentinel ever existed.
 */
export function useInfiniteScroll({
    hasMore,
    isLoading,
    onLoadMore,
    rootRef,
    rootMargin = '240px',
}: UseInfiniteScrollOptions) {
    const [sentinel, setSentinel] = useState<HTMLDivElement | null>(null);
    const onLoadMoreRef = useRef(onLoadMore);

    useEffect(() => {
        onLoadMoreRef.current = onLoadMore;
    });

    useEffect(() => {
        if (!sentinel || !hasMore || isLoading) return;

        const observer = new IntersectionObserver(
            (entries) => {
                if (entries.some((entry) => entry.isIntersecting)) {
                    onLoadMoreRef.current();
                }
            },
            { root: rootRef?.current ?? null, rootMargin },
        );
        observer.observe(sentinel);
        return () => observer.disconnect();
    }, [sentinel, hasMore, isLoading, rootRef, rootMargin]);

    return useCallback((node: HTMLDivElement | null) => {
        setSentinel(node);
    }, []);
}
