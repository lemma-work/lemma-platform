"use client";

// Following the bottom of a transcript.
//
// The rule everything here is built on: *giving up the bottom requires the
// reader.* Distance from the bottom cannot stand in for their intent, because
// this transcript moves constantly on its own. A tool card lands and adds
// ~284px in one commit. A finished run folds its trace away and takes ~2600px
// with it — then springs back. A widget reports its height once its iframe lays
// out. Each of those reads as "suddenly far from the bottom", identical to
// someone scrolling up, so any threshold is either too small (the transcript's
// own growth ends the follow) or too large (the reader scrolls away and gets
// dragged back).
//
// Worse, the most common case is invisible to measurement. When content
// collapses and returns inside a single frame, the browser clamps scrollTop on
// the way through and leaves it there; both scroll events read the same height
// on either side, and ResizeObserver — which reports the box at frame end —
// never fires at all. Comparing heights cannot detect it even in principle.
//
// A person, though, is never invisible: a wheel, a touch, a key, a press on the
// scrollbar. So scrolls this hook did not perform, and that no gesture
// preceded, are treated as the page moving under a still reader, and the bottom
// is retaken rather than surrendered.

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Marks one message row. The reader's place is kept by remembering the row
 * under their eyes, so this has to sit on the rows themselves: anchoring
 * anything that wraps *all* of them cannot work, because a wrapper's top edge
 * does not move when content inside it changes height, and the shift the
 * restore is looking for is always zero.
 *
 * One row per mark, and never a mark inside a mark: the search below relies on
 * these being a flat sequence in document order.
 */
export const TRANSCRIPT_ROW_ATTRIBUTE = "data-transcript-row";

/** How close to the bottom still counts as the bottom. */
const AT_BOTTOM_EPSILON = 40;
/** Scroll offset under which the transcript asks for older messages. */
const NEAR_TOP = 48;
/** How long a gesture keeps counting as the reader driving the scroller. */
const READER_GESTURE_MS = 500;

export interface TranscriptScroll {
  /** Attach to the scrolling element. */
  containerRef: React.RefObject<HTMLDivElement | null>;
  /** Attach to the scrolling element's onScroll. */
  onScroll: () => void;
  /** True while the transcript is following the bottom. Drives the jump control. */
  isFollowing: boolean;
  /** Jump to the newest content and resume following. */
  scrollToBottom: (behavior?: ScrollBehavior) => void;
  /** Run a prepend (older messages) without the viewport moving. */
  preserveAcross: (load: () => Promise<boolean>) => Promise<boolean>;
}

export function useTranscriptScroll({
  activeConversationId,
  onReachTop,
}: {
  activeConversationId: string | null;
  /** Called when the reader scrolls to the top; load older messages here. */
  onReachTop?: () => void;
}): TranscriptScroll {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [isFollowing, setIsFollowing] = useState(true);

  const followRef = useRef(true);
  // The offset our last write asked for. While this is set, scroll events are
  // ours — including every intermediate position of a smooth scroll.
  const pendingTargetRef = useRef<number | null>(null);
  // Where the reader was, so a reflow above them can put it back.
  const anchorRef = useRef<{ node: Element; offset: number } | null>(null);
  const readerGestureAtRef = useRef(0);
  const onReachTopRef = useRef(onReachTop);
  useEffect(() => {
    onReachTopRef.current = onReachTop;
  });

  const setFollowing = useCallback((next: boolean) => {
    followRef.current = next;
    setIsFollowing((current) => (current === next ? current : next));
  }, []);

  const write = useCallback((top: number, behavior: ScrollBehavior = "instant") => {
    const el = containerRef.current;
    if (!el) return;
    const target = Math.max(0, Math.min(top, el.scrollHeight - el.clientHeight));

    // A write that does not move anything produces no scroll event, so claiming
    // it would leave the claim standing forever — and every later scroll, the
    // reader's included, would be dismissed as ours. Following the bottom while
    // already at the bottom is exactly this case, so it happens constantly.
    if (Math.abs(el.scrollTop - target) <= 1) {
      pendingTargetRef.current = null;
      return;
    }

    pendingTargetRef.current = target;
    if (behavior === "smooth") {
      el.scrollTo({ top: target, behavior });
    } else {
      // Assigning scrollTop is instant and synchronous, and it cancels a smooth
      // scroll still running. scrollTo({behavior:"instant"}) does not reliably
      // do the second thing.
      el.scrollTop = target;
    }
  }, []);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "instant") => {
    const el = containerRef.current;
    if (!el) return;
    setFollowing(true);
    write(el.scrollHeight, behavior);
  }, [setFollowing, write]);

  const offsetOf = (el: HTMLElement, node: Element) =>
    node.getBoundingClientRect().top - el.getBoundingClientRect().top;

  /** The topmost row still on screen — what the reader is looking at. */
  const captureAnchor = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const rows = el.querySelectorAll(`[${TRANSCRIPT_ROW_ATTRIBUTE}]`);
    const top = el.getBoundingClientRect().top;

    // The rows are one column in document order, so "already scrolled past"
    // only flips once on the way down: the topmost row still on screen is a
    // dozen measurements away rather than one per row. Worth the search — this
    // runs on every scroll event a reader generates, over a transcript that can
    // be hundreds of rows long, and each measurement is a layout read.
    let low = 0;
    let high = rows.length - 1;
    let anchor: { node: Element; offset: number } | null = null;
    while (low <= high) {
      const mid = (low + high) >> 1;
      const box = rows[mid].getBoundingClientRect();
      if (box.bottom > top) {
        anchor = { node: rows[mid], offset: box.top - top };
        high = mid - 1;
      } else {
        low = mid + 1;
      }
    }
    anchorRef.current = anchor;
  }, []);

  const onScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;

    // Ours. Clear the claim once it lands; intermediate positions of a smooth
    // scroll keep it, which is what stops an animation from unfollowing itself.
    const pending = pendingTargetRef.current;
    if (pending !== null) {
      if (Math.abs(el.scrollTop - pending) <= 1) pendingTargetRef.current = null;
      return;
    }

    // Not ours, and no gesture behind it: the page moved, the reader did not.
    const readerIsDriving = Date.now() - readerGestureAtRef.current < READER_GESTURE_MS;
    if (!readerIsDriving && followRef.current) {
      write(el.scrollHeight);
      return;
    }

    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    setFollowing(distance <= AT_BOTTOM_EPSILON);
    if (!followRef.current) captureAnchor();

    if (el.scrollTop <= NEAR_TOP) onReachTopRef.current?.();
  }, [captureAnchor, setFollowing, write]);

  // Every way a person can drive a scroller. `mousedown` sits alongside
  // `pointerdown` because WebKit does not reliably emit pointer events for the
  // scrollbar itself.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const yieldToReader = () => {
      readerGestureAtRef.current = Date.now();
      // The reader outranks a scroll of ours still in flight; without this,
      // grabbing the transcript mid-animation would be ignored until the
      // animation reached a target they had already rejected.
      pendingTargetRef.current = null;
    };
    const events = ["wheel", "touchstart", "keydown", "pointerdown", "mousedown"] as const;
    for (const event of events) el.addEventListener(event, yieldToReader, { passive: true });
    return () => {
      for (const event of events) el.removeEventListener(event, yieldToReader);
    };
  }, []);

  // Content changes size for reasons that have nothing to do with the reader.
  // Following, that means the bottom moved and we go with it. Not following, it
  // means the page moved under them, and their line is put back where it was —
  // Safari has no scroll anchoring, and this container turns Chrome's off with
  // `overflow-anchor: none`.
  //
  // The scroller itself is watched alongside its content, because the bottom
  // also moves when the *window* changes height. The composer below this
  // transcript grows and shrinks constantly — a second typed line, a plan
  // summary, an approval card — and every pixel it takes comes out of this
  // viewport. That shortens the distance to the bottom without changing the
  // content or moving scrollTop, so neither a resize of the content nor a
  // scroll event is emitted: following would silently stop at the bottom and
  // leave the newest messages under the fold.
  useEffect(() => {
    const el = containerRef.current;
    const content = el?.firstElementChild;
    if (!el || !content) return;

    const observer = new ResizeObserver(() => {
      if (followRef.current) {
        write(el.scrollHeight);
        return;
      }
      const anchor = anchorRef.current;
      if (!anchor) return;
      // The row they were on can be unmounted out from under them — a run
      // folding its trace away takes its rows with it. Nothing can be restored
      // to a row that no longer exists, so take a fresh one rather than leaving
      // a dead anchor to fail every reflow from here on.
      if (!anchor.node.isConnected) {
        captureAnchor();
        return;
      }
      const shift = offsetOf(el, anchor.node) - anchor.offset;
      if (Math.abs(shift) < 1) return;
      write(el.scrollTop + shift);
    });
    observer.observe(content);
    observer.observe(el);
    return () => observer.disconnect();
  }, [captureAnchor, write]);

  // A different conversation starts at its newest message.
  useEffect(() => {
    anchorRef.current = null;
    followRef.current = true;
    const frame = requestAnimationFrame(() => {
      setFollowing(true);
      const el = containerRef.current;
      if (el) write(el.scrollHeight);
    });
    return () => cancelAnimationFrame(frame);
  }, [activeConversationId, setFollowing, write]);

  // Prepending older messages must not move the viewport. The row the reader
  // was on is put back where it was, rather than adding back the height the
  // load happened to bring: a run in flight adds its own height while the fetch
  // is in the air, so the delta between "before" and "after" is not the older
  // messages alone. Anchoring also keeps working when the prepended block
  // settles late — an image or a widget that reports its size a few frames
  // afterwards moves the same row again, and the resize observer above corrects
  // it against this same anchor.
  const preserveAcross = useCallback(async (load: () => Promise<boolean>) => {
    captureAnchor();
    const didLoad = await load();
    if (!didLoad) return didLoad;

    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    const el = containerRef.current;
    if (!el) return didLoad;
    if (followRef.current) {
      write(el.scrollHeight);
      return didLoad;
    }
    const anchor = anchorRef.current;
    if (!anchor?.node.isConnected) return didLoad;
    const shift = offsetOf(el, anchor.node) - anchor.offset;
    if (Math.abs(shift) >= 1) write(el.scrollTop + shift);
    return didLoad;
  }, [captureAnchor, write]);

  return { containerRef, onScroll, isFollowing, scrollToBottom, preserveAcross };
}
