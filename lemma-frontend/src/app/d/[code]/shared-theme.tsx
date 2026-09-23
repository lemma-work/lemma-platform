"use client";

import { useEffect, useState } from "react";
import { key } from "@/session/storage";

/** Light, dark, or whatever the machine says.
 *
 *  Not the app's `ThemeSwitch`. That one also carries eight accents and three
 *  corner scales, which are choices for somebody who lives here — a reader who
 *  followed a link wants the page to stop glaring at them at night and nothing
 *  else. It writes the same `theme` key and stamps the same
 *  `data-theme`, so a choice made on a shared document is the choice the app
 *  opens with, and the layout's pre-paint script already reads it.
 *
 *  Self-contained on purpose: importing the app's switcher would pull the icon
 *  library onto a page that strangers load to read one file. */

type Theme = "system" | "light" | "dark";
const KEY = key("theme");

const MARKS: Record<Theme, React.ReactElement> = {
    system: (
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <rect x="1.8" y="2.6" width="12.4" height="8.6" rx="1.6" stroke="currentColor" strokeWidth="1.4" />
            <path d="M5.6 13.6h4.8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
    ),
    light: (
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <circle cx="8" cy="8" r="3.2" stroke="currentColor" strokeWidth="1.4" />
            <g stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
                <path d="M8 1v1.6M8 13.4V15M15 8h-1.6M2.6 8H1M12.9 3.1l-1.1 1.1M4.2 11.8l-1.1 1.1M12.9 12.9l-1.1-1.1M4.2 4.2 3.1 3.1" />
            </g>
        </svg>
    ),
    dark: (
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path d="M13.4 9.6A5.8 5.8 0 0 1 6.4 2.6a5.9 5.9 0 1 0 7 7Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
        </svg>
    ),
};

const ORDER: Theme[] = ["system", "light", "dark"];
const LABEL: Record<Theme, string> = { system: "Match my system", light: "Light", dark: "Dark" };

export function SharedTheme() {
    /* Starts as `system` on both sides of the hydration line, then catches up
       to what is stored. Reading localStorage during the first render would
       mismatch the server's HTML; the layout's inline script has already put
       the right colours on screen, so this is only the control agreeing with
       them a moment later. */
    const [theme, setTheme] = useState<Theme>("system");
    useEffect(() => {
        try {
            const stored = localStorage.getItem(KEY);
            if (stored === "light" || stored === "dark") setTheme(stored);
        } catch { /* a browser refusing storage still renders */ }
    }, []);

    function choose(next: Theme) {
        setTheme(next);
        const root = document.documentElement;
        if (next === "system") root.removeAttribute("data-theme");
        else root.dataset.theme = next;
        try {
            if (next === "system") localStorage.removeItem(KEY);
            else localStorage.setItem(KEY, next);
        } catch { /* the page still looks right for this visit */ }
    }

    return (
        <div className="sharedtheme" role="group" aria-label="Appearance">
            {ORDER.map((value) => (
                <button
                    key={value}
                    type="button"
                    aria-pressed={theme === value}
                    title={LABEL[value]}
                    aria-label={LABEL[value]}
                    onClick={() => choose(value)}
                >
                    {MARKS[value]}
                </button>
            ))}
        </div>
    );
}
