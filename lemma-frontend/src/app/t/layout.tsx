import { Albert_Sans } from "next/font/google";
import "@/styles/outcomes.css";
import { Outcomes } from "./outcomes";

/* The app: the same session and data source as the classic workspace, which
   now lives at `/l`, under the frame's own shell. Albert Sans is the product
   face chosen on 2026-10-07; figures stay on Inter (`--font-ui`), because
   Albert Sans as served has no tabular numerals. */
const face = Albert_Sans({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-outcomes", display: "swap" });

export default function OutcomesLayout({ children }: { children: React.ReactNode }) {
    return <div className={face.variable}><Outcomes />{children}</div>;
}
