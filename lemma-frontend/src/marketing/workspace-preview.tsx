"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { WorkspaceLoading } from "@/shell/workspace-loading";
import { PreviewProvider } from "./preview-provider";
import { readTourStep } from "./preview-mode";

const Workspace = dynamic(() => import("@/shell/shell").then(module => module.AppShell), { ssr: false, loading: () => <WorkspaceLoading /> });

/** `lemma-tour:ready` means the workspace has something to show, not just
 *  that this frame is listening: the landing page keeps its placeholder over
 *  the frame until then, so a visitor never watches the workspace assemble. */
export function WorkspacePreview() {
    const [step, setStep] = useState(-1);
    const [revision, setRevision] = useState(0);
    const painted = useRef(false);
    const announce = useCallback(() => window.parent.postMessage({ type: "lemma-tour:ready" }, window.location.origin), []);
    const onPainted = useCallback(() => {
        if (painted.current) return;
        painted.current = true;
        announce();
    }, [announce]);
    useEffect(() => {
        const listen = (event: MessageEvent) => {
            if (event.origin !== window.location.origin) return;
            if (event.source === window.parent) {
                /* The page asks when it arrives after this frame did, and
                   so missed the announcement. */
                if (event.data?.type === "lemma-tour:hello" && painted.current) announce();
                const next = readTourStep(event.data);
                if (next !== null) { setStep(next); setRevision(value => value + 1); }
            } else if (event.data?.type === "lemma-tour:interact" && Array.from(document.querySelectorAll("iframe")).some(frame => frame.contentWindow === event.source)) {
                window.parent.postMessage({ type: "lemma-tour:interact" }, window.location.origin);
            }
        };
        window.addEventListener("message", listen);
        return () => window.removeEventListener("message", listen);
    }, [announce]);
    return <PreviewProvider><Workspace demoStep={step} demoRevision={revision} onPreviewPainted={onPainted} /></PreviewProvider>;
}
