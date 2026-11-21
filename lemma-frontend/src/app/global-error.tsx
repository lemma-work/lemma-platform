"use client";

import { useEffect } from "react";

import { captureClientError } from "@/site/analytics/client";
import { PageBroke } from "@/outcomes/page-boundary";
import { PREFIX } from "@/session/storage";

/* An error in the root layout itself, which `error.tsx` cannot catch because
   it sits inside that layout. This replaces the whole document, without the
   app's stylesheets or its appearance script, so the few rules the calm block
   needs are written here, in the app's own colours, light and dark. It says
   what every page that broke says (`outcomes/page-boundary.tsx`). */
const STYLE = `
:root { color-scheme: light dark; --paper: #ffffff; --ink: #0d0d0d; --ink-2: #5d5d5d; --ink-3: #8f8f8f; --line-2: rgb(0 0 0 / 0.10); --wash-b: #fafafa; --warn: #8a5f0f; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --paper: #212121; --ink: #ececec; --ink-2: #b4b4b4; --ink-3: #8f8f8f; --line-2: rgb(255 255 255 / 0.12); --wash-b: #262626; --warn: #dcb06a; } }
:root[data-theme="dark"] { --paper: #212121; --ink: #ececec; --ink-2: #b4b4b4; --ink-3: #8f8f8f; --line-2: rgb(255 255 255 / 0.12); --wash-b: #262626; --warn: #dcb06a; }
body { margin: 0; background: var(--paper); color: var(--ink); font: 14px/1.45 ui-sans-serif, -apple-system, system-ui, "Segoe UI", Helvetica, Arial, sans-serif; }
.kstate { display: grid; place-content: center; min-height: 100dvh; padding: 24px 16px; box-sizing: border-box; }
.kcalm { display: grid; justify-items: center; gap: 10px; padding: 56px 16px 40px; text-align: center; }
.kcalm__i { display: grid; place-items: center; width: 52px; height: 52px; border-radius: 50%; background: color-mix(in srgb, var(--warn) 12%, var(--paper)); color: var(--warn); }
.kcalm__t { margin: 8px 0 0; font-size: 20px; font-weight: 500; letter-spacing: -0.01em; text-wrap: balance; }
.kcalm__line { max-width: 440px; color: var(--ink-2); font-size: 14.5px; line-height: 1.5; text-wrap: pretty; }
.kcalm__line p, .kcalm__note { margin: 0; }
.kcalm__note { color: var(--ink-3); font-size: 12.5px; }
.kcalm__acts { display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; margin-top: 10px; }
.b { display: inline-flex; align-items: center; justify-content: center; gap: 7px; height: 40px; padding: 0 16px; border: 1px solid transparent; border-radius: 11px; font: inherit; font-size: 14px; font-weight: 500; white-space: nowrap; cursor: pointer; }
.b svg { flex: none; width: 16px; height: 16px; }
.b--primary { background: var(--ink); color: var(--paper); }
.b--secondary { border-color: var(--line-2); background: var(--paper); color: var(--ink); }
.b--secondary:hover { background: var(--wash-b); }
`;

export default function GlobalError({ error }: { error: Error & { digest?: string }; retry: () => void }) {
    useEffect(() => {
        captureClientError(error);
    }, [error]);
    /* The appearance the reader chose, which the root layout's script would
       have applied before paint. */
    useEffect(() => {
        try {
            const chosen = localStorage.getItem(PREFIX + ":theme");
            if (chosen === "light" || chosen === "dark") document.documentElement.dataset.theme = chosen;
        } catch { /* no storage: the system's appearance stands */ }
    }, []);
    return (
        <html lang="en">
            <body>
                <title>Lemma</title>
                <style dangerouslySetInnerHTML={{ __html: STYLE }} />
                <main className="kstate">
                    <PageBroke error={error} digest={error?.digest} />
                </main>
            </body>
        </html>
    );
}
