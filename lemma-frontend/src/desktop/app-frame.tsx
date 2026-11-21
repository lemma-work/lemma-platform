"use client";

import { useEffect, useState, type ReactNode } from "react";
import { frameThemeKeeper } from "./app-theme";
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
 *  as a window where it would not be. `pod-apps.ts` decides which.
 *
 *  `cover` is drawn over the frame until the frame's load event, and while
 *  the desktop app is still naming a frame for it: what the app looks like
 *  while it opens. A load is only the frame answering -- a cross-origin
 *  frame fires it for an error page too -- so the cover goes and nothing is
 *  said about how the app is. A new `attempt` loads the frame afresh, the
 *  cover with it. */
export function AppFrameView({
    url,
    hidden,
    frameRef,
    onFrameLoad,
    cover,
    attempt = 0,
    mark,
}: {
    url: string;
    hidden?: boolean;
    frameRef: (element: HTMLIFrameElement | null) => void;
    onFrameLoad: (view: Window | null) => void;
    cover?: ReactNode;
    attempt?: number;
    /** The app's own mark, for the window offered in its place. */
    mark?: ReactNode;
}) {
    const frame = useAppFrame(url);
    /* The app is drawn in this Lemma's theme: told on each of its loads and on
       every appearance change, until the frame goes or points elsewhere. */
    const [theme] = useState(() => frameThemeKeeper());
    const src = frame.kind === "frame" ? frame.src : null;
    useEffect(() => () => theme.stop(), [theme, src]);
    /* Which frame last loaded: its address and its attempt. */
    const [loaded, setLoaded] = useState<string | null>(null);
    const loadKey = src === null ? null : src + "#" + attempt;
    if (frame.kind === "window") return <AppWindowPanel url={url} hidden={hidden} reason={frame.reason} mark={mark} />;
    /* No frame yet: the shell answers in milliseconds, and a frame on the
       app's own address first would load it signed out and then swap. Said,
       rather than a blank pane — milliseconds on a warm machine is seconds on
       a busy one, and a blank pane reads as broken. */
    if (frame.kind === "pending" && !cover) {
        return (
            <div className="app-window" hidden={hidden} aria-busy="true">
                <p className="empty-row" role="status">Opening {appLabel(url)}…</p>
            </div>
        );
    }
    const opening = Boolean(cover) && (loadKey === null || loaded !== loadKey);
    return (
        <>
            {frame.kind === "frame" && (
                <iframe
                    key={attempt}
                    ref={frameRef}
                    className="frame"
                    title="App"
                    src={frame.src}
                    /* A deck's present mode, a video, a map: an app may ask for the
                       whole screen, and a frame without this refuses it silently. */
                    allow="fullscreen"
                    allowFullScreen
                    hidden={hidden}
                    onLoad={event => {
                        const view = event.currentTarget.contentWindow;
                        theme.loaded(view, frame.src);
                        setLoaded(frame.src + "#" + attempt);
                        onFrameLoad(view);
                    }}
                />
            )}
            {opening && <div className="app-veil" hidden={hidden} aria-busy="true">{cover}</div>}
        </>
    );
}
