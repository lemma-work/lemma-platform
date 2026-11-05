"use client";

import { useEffect, useRef, useState, useSyncExternalStore, type RefObject } from "react";

/** How far the reader has read a channel, by the lines they have actually
 *  seen -- not by the server's clock, and not by the page being open.
 *
 *  A line counts as seen once most of it has been on screen while the tab is
 *  in front. Reading back through history sees only old lines, so the read
 *  position never jumps past what is below the fold; at the bottom, with the
 *  newest top-level line in view, the channel is read through its newest line
 *  of all (`lastSeq`), which may be a thread reply the timeline counts rather
 *  than shows -- but never through a reply naming the reader that they have
 *  not seen (`heldBack`): a mention stays unread until its thread is opened.
 *  And nothing is marked while the page is behind another tab. */

export interface ReadInputs {
    /** The highest `seq` seen on screen, or 0. */
    seenMax: number;
    /** The reader is at the bottom of the timeline. */
    atEnd: boolean;
    /** The newest top-level line the timeline holds. */
    newestShown: number;
    /** The newest line in the channel, replies included. */
    lastSeq: number;
    /** What the server last said the reader had read. */
    readSeq: number;
    /** What this view last asked to mark, so one line is marked once. */
    marked: number;
    /** The oldest unread line naming the reader that is in a thread they
     *  have not opened, or null: the jump to `lastSeq` stops short of it. */
    heldBack?: number | null;
}

/** The `seq` to mark read now, or null when there is nothing new to mark. */
export function readTarget(inputs: ReadInputs): number | null {
    let target = inputs.seenMax;
    if (inputs.atEnd && inputs.newestShown > 0 && inputs.seenMax >= inputs.newestShown) {
        const held = inputs.heldBack ?? null;
        target = Math.max(target, held !== null && held > 0 ? Math.min(inputs.lastSeq, held - 1) : inputs.lastSeq);
    }
    return target > Math.max(inputs.readSeq, inputs.marked) ? target : null;
}

/** The oldest unread mention of the reader in this channel that sits in a
 *  thread not open now -- what reading the channel to its end must not mark
 *  read on their behalf. */
export function unseenThreadMention(
    mentions: readonly { channelId: string; seq: number; threadRootId: string | null }[],
    channelId: string,
    openThread: string | null,
): number | null {
    let oldest: number | null = null;
    for (const mention of mentions) {
        if (mention.channelId !== channelId || !mention.threadRootId || mention.threadRootId === openThread || mention.seq <= 0) continue;
        if (oldest === null || mention.seq < oldest) oldest = mention.seq;
    }
    return oldest;
}

/** Whether the page is in front: what is on screen behind another tab has
 *  not been read. */
export function usePageShown(): boolean {
    return useSyncExternalStore(
        (notify) => {
            if (typeof document === "undefined") return () => {};
            document.addEventListener("visibilitychange", notify);
            return () => document.removeEventListener("visibilitychange", notify);
        },
        () => typeof document === "undefined" || document.visibilityState !== "hidden",
        () => true,
    );
}

/** The highest `data-seq` of any element under `box` that has been on
 *  screen while the page was visible. Re-observes when `version` changes --
 *  a new line, an older page. */
export function useSeenSeq(box: RefObject<HTMLElement | null>, version: unknown, active: boolean): number {
    const [seen, setSeen] = useState(0);
    const [shown, setShown] = useState(0);
    const pending = useRef(0);
    /* An observer reports a line once, when it crosses the edge; a line that
       sat on screen while the tab was behind another is reported again by
       observing afresh when the tab comes back. */
    useEffect(() => {
        if (typeof document === "undefined") return;
        const back = () => { if (document.visibilityState === "visible") setShown((was) => was + 1); };
        document.addEventListener("visibilitychange", back);
        return () => document.removeEventListener("visibilitychange", back);
    }, []);
    useEffect(() => {
        const root = box.current;
        if (!root || !active || typeof IntersectionObserver === "undefined") return;
        const observer = new IntersectionObserver((entries) => {
            if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
            let best = pending.current;
            for (const entry of entries) {
                /* Most of it, or -- for a line taller than the view -- half the view of it. */
                const enough = entry.intersectionRatio >= 0.6 || entry.intersectionRect.height >= root.clientHeight / 2;
                if (!entry.isIntersecting || !enough) continue;
                const seq = Number((entry.target as HTMLElement).dataset.seq);
                if (Number.isFinite(seq) && seq > best) best = seq;
            }
            if (best > pending.current) {
                pending.current = best;
                setSeen(best);
            }
        }, { root, threshold: [0, 0.6, 1] });
        for (const element of root.querySelectorAll<HTMLElement>("[data-seq]")) observer.observe(element);
        return () => observer.disconnect();
    }, [box, version, active, shown]);
    return seen;
}
