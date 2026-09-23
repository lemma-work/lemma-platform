'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

export interface HorizontalOverflow {
    /** True while content extends past either edge. */
    overflowing: boolean;
    /** True while there is content hidden off the left edge. */
    hiddenLeft: boolean;
    /** True while there is content hidden off the right edge. */
    hiddenRight: boolean;
}

/** Sub-pixel layout means `scrollLeft` rarely lands exactly on the bound. */
const EDGE_EPSILON = 1;

/**
 * Makes a horizontally scrolling strip usable with a mouse, and visible when it
 * has more to show.
 *
 * A strip with `overflow-x: auto` and a hidden scrollbar is reachable by
 * trackpad and by keyboard (focus scrolls its target into view) and by nothing
 * else. On a plain wheel mouse the content past the edge is not awkward to get
 * to — it cannot be got to at all, and hiding the scrollbar removes the last
 * clue that it exists. Two things fix that: the wheel scrolls the strip, and
 * the edges say when something is behind them.
 *
 * The wheel listener is deliberately non-passive, because translating a
 * vertical wheel into horizontal scroll means calling `preventDefault` — and
 * only when this strip can actually take the movement. At either end the event
 * is left alone so the page scrolls underneath instead of the wheel dying in a
 * strip that has nowhere left to go.
 */
export function useHorizontalOverflow<T extends HTMLElement>() {
    const ref = useRef<T | null>(null);
    const [overflow, setOverflow] = useState<HorizontalOverflow>({
        overflowing: false,
        hiddenLeft: false,
        hiddenRight: false,
    });

    const measure = useCallback(() => {
        const node = ref.current;
        if (!node) return;

        const maxScroll = node.scrollWidth - node.clientWidth;
        setOverflow((previous) => {
            const next: HorizontalOverflow = {
                overflowing: maxScroll > EDGE_EPSILON,
                hiddenLeft: node.scrollLeft > EDGE_EPSILON,
                hiddenRight: node.scrollLeft < maxScroll - EDGE_EPSILON,
            };

            // Scroll fires continuously; re-rendering on every frame for a value
            // that did not move is the expensive half of an edge fade.
            if (
                previous.overflowing === next.overflowing
                && previous.hiddenLeft === next.hiddenLeft
                && previous.hiddenRight === next.hiddenRight
            ) {
                return previous;
            }
            return next;
        });
    }, []);

    useEffect(() => {
        const node = ref.current;
        if (!node) return;

        const onWheel = (event: WheelEvent) => {
            // A trackpad already sends horizontal intent; taking it over would
            // double the movement and fight the gesture.
            if (Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
            if (event.deltaY === 0) return;

            const maxScroll = node.scrollWidth - node.clientWidth;
            if (maxScroll <= EDGE_EPSILON) return;

            const target = node.scrollLeft + event.deltaY;
            const clamped = Math.max(0, Math.min(maxScroll, target));
            if (clamped === node.scrollLeft) return;

            event.preventDefault();
            node.scrollLeft = clamped;
        };

        node.addEventListener('wheel', onWheel, { passive: false });
        node.addEventListener('scroll', measure, { passive: true });

        const observer = new ResizeObserver(measure);
        observer.observe(node);
        // Tables arrive after the first paint, so the strip's own width changes
        // without the container's — watch the content, not just the box.
        Array.from(node.children).forEach((child) => observer.observe(child));

        measure();

        return () => {
            node.removeEventListener('wheel', onWheel);
            node.removeEventListener('scroll', measure);
            observer.disconnect();
        };
    }, [measure]);

    return { ref, ...overflow, remeasure: measure };
}
