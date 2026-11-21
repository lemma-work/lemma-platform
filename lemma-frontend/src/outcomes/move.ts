"use client";

import type { View } from "./model";

/** How a page change moves, by where it goes (kit.css, "Moving").
 *
 *  Across -- from one place in the sidebar to another: the new page settles
 *  in from 6px down, and what was clicked carries over when the new page has
 *  it too (a Lemma's face in the sidebar grows into its face on its page).
 *  Deeper -- into something on the page: it comes in from the right, and
 *  the heading clicked becomes its title. Back -- the same, the other way.
 *  The sidebar never moves. It is the browser's view transitions; a browser
 *  without them, a hidden window or reduced motion just changes the page. */
export type Direction = "across" | "deeper" | "back";

/** How far into a Lemma a view is: the places in the sidebar are 0, a
 *  Lemma's page and its apps and chats 1, its own pages 2, a thing on one
 *  of them 3. */
export function depthOf(view: View): number {
    switch (view.name) {
        case "outcome":
            return view.tab === "home" ? 1 : 2;
        case "app":
        case "chats":
            return 1;
        case "workflow":
        case "run":
        case "agent":
        case "table":
        case "record":
        case "file":
        case "computer":
            return 3;
        default:
            return 0;
    }
}

function podOf(view: View): string | null {
    if (view.name === "chats") return view.open.kind === "none" ? null : view.open.podId;
    return "podId" in view && typeof view.podId === "string" ? view.podId : null;
}

/** Deeper or back within one Lemma; across anywhere else. */
export function directionOf(from: View, to: View): Direction {
    const here = podOf(from);
    if (!here || here !== podOf(to)) return "across";
    const a = depthOf(from);
    const b = depthOf(to);
    if (b > a && a >= 1) return "deeper";
    if (b < a && b >= 1) return "back";
    return "across";
}

/** What carries from one page to the next, as the `data-hero` both ends
 *  wear: a Lemma's face, or one of its sections' headings and its page's
 *  title. Null when nothing does. */
export function heroOf(from: View, to: View): { from: string; to: string } | null {
    if (to.name === "outcome" && to.tab === "home" && podOf(from) !== to.podId) {
        return { from: `.ox-side [data-hero="face:${to.podId}"]`, to: `.ox-main [data-hero="face:${to.podId}"]` };
    }
    if (from.name === "outcome" && to.name === "outcome" && from.podId === to.podId) {
        if (from.tab === "home" && to.tab !== "home") return { from: `.ox-lp [data-hero="title:${to.tab}"]`, to: `.ox-subhead [data-hero="title:${to.tab}"]` };
        if (to.tab === "home" && from.tab !== "home") return { from: `.ox-subhead [data-hero="title:${from.tab}"]`, to: `.ox-lp [data-hero="title:${from.tab}"]` };
    }
    return null;
}

type Transition = { ready: Promise<void>; finished: Promise<void>; skipTransition: () => void };
type StartTransition = (update: () => Promise<void> | void) => Transition;

function canMove(): boolean {
    if (typeof document === "undefined") return false;
    const start = (document as Document & { startViewTransition?: StartTransition }).startViewTransition;
    return typeof start === "function"
        && document.visibilityState === "visible"
        && !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Make the change `update` makes -- one that resolves once the new page is
 *  on screen -- moving the way `direction` says, carrying `hero` across. A
 *  move that has not started in 400ms is skipped, so a change is never held
 *  up; one cut short by the next is fine, since the page is already right. */
export function move(direction: Direction, hero: { from: string; to: string } | null, update: () => Promise<void>): void {
    if (!canMove()) {
        void update();
        return;
    }
    const root = document.documentElement;
    root.dataset.move = direction;
    const source = hero ? document.querySelector<HTMLElement>(hero.from) : null;
    if (source) source.style.viewTransitionName = "ox-hero";
    let target: HTMLElement | null = null;
    let started = false;
    const start = (document as Document & { startViewTransition: StartTransition }).startViewTransition.bind(document);
    const transition = start(async () => {
        started = true;
        if (source) source.style.viewTransitionName = "";
        await update();
        target = hero && source ? document.querySelector<HTMLElement>(hero.to) : null;
        if (target) target.style.viewTransitionName = "ox-hero";
    });
    transition.ready.catch(() => undefined);
    window.setTimeout(() => {
        if (!started) transition.skipTransition();
    }, 400);
    transition.finished.catch(() => undefined).finally(() => {
        if (source) source.style.viewTransitionName = "";
        if (target) target.style.viewTransitionName = "";
        delete root.dataset.move;
    });
}
