import { Albert_Sans } from "next/font/google";
import type { ReactNode } from "react";

/* The plugin pages set their words in Albert Sans, the face the app's own
   frame uses (`app/(lemma)/layout.tsx`), so a plugin reads here the way it
   will read once added. Figures stay on Inter (`--font-ui`), because Albert
   Sans as served has no tabular numerals. */
const face = Albert_Sans({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-frame", display: "swap" });

export default function PluginsLayout({ children }: { children: ReactNode }) {
    return <div className={face.variable}>{children}</div>;
}
