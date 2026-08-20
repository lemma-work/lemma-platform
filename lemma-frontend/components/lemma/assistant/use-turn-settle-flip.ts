"use client";

// The settle, as motion.
//
// When a run ends, the turn reorganizes in one commit: the status pill jumps
// from the frontier (after the newest beat) to the turn's header slot (before
// the beats), the beats shift down, and the timestamp stamp lands. Rendered
// bare, everything snaps at once and the eye loses the pill — the one object
// whose continuity matters, because it is what the reader was watching.
//
// The fix is a FLIP: while the turn is live, every commit records where the
// turn's children sit. On the settle commit, each child that moved starts at
// its old position — an inverted transform, so no reflow happens and the
// transcript's scroll anchoring is untouched — and eases to its new one. The
// pill slides to its header slot; the beats make room for it.

import { useLayoutEffect, useRef } from "react";

const SETTLE_MS = 240;

export function useTurnSettleFlip(armed: boolean, settled: boolean) {
  const turnRef = useRef<HTMLDivElement | null>(null);
  const capturedRef = useRef<{ rects: Map<Element, DOMRect>; pill: DOMRect | null } | null>(null);

  // Record geometry on every live commit. The settle commit is the first one
  // that is not live, and it still needs the positions from the commit before.
  useLayoutEffect(() => {
    const el = turnRef.current;
    if (!el || !armed) return;
    const rects = new Map<Element, DOMRect>();
    let pill: DOMRect | null = null;
    for (const child of Array.from(el.children)) {
      const rect = child.getBoundingClientRect();
      rects.set(child, rect);
      if (child.classList.contains("lchat-status")) pill = rect;
    }
    capturedRef.current = { rects, pill };
  });

  useLayoutEffect(() => {
    if (!settled) return;
    const el = turnRef.current;
    const captured = capturedRef.current;
    capturedRef.current = null;
    if (!el || !captured) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const moved: Array<{ child: HTMLElement; dy: number }> = [];
    for (const child of Array.from(el.children)) {
      // The settled pill is a fresh node — match it to the live pill's last
      // position by its class; everything else matches by identity.
      const oldRect = captured.rects.get(child)
        ?? (child.classList.contains("lchat-status") ? captured.pill : null);
      if (!oldRect) continue;
      const dy = oldRect.top - child.getBoundingClientRect().top;
      if (Math.abs(dy) < 1) continue;
      moved.push({ child: child as HTMLElement, dy });
    }
    if (moved.length === 0) return;

    for (const { child, dy } of moved) {
      child.style.transition = "none";
      child.style.transform = `translateY(${dy}px)`;
      // The pill is the actor crossing the beats on its way up; it rides
      // above them for the flight. (z-index works on flex items directly.)
      if (child.classList.contains("lchat-status")) child.style.zIndex = "2";
    }
    // Commit the inverted positions before playing, in the same frame.
    void el.offsetHeight;
    requestAnimationFrame(() => {
      for (const { child } of moved) {
        child.style.transition = `transform ${SETTLE_MS}ms var(--ease-standard)`;
        child.style.transform = "";
      }
      window.setTimeout(() => {
        for (const { child } of moved) {
          child.style.transition = "";
          child.style.zIndex = "";
        }
      }, SETTLE_MS + 60);
    });
  }, [settled]);

  return turnRef;
}
