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

/** Give a tab that is still waiting for its address something to read.
 *
 *  A tab claimed inside a click starts on `about:blank` and stays there until
 *  whatever it is waiting for arrives — an authorize URL, a browser-access
 *  grant — which takes seconds. An empty tab for that long reads as a click
 *  that did nothing, and the person closes the very tab the flow needed. So
 *  the blank document the tab already has is written into, synchronously,
 *  before anything navigates: it says where they are going until the page they
 *  are going to arrives.
 *
 *  The DOM rather than `document.write`, which would have to be finished
 *  before the document is: this runs on an already-parsed blank page. Styled
 *  no further than the type, because the page belongs to whichever provider
 *  comes next, not to this app. */
export function holdTab(tab: Window, what: string): void {
    const page = tab.document;
    const said = "Taking you to " + what + "…";
    page.title = said;
    const body = page.body;
    if (!body) return;
    const style = page.createElement("style");
    style.textContent =
        "body{margin:0;min-height:100vh;display:grid;place-items:center;"
        + "font:400 15px/1.6 system-ui,sans-serif;text-align:center}";
    const line = page.createElement("p");
    line.textContent = said;
    body.replaceChildren(style, line);
}

/** Open a URL that is not known yet, from a click that is happening now.
 *
 *  A browser treats a tab opened after an await as a pop-up and blocks it, so
 *  in a browser the tab is opened empty inside the click's own turn, given a
 *  face for the wait (`holdTab`), and pointed at the URL when it arrives;
 *  `noopener` cannot be used for that, because then there is nothing to point,
 *  so the reference is cut by hand. The desktop shell refuses a blank window
 *  outright, and opens a routed one without a gesture, so there it simply
 *  waits and hands the URL to `openExternal`.
 *
 *  Resolves once the URL was handed on; rejects, closing any empty tab, if it
 *  never arrived. */
export async function openExternalWhenReady(url: Promise<string>, what: string): Promise<void> {
    if (isDesktop()) {
        openExternal(await url);
        return;
    }
    const opened = window.open("", "_blank");
    /* Before the await, not after: the await is the blank part. */
    if (opened) holdTab(opened, what);
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
