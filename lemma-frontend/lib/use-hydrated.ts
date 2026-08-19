'use client';

import { useSyncExternalStore } from 'react';

/** Hydration happens once and never again, so there is nothing to subscribe to. */
const noopSubscribe = () => () => {};
const alwaysTrue = () => true;
const alwaysFalse = () => false;

/**
 * False on the server and through the hydrating render, true from the next one.
 *
 * When a component's answer lives on the device — localStorage, `window.__ENV`,
 * a media query — the server does not have it and cannot guess it. The tempting
 * move is to let the server render the "nothing known yet" case, but that case
 * is usually the loud one: an unanswered consent prompt, an un-dismissed
 * primer. The server then bakes it into the HTML of every document, and
 * hydration takes it straight back out. To the person looking at the screen
 * that is a flash on every hard load and nothing at all on client-side
 * navigation — which is exactly the shape that makes it hard to believe.
 *
 * Gating on this collapses the gap: the first client render matches the server
 * exactly, because both render nothing, and the real answer arrives in the
 * re-render immediately after.
 */
export function useHydrated(): boolean {
    return useSyncExternalStore(noopSubscribe, alwaysTrue, alwaysFalse);
}
