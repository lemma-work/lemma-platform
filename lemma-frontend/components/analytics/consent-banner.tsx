'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';

import { Button } from '@/components/ui/button';
import { applyAnalyticsPersistence } from '@/lib/analytics/client';
import { readConsentDecision, recordConsentDecision } from '@/lib/analytics/consent';
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
    // `undefined` means "not decided yet on the client" — this must not render
    // during SSR, where localStorage does not exist and every visitor would get
    // a flash of the banner regardless of their earlier answer.
    const [visible, setVisible] = useState<boolean | undefined>(undefined);

    useEffect(() => {
        if (isLocalDeployment() || !config.ANALYTICS_KEY) {
            setVisible(false);
            return;
        }
        setVisible(readConsentDecision() === 'unanswered');
    }, []);

    if (!visible) return null;

    const decide = (decision: 'granted' | 'denied') => {
        recordConsentDecision(decision);
        applyAnalyticsPersistence(decision === 'granted');
        setVisible(false);
    };

    return (
        <div
            role="dialog"
            aria-live="polite"
            aria-label="Analytics preferences"
            className="fixed bottom-4 left-4 z-50 max-w-sm rounded-lg border border-border bg-background p-4 shadow-lg"
        >
            <p className="text-sm text-foreground">
                We measure how Lemma is used so we can make it better. Nothing you build —
                records, files or agent conversations — is ever sent.
            </p>
            <p className="mt-2 text-xs text-muted-foreground">
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
