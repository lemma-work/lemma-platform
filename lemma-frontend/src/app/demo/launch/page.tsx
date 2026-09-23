import type { Metadata } from "next";
import { LaunchPreview } from "@/marketing/launch-preview";
import "../preview.css";

export const metadata: Metadata = { title: "Acme · Teammate workspace", robots: { index: false, follow: false } };
export default function LaunchPage() { return <LaunchPreview />; }
