"use client";

import "@/styles/desktop.css";
import { AppsIcon, ExternalIcon } from "@/ui/icons";
import { openPodApp } from "./pod-apps";

/** Stands where an app's frame would, when a frame would load it signed out.
 *
 *  Apps run on their own address, and on macOS the desktop webview will not
 *  give an embedded one the session (`crossSiteFramesCarryCookies` has the
 *  measurements). Its own window is top-level and signs in normally, so this
 *  offers that — from a click, since opening a window is the click's to do. */
export function AppWindowPanel({ url, hidden }: { url: string; hidden?: boolean }) {
    return (
        <div className="app-window" hidden={hidden}>
            <section className="app-window__card">
                <span className="app-window__mark" aria-hidden="true"><AppsIcon size={18} /></span>
                <h3>This app opens in its own window</h3>
                <p>
                    Apps run on their own address, and macOS will not sign an embedded one in.
                    In its own window it has your session.
                </p>
                <button className="btn btn--primary" onClick={() => openPodApp(url)}>
                    <ExternalIcon size={15} /> Open app
                </button>
            </section>
        </div>
    );
}
