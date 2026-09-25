"use client";

import { AppWindowPanel } from "./app-window";
import { useAppFrame } from "./pod-apps";

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
    if (frame.kind === "window") return <AppWindowPanel url={url} hidden={hidden} />;
    /* Nothing yet: the shell answers in milliseconds, and a frame on the
       app's own address first would load it signed out and then swap. */
    if (frame.kind === "pending") return <div className="frame" hidden={hidden} aria-busy="true" />;
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
