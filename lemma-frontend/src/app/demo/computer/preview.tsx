"use client";

import { useState } from "react";
import { createPortal } from "react-dom";
import { WorkspaceStartup } from "@/computer/startup-view";
import { usePictureInPicture } from "@/computer/use-picture-in-picture";
import type { WorkspaceStatus } from "@/computer/startup";

const states = {
    downloading: { state: "downloading", done_mb: 245, total_mb: 980, detail: "Downloading the workspace image." },
    unknown: { state: "downloading", detail: "Preparing the workspace image." },
    starting: { state: "starting", detail: "Your computer is starting." },
} satisfies Record<string, WorkspaceStatus>;

export function ComputerPreview() {
    const [state, setState] = useState<keyof typeof states>("downloading");
    const pip = usePictureInPicture(true);
    return (
        <main className="computer-preview library-view">
            <header className="library-heading"><div><h1>Computer preview</h1><p>Development preview · sample status, no live computer connected.</p></div></header>
            <nav className="computer-preview-actions" aria-label="Workspace state">
                <button className="btn" aria-pressed={state === "downloading"} onClick={() => setState("downloading")}>Downloading</button>
                <button className="btn" aria-pressed={state === "unknown"} onClick={() => setState("unknown")}>Unknown size</button>
                <button className="btn" aria-pressed={state === "starting"} onClick={() => setState("starting")}>Starting</button>
            </nav>
            <WorkspaceStartup status={states[state]} />
            <section className="computer-startup">
                <h2>Floating window</h2>
                <p>The live browser uses this window control. This preview only opens sample text.</p>
                {pip.supported ? <button className="btn" onClick={() => pip.pipWindow ? pip.close() : void pip.open()}>{pip.pipWindow ? "Bring it back" : "Pop out"}</button> : <p>This browser does not support floating windows.</p>}
                {pip.failed && <p role="alert">Couldn’t open the floating window.</p>}
                <a href="/t">Open your workspace</a>
            </section>
            {pip.pipWindow && createPortal(<section className="computer-startup"><h1>Computer preview</h1><p>Sample content in the floating window.</p><button className="btn" onClick={pip.close}>Bring it back</button></section>, pip.pipWindow.document.body)}
        </main>
    );
}
