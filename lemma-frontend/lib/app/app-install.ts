// The workspace's half of installing an app to a home screen.
//
// Installing is a top-level operation in every browser, and the app frame is a
// sandboxed cross-origin iframe: `beforeinstallprompt` never fires inside it,
// and a tab the frame opened for itself would inherit the sandbox and so could
// not install either. The frame therefore asks the workspace to do the opening,
// and the workspace marks the URL so the offer appears the moment the tab
// lands rather than waiting for a second visit.
//
// Both names are a contract with `app/core/app_install.py`; changing one
// without the other silently stops the handoff.

export const APP_INSTALL_REQUEST_MESSAGE = 'lemma-app-install-request';

const INSTALL_MARKER = '#install';

/**
 * The app's URL, marked so its install offer appears on arrival.
 *
 * Parsed with no base, because this runs while rendering the app header and
 * a client component still renders on the server, where there is no
 * `window.location` to resolve against. An app URL is absolute anyway --
 * `public_app_url` builds it from the scheme and the app host.
 */
export function appInstallUrl(url: string): string {
    try {
        const marked = new URL(url);
        marked.hash = INSTALL_MARKER;
        return marked.toString();
    } catch {
        // A URL we cannot parse is still a link worth following.
        return url;
    }
}
