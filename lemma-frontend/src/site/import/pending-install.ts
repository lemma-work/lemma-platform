import { key, type KeyValueStore } from "@/session/storage";

/** An install somebody started and has not finished, kept past the tab.
 *
 *  The install page sends a signed-out visitor through sign-in and, when they
 *  have no organization, through making one. Each of those can end somewhere
 *  other than the install — a sign-in finished in another tab, a link followed
 *  by hand — and then the template is simply gone. So the page writes down
 *  what was being installed, and the workspace offers it back to whoever
 *  lands there without it.
 *
 *  `localStorage` rather than the tab's own storage, because the tab is what
 *  gets lost. Only a public page's path and title are kept: nothing about the
 *  account, so a shared browser hands the next person a link at most. */
const PENDING = "install-pending";

/** Long enough to cover a sign-up and a coffee, short enough that a stray
 *  install from last week is not offered to somebody who has moved on. */
export const PENDING_MS = 24 * 60 * 60 * 1000;

const INSTALL_PATH = /^\/import\/github\/[^/?#]+\/[^/?#]+\/?(?:\?[^#]*)?$/;

export interface PendingInstall {
    path: string;
    title: string;
}

export function rememberInstall(store: KeyValueStore, install: PendingInstall, now = Date.now()): void {
    if (!INSTALL_PATH.test(install.path)) return;
    try {
        store.setItem(key(PENDING), JSON.stringify({ ...install, at: now }));
    } catch {
        /* Storage refused: the install is only lost the way it was before. */
    }
}

export function pendingInstall(store: KeyValueStore, now = Date.now()): PendingInstall | null {
    try {
        const raw = store.getItem(key(PENDING));
        if (!raw) return null;
        const held = JSON.parse(raw) as Partial<PendingInstall & { at: number }>;
        if (typeof held.path !== "string" || !INSTALL_PATH.test(held.path)) return null;
        if (typeof held.title !== "string" || typeof held.at !== "number") return null;
        if (now - held.at > PENDING_MS || held.at > now) return null;
        return { path: held.path, title: held.title };
    } catch {
        return null;
    }
}

export function forgetInstall(store: KeyValueStore): void {
    try {
        store.removeItem(key(PENDING));
    } catch {
        /* nothing held */
    }
}

/** The visit an install opens.
 *
 *  Somebody who just installed a template came to use it. The profile step
 *  and the tour are Lemma introducing itself, and on that visit they stand
 *  between the person and what they installed. Held in the tab's own storage:
 *  this visit stays quiet, and the next one is offered both as usual. */
const ARRIVAL = "install-arrival";

export function markInstallArrival(store: KeyValueStore): void {
    try {
        store.setItem(key(ARRIVAL), "1");
    } catch {
        /* Storage refused: the step and the tour show as before. */
    }
}

export function arrivedFromInstall(store: KeyValueStore): boolean {
    try {
        return store.getItem(key(ARRIVAL)) === "1";
    } catch {
        return false;
    }
}
