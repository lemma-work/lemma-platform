"use client";

import "@/styles/desktop.css";
import { AppsIcon, ExternalIcon } from "@/ui/icons";
import { openPodApp } from "./pod-apps";

/** Stands where an app's frame would, when no frame could be signed in.
 *
 *  On macOS an app framed on its own address gets no session, and the
 *  workspace frames an alias instead (`appFrameMode` has why). Where that
 *  alias cannot be had -- an older app shell -- the app's own window is
 *  top-level and signs in normally, so this offers that, from a click, since
 *  opening a window is the click's to do. */
export function AppWindowPanel({ url, hidden }: { url: string; hidden?: boolean }) {
    return (
        <div className="app-window" hidden={hidden}>
            <section className="app-window__card">
                <span className="app-window__mark" aria-hidden="true"><AppsIcon size={18} /></span>
                <h3>This app opens in its own window</h3>
                {/* Neutral on purpose. It lands here when the shell gave no
                    signed-in address for the frame — an older shell, or a
                    refusal — and "macOS will not sign it in" blamed the one
                    thing that was not the cause. */}
                <p>
                    It couldn’t be shown signed in beside the conversation. In a window of its own it
                    opens with your session.
                </p>
                <button className="btn btn--primary" onClick={() => openPodApp(url)}>
                    <ExternalIcon size={15} /> Open app
                </button>
            </section>
        </div>
    );
}
