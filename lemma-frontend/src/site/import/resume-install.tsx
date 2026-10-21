"use client";

import { useState } from "react";
import { ArrowRightIcon, CloseIcon } from "@/ui/icons";
import { forgetInstall, pendingInstall, type PendingInstall } from "./pending-install";

/** "You were installing Ledger" — the way back to an install that a sign-in
 *  or a new organization dropped on the floor. Drawn wherever somebody lands
 *  in the workspace without a teammate yet; it is nothing when no install is
 *  waiting. */
export function ResumeInstall() {
    const [held, setHeld] = useState<PendingInstall | null>(() => {
        try {
            return pendingInstall(window.localStorage);
        } catch {
            return null;
        }
    });
    if (!held) return null;
    return (
        <div className="resume-install" role="status">
            <span>
                You were installing <b>{held.title}</b>.
            </span>
            <a className="btn btn--primary" href={held.path}>
                Continue installing <ArrowRightIcon size={15} />
            </a>
            <button
                className="resume-install__dismiss"
                aria-label={"Not now: stop offering " + held.title}
                onClick={() => {
                    try { forgetInstall(window.localStorage); } catch { /* no storage */ }
                    setHeld(null);
                }}
            >
                <CloseIcon size={15} />
            </button>
        </div>
    );
}
