"use client";
import dynamic from "next/dynamic";
import { PreviewProvider } from "./preview-provider";
import { KitFeedbackLoop } from "./kit/feedback-loop";
import { RemyApp } from "./apps/remy";
import { JuneApp } from "./apps/june";
import { ScoutApp } from "./apps/scout";

function WorkApp() {
    const id = new URLSearchParams(window.location.search).get("teammate") ?? "kit";
    return <PreviewProvider>{id === "remy" ? <RemyApp /> : id === "june" ? <JuneApp /> : id === "scout" ? <ScoutApp /> : <KitFeedbackLoop />}</PreviewProvider>;
}
export const LaunchPreview = dynamic(() => Promise.resolve(WorkApp), { ssr: false });
