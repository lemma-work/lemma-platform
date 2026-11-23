"use client";

import type { ReactNode } from "react";
import "@/styles/desktop.css";
import "@/styles/frame-states.css";
import { AppsIcon, ExternalIcon } from "@/ui/icons";
import { openPodApp } from "./pod-apps";

/** Stands where an app's frame would, when no frame could be signed in.
 *
 *  On macOS an app framed on its own address gets no session, and the
 *  workspace frames an alias instead (`appFrameMode` has why). Where that
 *  alias cannot be had -- an older app shell -- the app's own window is
 *  top-level and signs in normally, so this offers that, from a click, since
 *  opening a window is the click's to do. Drawn the way an app opening in
 *  the frame is: its mark, one line, the way forward, in the middle. */
export function AppWindowPanel({ url, hidden, reason, mark }: { url: string; hidden?: boolean; reason?: string; mark?: ReactNode }) {
    return (
        <div className="app-window" hidden={hidden}>
            <section className="app-open">
                {mark ?? <span className="app-open__mark" aria-hidden="true"><AppsIcon size={22} /></span>}
                <h2 className="app-open__t">This app opens in its own window</h2>
                {/* Neutral on purpose. It lands here when the shell gave no
                    signed-in address for the frame — an older shell, or a
                    refusal — and "macOS will not sign it in" blamed the one
                    thing that was not the cause. */}
                <p className="app-open__line">
                    It couldn’t be shown signed in beside the conversation. In a window of its own it
                    opens with your session.
                </p>
                {reason && <p className="app-open__note">{reason}</p>}
                <div className="app-open__acts">
                    <button type="button" className="b b--m b--primary" onClick={() => openPodApp(url)}>
                        <ExternalIcon size={15} /> Open app
                    </button>
                </div>
            </section>
        </div>
    );
}
