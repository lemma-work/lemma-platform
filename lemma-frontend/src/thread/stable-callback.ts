import { useCallback, useRef } from "react";

/** A handler whose identity never changes and which always runs the latest
 *  version of `fn`.
 *
 *  The conversation pane re-renders for every few tokens of a reply, and most
 *  of its handlers close over the SDK session, whose object is new each time.
 *  Handed down as they were, they made every memoised child — each turn, the
 *  composer — look changed on every token, so none of them could skip a
 *  render. For handlers only: called during render, this would read whatever
 *  the last render left behind. */
export function useStableCallback<A extends unknown[], R>(fn: (...args: A) => R): (...args: A) => R {
    const latest = useRef(fn);
    latest.current = fn;
    return useCallback((...args: A) => latest.current(...args), []);
}
