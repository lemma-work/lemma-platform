/** Held while the frame is still blank. Nothing is drawn at this size — the
 *  frame is hidden until it has something to show — so it is a reservation, not
 *  a minimum. */
export const PLACEHOLDER = 180;
/** For content that never reports at all: an external page that does not
 *  implement the protocol still has to be given some height to stand in. */
export const UNREPORTED = 480;

export interface FrameInput {
    /** The `load` event has fired. */
    loaded: boolean;
    /** The height the content asked for, or null if it has not said. */
    reported: number | null;
    /** Loaded, but long enough with no height that none is coming. */
    unreported: boolean;
    /** Long enough with no `load` at all that saying "Loading…" is a lie. */
    stalled: boolean;
    expanded: boolean;
    full: boolean;
    /** The cap on an unexpanded frame, so one page cannot swallow a transcript. */
    ceiling: number;
}

export function frameState(input: FrameInput): { show: "waiting" | "stalled" | "content"; height: number } {
    const { loaded, reported, unreported, stalled, expanded, full, ceiling } = input;
    /* Nothing is coming, so no space is kept for it: a placeholder here would
       put 180px of nothing above the sentence explaining why there is nothing. */
    if (stalled && !loaded) return { show: "stalled", height: 0 };
    const natural = reported ?? (unreported ? UNREPORTED : PLACEHOLDER);
    return {
        show: loaded && (reported !== null || unreported) ? "content" : "waiting",
        height: expanded || full ? natural : Math.min(natural, ceiling),
    };
}
