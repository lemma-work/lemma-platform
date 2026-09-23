import type { Metadata } from "next";
import { WorkspacePreview } from "@/marketing/workspace-preview";
import "../preview.css";

export const metadata: Metadata = { title: "Acme · Interactive product tour", robots: { index: false, follow: false } };

export default function LandingPreview() {
    return <WorkspacePreview />;
}
