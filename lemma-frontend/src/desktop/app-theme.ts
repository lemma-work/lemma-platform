/** Keeps an app drawn in this Lemma's look.
 *
 *  A Lemma shows each app in a frame on the app's own origin, so the app
 *  inherits nothing from the page around it. Left alone it follows the
 *  device's light or dark setting, and switching Lemma's theme does nothing
 *  to it. The protocol is the SDK's `lemma-app-theme` message (tokens named
 *  `--lemma-app-*`), the one previews and embedded widgets already get: sent
 *  when the app loads, and again on every appearance change (a toggle here, or
 *  the system flipping under "system"). It is only ever addressed to the
 *  app's own origin. */

import { appThemeMessage, onAppearanceChange, type ThemeMessage } from "@/thread/widget-theme";

/** All this needs of the framed app's window. */
export interface ThemedView {
    postMessage(message: unknown, targetOrigin: string): void;
}

export interface AppThemeSources {
    /** The message to send, read from this page's resolved theme. */
    build: () => ThemeMessage;
    /** Calls `run` on every appearance change; returns the way to stop. */
    onChange: (run: () => void) => () => void;
    /** What a relative `src` is resolved against. */
    base: string;
}

const NOTHING = () => undefined;

/** Tell the app in `view` (loaded from `src`) this Lemma's theme now and on
 *  every change. Returns the way to stop, for when the frame goes. */
export function keepAppThemed(view: ThemedView, src: string, sources: Partial<AppThemeSources> = {}): () => void {
    const build = sources.build ?? appThemeMessage;
    const onChange = sources.onChange ?? onAppearanceChange;
    let origin: string;
    try {
        origin = new URL(src, sources.base ?? window.location.href).origin;
    } catch {
        return NOTHING;
    }
    /* An opaque origin ("null") cannot be addressed, and "*" would hand the
       theme to whatever the frame navigated to. */
    if (origin === "null") return NOTHING;
    const send = () => {
        try {
            view.postMessage(build(), origin);
        } catch { /* the frame moved on; its next load sends again */ }
    };
    send();
    return onChange(send);
}

/** The theme for one frame across all of its loads.
 *
 *  Every load is a new document that has heard nothing — a reload, an app
 *  that navigates, a sign-in that comes back — so each one is told again.
 *  The frame's window is the same object every time, which is why this is
 *  driven by the load itself rather than by noticing a new window. */
export function frameThemeKeeper(sources: Partial<AppThemeSources> = {}) {
    let stop: () => void = NOTHING;
    return {
        loaded(view: ThemedView | null, src: string): void {
            stop();
            stop = view ? keepAppThemed(view, src, sources) : NOTHING;
        },
        stop(): void {
            stop();
            stop = NOTHING;
        },
    };
}
