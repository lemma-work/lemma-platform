"use client";

import { useEffect, useRef } from "react";
import { appThemeMessage, onAppearanceChange } from "@/thread/widget-theme";

/** A plugin's try-it demo: its real apps on made-up rows, served from this
 *  site, in a frame told the site's light or dark and colours with the
 *  `lemma-app-theme` message apps get inside a Lemma.
 *
 *  It is told when the frame loads, again when the demo says it is listening
 *  (`lemma-demo-ready`: an app that loads in parts starts listening after the
 *  load event, and would miss the first), again on every load after (the
 *  demo's own bar switches apps by loading another page), and on every change
 *  of appearance here. Only ever addressed to this site's own origin. */
export function DemoFrame({ src, title, className }: { src: string; title: string; className?: string }) {
    const ref = useRef<HTMLIFrameElement>(null);

    useEffect(() => {
        const frame = ref.current;
        if (!frame) return;
        const origin = window.location.origin;
        const send = () => {
            try {
                frame.contentWindow?.postMessage(appThemeMessage(), origin);
            } catch {
                /* the frame is between pages; its next load sends again */
            }
        };
        const ready = (event: MessageEvent) => {
            const data = event.data as { type?: unknown } | null;
            if (event.source === frame.contentWindow && data?.type === "lemma-demo-ready") send();
        };
        frame.addEventListener("load", send);
        window.addEventListener("message", ready);
        const stop = onAppearanceChange(send);
        send();
        return () => {
            frame.removeEventListener("load", send);
            window.removeEventListener("message", ready);
            stop();
        };
    }, []);

    return <iframe ref={ref} className={className} src={src} title={title} sandbox="allow-scripts allow-same-origin allow-forms" />;
}
