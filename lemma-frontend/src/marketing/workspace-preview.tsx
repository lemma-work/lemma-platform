"use client";

import { PageLoading } from "@/ui/loading";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { PreviewProvider } from "./preview-provider";
import { readTourStep } from "./preview-mode";

const Workspace = dynamic(() => import("@/shell/shell").then(module => module.AppShell), { ssr: false, loading: () => <PageLoading label="Opening sample workspace" /> });

export function WorkspacePreview() {
    const [step, setStep] = useState(-1);
    const [revision, setRevision] = useState(0);
    useEffect(() => {
        const listen = (event: MessageEvent) => {
            if (event.origin !== window.location.origin) return;
            if (event.source === window.parent) {
                const next = readTourStep(event.data);
                if (next !== null) { setStep(next); setRevision(value => value + 1); }
            } else if (event.data?.type === "lemma-tour:interact" && Array.from(document.querySelectorAll("iframe")).some(frame => frame.contentWindow === event.source)) {
                window.parent.postMessage({ type: "lemma-tour:interact" }, window.location.origin);
            }
        };
        window.addEventListener("message", listen);
        window.parent.postMessage({ type: "lemma-tour:ready" }, window.location.origin);
        return () => window.removeEventListener("message", listen);
    }, []);
    return <PreviewProvider><Workspace demoStep={step} demoRevision={revision} /></PreviewProvider>;
}
