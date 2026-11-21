"use client";

/* Which plugin a click elsewhere meant, carried to the Add an app page.
   The frame's addresses cannot carry it, so it is left for one read:
   the Apps page's Ready-made "Add" points at its plugin, and the Add page
   scrolls to that card and lights it once. Storage can be refused (a
   private window); then the Add page simply opens at its top. */

const KEY = "lemma:add-point";

/** Leave `slug` for the Add page to find. */
export function pointAt(slug: string): void {
    try {
        window.sessionStorage.setItem(KEY, slug);
    } catch {
        /* no storage: the page opens at its top */
    }
}

/** The plugin pointed at, once: reading it clears it. */
export function takePointed(): string | null {
    try {
        const slug = window.sessionStorage.getItem(KEY);
        window.sessionStorage.removeItem(KEY);
        return slug;
    } catch {
        return null;
    }
}
