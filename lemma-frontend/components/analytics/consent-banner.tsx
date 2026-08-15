'use client';

import { useSyncExternalStore } from 'react';
import Link from 'next/link';

import { Button } from '@/components/ui/button';
import { applyAnalyticsPersistence } from '@/lib/analytics/client';
import {
    consentServerSnapshot,
    readConsentDecision,
    recordConsentDecision,
    subscribeToConsent,
} from '@/lib/analytics/consent';
import { config, isLocalDeployment } from '@/lib/config';

/**
 * Asks once whether analytics may persist to this device.
 *
 * The client is already running when this renders, in memory-only mode — the
 * session is measurable, nothing is written to the device. Accepting upgrades
 * persistence in place, which keeps the anonymous id established before the
 * answer, so a landing→signup funnel is not broken by the act of consenting.
 *
 * Never rendered where analytics does not run at all: no key, or a Desktop-local
 * install. Asking for consent to something that is not happening is worse than
 * not asking.
 */
export function ConsentBanner() {
    // Read through the store rather than an effect: the decision lives in
    // localStorage, which does not exist during SSR, and the server snapshot
    // keeps the banner hidden until hydration says otherwise — so nobody who has
    // already answered sees it flash.
    const decision = useSyncExternalStore(
        subscribeToConsent,
        readConsentDecision,
        consentServerSnapshot,
    );

    const analyticsRuns = !isLocalDeployment() && Boolean(config.ANALYTICS_KEY);
    if (!analyticsRuns || decision !== 'unanswered') return null;

    const decide = (choice: 'granted' | 'denied') => {
        recordConsentDecision(choice);
        applyAnalyticsPersistence(choice === 'granted');
    };

    return (
        <div
            role="dialog"
            aria-live="polite"
            aria-label="Analytics preferences"
            className="lemma-pop-card fixed bottom-4 left-4 z-[1200] max-w-sm p-4"
        >
            <p className="text-sm text-[var(--text-primary)]">
                We measure how Lemma is used so we can make it better. Nothing you build —
                records, files or agent conversations — is ever sent.
            </p>
            <p className="mt-2 text-xs text-[var(--text-secondary)]">
                Accepting stores a small identifier on this device.{' '}
                <Link href="/privacy" className="underline underline-offset-2">
                    How we handle data
                </Link>
                .
            </p>
            <div className="mt-3 flex gap-2">
                <Button size="sm" onClick={() => decide('granted')}>
                    Accept
                </Button>
                <Button size="sm" variant="secondary" onClick={() => decide('denied')}>
                    Decline
                </Button>
            </div>
        </div>
    );
}
