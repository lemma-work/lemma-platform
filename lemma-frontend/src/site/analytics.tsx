"use client";
import { useEffect, useSyncExternalStore } from "react";
import { usePathname } from "next/navigation";
import {
    startAnalytics,
    capturePageview,
    applyAnalyticsPersistence,
} from "./analytics/client";
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
    useEffect(() => {
        let alive = true;
        void startAnalytics().then(() => {
            if (alive && pathname) capturePageview(pathname);
        });
        return () => {
            alive = false;
        };
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
