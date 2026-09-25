import { isDesktop } from "./bridge";

/** Open a URL outside the workspace, in a window that cannot reach back.
 *
 *  `noopener` so the opened page holds no `window.opener` and cannot navigate
 *  the workspace somewhere else — which matters most for what this is used
 *  for: authorization and setup URLs that come from a provider's response, not
 *  from Lemma. `noreferrer` also withholds the referrer, which would otherwise
 *  hand the provider workspace and pod ids from the path.
 *
 *  In the desktop app nothing here decides where it lands. The shell sees the
 *  new-window request and routes it itself (`new_window_disposition` in
 *  `desktop/src/navigation.rs`): a published pod app gets its own app window,
 *  a page of this workspace stays in the app, and anything else goes to the
 *  system browser.
 *
 *  Returns nothing, deliberately. With `noopener` a browser hands back `null`
 *  whether or not the tab opened, and in the desktop app the webview never
 *  opens one at all — so a caller that read `null` as "blocked" was telling
 *  everyone their pop-up blocker had stopped a tab that had in fact opened. */
export function openExternal(url: string): void {
    /* A mail or phone link is a handler, not a page: a new blank tab that
       immediately hands off and stays open is the wrong answer to it. */
    if (/^(mailto|tel):/i.test(url)) {
        window.location.assign(url);
        return;
    }
    window.open(url, "_blank", "noopener,noreferrer");
}

/** Open a URL that is not known yet, from a click that is happening now.
 *
 *  A browser treats a tab opened after an await as a pop-up and blocks it, so
 *  in a browser the tab is opened empty inside the click's own turn and pointed
 *  at the URL when it arrives; `noopener` cannot be used for that, because then
 *  there is nothing to point, so the reference is cut by hand. The desktop
 *  shell refuses a blank window outright, and opens a routed one without a
 *  gesture, so there it simply waits and hands the URL to `openExternal`.
 *
 *  Resolves once the URL was handed on; rejects, closing any empty tab, if it
 *  never arrived. */
export async function openExternalWhenReady(url: Promise<string>): Promise<void> {
    if (isDesktop()) {
        openExternal(await url);
        return;
    }
    const opened = window.open("", "_blank");
    let target: string;
    try {
        target = await url;
    } catch (problem) {
        if (opened && !opened.closed) opened.close();
        throw problem;
    }
    if (!opened || opened.closed) {
        openExternal(target);
        return;
    }
    opened.opener = null;
    opened.location.replace(target);
}
