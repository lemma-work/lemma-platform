"use client";

import "@/styles/desktop.css";
import { CheckCircleIcon, CloseIcon, WarningIcon } from "@/ui/icons";
import { LoadingIndicator } from "@/ui/loading";
import { useAutoConnectThisComputer } from "./auto-connect";
import { useSandboxImageNotice } from "./sandbox-images";
import { SetupChecklist } from "./setup-checklist";
import { NoModelNotice } from "./no-model-notice";

/** Everything the desktop app does behind the workspace, mounted once in the
 *  authenticated shell and silent in a browser.
 *
 *  The first-run Server setup checklist, once per local install. And two
 *  things behind it. This computer connects itself to the workspace on
 *  screen (`auto-connect.ts`), which reports on the Models page rather than
 *  here — nobody asked for it, so it must not interrupt. And the sandbox image
 *  download, which somebody did ask for, gets one quiet notice in the corner:
 *  a progress line while it runs, and a dismissable ending. */
export function DesktopNotices({ orgId = null }: { orgId?: string | null }) {
    useAutoConnectThisComputer();
    const { notice, dismiss } = useSandboxImageNotice();
    return <>
        <SetupChecklist />
        {/* One corner card at a time: a download somebody asked for first. */}
        {notice.kind === "none"
            ? <NoModelNotice orgId={orgId} />
            : <SandboxNotice notice={notice} dismiss={dismiss} />}
    </>;
}

function SandboxNotice({ notice, dismiss }: ReturnType<typeof useSandboxImageNotice>) {
    if (notice.kind === "none") return null;
    return (
        <div className="desk-notice" role="status" aria-live="polite" data-kind={notice.kind}>
            <span className="desk-notice__mark" aria-hidden="true">
                {notice.kind === "downloading" ? <LoadingIndicator inline label="Downloading" />
                    : notice.kind === "ready" ? <CheckCircleIcon size={17} />
                    : <WarningIcon size={17} />}
            </span>
            <span className="desk-notice__body">
                <b>{notice.title}</b>
                <span>{notice.description}</span>
            </span>
            <button className="desk-notice__close icon-button" aria-label="Dismiss" title="Dismiss" onClick={dismiss}>
                <CloseIcon size={14} />
            </button>
        </div>
    );
}
