"use client";
import dynamic from "next/dynamic";
import s from "./import.module.css";

/** The panel reads the session and the URL, both browser-only, so it renders
 *  on the client alone — with a placeholder the same size as its first
 *  state, so the page does not jump when it arrives. */
export const InstallHost = dynamic(
    () => import("./install-panel").then((m) => m.InstallPanel),
    {
        ssr: false,
        loading: () => (
            <div className={s.panel}>
                <p role="status" className={s.muted}>
                    Opening…
                </p>
            </div>
        ),
    },
);
