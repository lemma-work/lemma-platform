"use client";
import { useSyncExternalStore } from "react";
import {
    recordConsentDecision,
    readConsentDecision,
    subscribeToConsent,
    consentServerSnapshot,
} from "./analytics/consent";
export function PrivacyChoices() {
    const decision = useSyncExternalStore(
        subscribeToConsent,
        readConsentDecision,
        consentServerSnapshot,
    );
    return (
        <section className="site-card">
            <h2>Analytics storage preferences</h2>
            <p>Current choice: {decision}. You can change this at any time.</p>
            <div className="site-actions">
                <button
                    className="btn"
                    onClick={() => recordConsentDecision("denied")}
                >
                    Disable analytics storage
                </button>
                <button
                    className="btn"
                    onClick={() => recordConsentDecision("granted")}
                >
                    Allow analytics storage
                </button>
            </div>
        </section>
    );
}
