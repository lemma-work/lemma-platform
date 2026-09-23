'use client';

import { useEffect, useRef, useState } from 'react';

/** Below this, a load is not worth announcing — it will be over before it reads. */
export const LOADING_GATE_DELAY_MS = 120;
/** Once a placeholder is on screen it stays long enough to be seen, not blinked. */
export const LOADING_GATE_MIN_VISIBLE_MS = 400;

export type LoadingGateOptions = {
    delayMs?: number;
    minVisibleMs?: number;
};

/**
 * Decides whether a placeholder should actually be on screen.
 *
 * Two failure modes, one gate. A fast response (cache hit, warm route) would
 * otherwise flash a skeleton for a frame or two — visible as a flicker, never
 * as information. A medium response would show one that disappears before it
 * can be read, which is the same flicker with extra steps. So: nothing appears
 * for the first `delayMs`, and anything that does appear stays `minVisibleMs`.
 *
 * The consequence worth stating plainly — while `isLoading` is true but the
 * gate is still shut, this returns `false` and the caller renders *the empty
 * box*, not the settled content and not a placeholder. That is the point: the
 * box was already the right size.
 */
export function useLoadingGate(isLoading: boolean, options: LoadingGateOptions = {}): boolean {
    const { delayMs = LOADING_GATE_DELAY_MS, minVisibleMs = LOADING_GATE_MIN_VISIBLE_MS } = options;
    const [visible, setVisible] = useState(false);
    const shownAtRef = useRef(0);

    useEffect(() => {
        let timer: ReturnType<typeof setTimeout> | undefined;

        if (isLoading) {
            timer = setTimeout(() => {
                shownAtRef.current = Date.now();
                setVisible(true);
            }, delayMs);
        } else if (shownAtRef.current > 0) {
            // Always via a timer, even at zero delay: setting state straight from
            // an effect body is the pattern this codebase lints against.
            const remaining = Math.max(0, minVisibleMs - (Date.now() - shownAtRef.current));
            timer = setTimeout(() => {
                shownAtRef.current = 0;
                setVisible(false);
            }, remaining);
        }

        return () => {
            if (timer) clearTimeout(timer);
        };
    }, [isLoading, delayMs, minVisibleMs]);

    return visible;
}
