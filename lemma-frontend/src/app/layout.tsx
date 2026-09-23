import type { Metadata } from "next";
import { CARRY_SCRIPT, PREFIX } from "@/session/storage";
import "@/styles/base.css";
import "@/styles/shell.css";
import "@/styles/refinement.css";
import "@/styles/library.css";
import "@/styles/forms.css";
import "@/styles/auth.css";
import "@/styles/agents.css";
import "@/styles/skills.css";
import "@/styles/schedules.css";
import "@/styles/workflows.css";
import "@/styles/computer.css";
import "@/styles/tool-cards.css";
import "@/styles/document.css";

export const metadata: Metadata = { title: "Lemma", description: "Your teammates and the work they are doing." };

// Apply saved appearance before paint. The app reads the same preferences.
// `CARRY_SCRIPT` runs first and must: it carries these keys over from the
// pre-rename prefix, and reading them before it has paints one frame of the
// wrong theme.
const themeScript = `(function(){try{var d=document.documentElement,s=localStorage,t=s.getItem('${PREFIX}:theme');if(t==='light'||t==='dark')d.dataset.theme=t;d.dataset.accent=s.getItem('${PREFIX}:accent')||'violet';d.dataset.corners=s.getItem('${PREFIX}:corners')||'soft'}catch(e){}})()`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
    return <html lang="en" suppressHydrationWarning>
        <head>
            {/* Two tags, not one string. Concatenating two IIFEs is how the
                appearance script stopped running once already: it parses, and
                then calls the first one's return value. */}
            <script dangerouslySetInnerHTML={{ __html: CARRY_SCRIPT }} />
            <script dangerouslySetInnerHTML={{ __html: themeScript }} />
            <link rel="preconnect" href="https://fonts.googleapis.com" />
            <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
            <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Schibsted+Grotesk:ital,wght@0,400..600;1,400..600&family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;1,6..72,400&family=DM+Mono:wght@400;500&display=swap" />
        </head>
        <body><div id="root">{children}</div></body>
    </html>;
}
