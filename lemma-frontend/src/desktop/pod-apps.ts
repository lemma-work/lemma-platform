import { crossSiteFramesCarryCookies, useCrossSiteFramesCarryCookies } from "./bridge";
import { openExternal } from "./open-external";

/** Pod apps in the desktop app.
 *
 *  Where an iframed app would load signed out — macOS, on the local hostnames;
 *  `crossSiteFramesCarryCookies` has why — an app opens in a window of its own
 *  instead, which is top-level and first-party and so has the session.
 *
 *  There is no command for it. The shell already routes a new-window request
 *  for a published app to its app window (`OpenAppWindow` in
 *  `desktop/src/navigation.rs`, `open_pod_app_window` in `pod_windows.rs`), and
 *  that window is deliberately outside every capability file: it runs
 *  user-authored code, so it gets no IPC at all. Asking for a new window is
 *  therefore the whole mechanism, and the same call is right in a browser. */

export function appsOpenInWindow(): boolean {
    return !crossSiteFramesCarryCookies();
}

export function useAppsOpenInWindow(): boolean {
    return !useCrossSiteFramesCarryCookies();
}

/** Open an app in its own window. Call from the click itself. */
export function openPodApp(url: string): void {
    openExternal(url);
}
