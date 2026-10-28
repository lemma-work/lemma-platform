"use client";
import { useEffect, useRef, useSyncExternalStore } from "react";
import { usePathname } from "next/navigation";
import {
    startAnalytics,
    capturePageview,
    applyAnalyticsPersistence,
} from "./analytics/client";
import { isNewView } from "./analytics/route-template";
import {
    readConsentDecision,
    recordConsentDecision,
    subscribeToConsent,
    consentServerSnapshot,
} from "./analytics/consent";
import { isAnalyticsDocument } from "./analytics/document";
import { config, isLocalDeployment } from "./config";
export function Analytics() {
    const pathname = usePathname();
    const hydrated = useSyncExternalStore(
        () => () => {},
        () => true,
        () => false,
    );
    const consent = useSyncExternalStore(
        subscribeToConsent,
        readConsentDecision,
        consentServerSnapshot,
    );
    /* The address the last pageview was decided against. Both frames rewrite
       their own address as they settle, and only a move to a new place is a
       pageview (`isNewView`). Updated on every change, counted or not, so the
       next move is judged from where the page actually is. No cancel on
       cleanup: a remount with the same address is not a new view, and a
       cancelled first one would be no view at all. */
    const viewed = useRef<string | null>(null);
    useEffect(() => {
        if (!pathname) return;
        const previous = viewed.current;
        viewed.current = pathname;
        if (!isNewView(previous, pathname)) return;
        void startAnalytics().then(capturePageview);
    }, [pathname]);
    useEffect(() => {
        applyAnalyticsPersistence(consent === "granted");
    }, [consent]);
    if (
        !hydrated ||
        !isAnalyticsDocument() ||
        !config.ANALYTICS_KEY ||
        isLocalDeployment() ||
        consent !== "unanswered"
    )
        return null;
    return (
        <aside className="site-consent" aria-label="Analytics preferences">
            <p>
                Allow analytics storage to help us understand how Lemma is used?{" "}
                <a href="/privacy">Privacy policy</a>
            </p>
            <div className="site-actions">
                <button
                    className="btn"
                    onClick={() => recordConsentDecision("denied")}
                >
                    No thanks
                </button>
                <button
                    className="btn btn--primary"
                    onClick={() => recordConsentDecision("granted")}
                >
                    Allow
                </button>
            </div>
        </aside>
    );
}
