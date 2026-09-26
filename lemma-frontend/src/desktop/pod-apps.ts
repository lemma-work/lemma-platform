import { useEffect, useState } from "react";
import { invoke, useAppFrameMode, type AppFrameMode } from "./bridge";
import { openExternal } from "./open-external";

/** Pod apps beside the agent, in the desktop app and out of it.
 *
 *  Every API returns an app's own URL (`<slug>.apps.lemma.localhost:<port>`
 *  locally), and that is what a browser, WebView2 and "open in a new window"
 *  use. Only the macOS app frames something else: an alias of that URL on the
 *  workspace's own host, which WebKit counts as the same site and so sends the
 *  session to (`appFrameMode` has why). Where no alias can be had, the app
 *  opens in a window of its own instead.
 *
 *  There is no command for the window. The shell already routes a new-window
 *  request for a published app to its app window (`OpenAppWindow` in
 *  `desktop/src/navigation.rs`), and that window is deliberately outside every
 *  capability file: it runs user-authored code, so it gets no IPC at all. */

export type AppFrame =
    | { kind: "pending" }
    | { kind: "frame"; src: string }
    | { kind: "window" };

/** What to show for `url`, given how this page frames apps.
 *
 *  `ask` is the shell's `app_frame_url`. An answer that is not a URL, or no
 *  answer -- a shell from before the command, a refusal -- falls back to the
 *  window, which always works, rather than a frame that would load signed
 *  out. */
export async function resolveAppFrame(
    url: string,
    mode: AppFrameMode,
    ask: (url: string) => Promise<unknown>,
): Promise<AppFrame> {
    if (mode === "direct") return { kind: "frame", src: url };
    if (mode === "window") return { kind: "window" };
    try {
        const answer = await ask(url);
        const src = (answer as { url?: unknown } | null)?.url;
        if (typeof src === "string" && /^https?:\/\//.test(src)) return { kind: "frame", src };
    } catch {
        /* Falls through to the window. */
    }
    return { kind: "window" };
}

function askShell(url: string): Promise<unknown> {
    return invoke("app_frame_url", { url });
}

/** The frame for one app URL, resolved once per URL. */
export function useAppFrame(url: string): AppFrame {
    const mode = useAppFrameMode();
    const [frame, setFrame] = useState<AppFrame>(() =>
        mode === "direct" ? { kind: "frame", src: url } : { kind: "pending" });
    useEffect(() => {
        let live = true;
        if (mode === "direct") {
            setFrame({ kind: "frame", src: url });
            return;
        }
        setFrame({ kind: "pending" });
        void resolveAppFrame(url, mode, askShell).then((next) => { if (live) setFrame(next); });
        return () => { live = false; };
    }, [url, mode]);
    return frame;
}

/** Open an app in its own window. Call from the click itself. */
export function openPodApp(url: string): void {
    openExternal(url);
}
