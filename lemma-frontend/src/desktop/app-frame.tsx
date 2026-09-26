"use client";

import { AppWindowPanel } from "./app-window";
import { useAppFrame } from "./pod-apps";

/** What to call an app in a sentence, from its address: a published app lives
 *  at `<slug>.apps.<host>`, and the slug is the name its builder gave it. */
function appLabel(url: string): string {
    try {
        const [slug, second] = new URL(url).hostname.split(".");
        return second === "apps" && slug ? slug : "the app";
    } catch {
        return "the app";
    }
}

/** One pod app beside the agent: framed where a frame is signed in, offered
 *  as a window where it would not be. `pod-apps.ts` decides which. */
export function AppFrameView({
    url,
    hidden,
    frameRef,
    onFrameLoad,
}: {
    url: string;
    hidden?: boolean;
    frameRef: (element: HTMLIFrameElement | null) => void;
    onFrameLoad: (view: Window | null) => void;
}) {
    const frame = useAppFrame(url);
    if (frame.kind === "window") return <AppWindowPanel url={url} hidden={hidden} reason={frame.reason} />;
    /* No frame yet: the shell answers in milliseconds, and a frame on the
       app's own address first would load it signed out and then swap. Said,
       rather than a blank pane — milliseconds on a warm machine is seconds on
       a busy one, and a blank pane reads as broken. */
    if (frame.kind === "pending") {
        return (
            <div className="app-window" hidden={hidden} aria-busy="true">
                <p className="empty-row" role="status">Opening {appLabel(url)}…</p>
            </div>
        );
    }
    return (
        <iframe
            ref={frameRef}
            className="frame"
            title="App"
            src={frame.src}
            hidden={hidden}
            onLoad={event => onFrameLoad(event.currentTarget.contentWindow)}
        />
    );
}
