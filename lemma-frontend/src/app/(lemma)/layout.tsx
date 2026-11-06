import { Albert_Sans } from "next/font/google";
import "@/styles/outcomes.css";
import { Outcomes } from "./outcomes";

/* The app, under every one of its addresses -- `/home`, `/needs`, `/apps`,
   `/connectors`, `/people`, `/chats` and everything about a Lemma under
   `/l` -- mounted once here, beside pages that draw nothing: the frame reads
   its own address (`outcomes/address.ts`), so moving between them never
   crosses a boundary that would drop what its queries hold or reload an app
   it keeps open. Albert Sans is the product face chosen on 2026-10-07;
   figures stay on Inter (`--font-ui`), because Albert Sans as served has no
   tabular numerals. */
const face = Albert_Sans({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-outcomes", display: "swap" });

export default function OutcomesLayout({ children }: { children: React.ReactNode }) {
    return <div className={face.variable}><Outcomes />{children}</div>;
}
