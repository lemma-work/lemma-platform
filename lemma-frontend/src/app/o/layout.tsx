import { Albert_Sans } from "next/font/google";
import "@/styles/outcomes.css";
import { Outcomes } from "./outcomes";

/* The outcome-first frame lives beside the workspace rather than inside it:
   the same session and data source under a different shell, so trying it
   changes nothing for anyone still on `/t`. Albert Sans is the product face
   chosen on 2026-10-07; figures stay on Inter (`--font-ui`), because Albert
   Sans as served has no tabular numerals. */
const face = Albert_Sans({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-outcomes", display: "swap" });

export default function OutcomesLayout({ children }: { children: React.ReactNode }) {
    return <div className={face.variable}><Outcomes />{children}</div>;
}
