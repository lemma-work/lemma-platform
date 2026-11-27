"use client";

import { writeView, type View } from "./address";

/** How a page change moves (kit.css, "Moving").
 *
 *  The page in the main area does not move: the new one fades in over the
 *  old, in place. A page that slid or rose in read as the whole screen
 *  jumping, on every click. What was clicked carries over when the new
 *  page has it too: a Lemma's face in the sidebar grows into its face on
 *  its page, a section's heading becomes its page's title. The sidebar
 *  never moves. It is the browser's view transitions; a browser without
 *  them, a hidden window or reduced motion just changes the page. */

function podOf(view: View): string | null {
    if (view.name === "chats") return view.open.kind === "none" ? null : view.open.podId;
    return "podId" in view && typeof view.podId === "string" ? view.podId : null;
}

/** Whether two views are one page, somewhere else on it: an app moving
 *  inside itself, another section of a Lemma's tab, History narrowed to one
 *  Lemma, another part of Settings or of a Lemma's machine, or the page that
 *  is open already. A change between them is a change on the page, not a
 *  change of page, and nothing moves for it. */
export function samePage(from: View, to: View): boolean {
    if (writeView(from) === writeView(to)) return true;
    if (from.name !== to.name) return false;
    switch (from.name) {
        case "app":
            return to.name === "app" && from.podId === to.podId && from.app === to.app;
        case "outcome":
            return to.name === "outcome" && from.podId === to.podId && from.tab === to.tab;
        case "computer":
            return to.name === "computer" && from.podId === to.podId;
        case "history":
        case "settings":
            return true;
        default:
            return false;
    }
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

/** The latest change asked for. A page change waits a frame for its move to
 *  start, while a change on the same page happens at once; so one asked for
 *  later can land first, and the earlier one, still waiting, makes nothing
 *  when its turn comes. The last change asked for is where the reader is. */
let latest = 0;

/** Make the change `update` makes -- one that resolves once the new page is
 *  on screen -- going from `from` to `to`: the new page fading in over the
 *  old, and what was clicked carried across. A change on the same page just
 *  happens. A move that has not started in 400ms is skipped, so a change is
 *  never held up; one cut short by the next is fine, since the page is
 *  already right. */
export function move(from: View, to: View, update: () => Promise<void>): void {
    const asked = ++latest;
    if (samePage(from, to) || !canMove()) {
        void update();
        return;
    }
    const hero = heroOf(from, to);
    const source = hero ? document.querySelector<HTMLElement>(hero.from) : null;
    if (source) source.style.viewTransitionName = "ox-hero";
    let target: HTMLElement | null = null;
    let started = false;
    const start = (document as Document & { startViewTransition: StartTransition }).startViewTransition.bind(document);
    const transition = start(async () => {
        started = true;
        if (source) source.style.viewTransitionName = "";
        if (asked !== latest) return;
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
    });
}
