"use client";

import { useState, type ReactNode } from "react";

/** Where an app's cover lives: on the app's own origin, which answers with
 *  the cover its build ships or one it draws from the app's name. Null for an
 *  app with no origin of its own, such as the sample. */
export function appCoverUrl(appUrl: string): string | null {
    if (!/^https?:\/\//.test(appUrl)) return null;
    try {
        return new URL("/.lemma/cover.png", appUrl).toString();
    } catch {
        return null;
    }
}

/** An app's cover, or `fallback` when there is none to show. A private app's
 *  origin answers only someone who has opened it, so a cover that will not
 *  load is expected rather than an error. */
export function AppCover({ url, fallback }: { url: string; fallback: ReactNode }) {
    const [failed, setFailed] = useState(false);
    const src = appCoverUrl(url);
    if (!src || failed) return <>{fallback}</>;
    return <img className="app-cover" src={src} alt="" loading="lazy" decoding="async" onError={() => setFailed(true)} />;
}
