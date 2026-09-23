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
import { useHydrated } from '@/lib/use-hydrated';

/**
 * Asks once whether analytics may persist to this device.
 *
 * The client is already running when this renders, in memory-only mode — the
 * session is measurable, nothing is written to the device. Accepting upgrades
 * persistence in place, which keeps the anonymous id established before the
 * answer, so a landing→signup funnel is not broken by the act of consenting.
 *
 * The copy says what is actually being asked. "We use cookies to improve your
 * experience" is not a question, and neither was the version of this that led
 * with "we measure how Lemma is used so we can make it better" — that is a
 * statement of our motives, and our motives are not the thing being consented
 * to. What is being consented to is one identifier, in this browser, that makes
 * two visits into one visitor. So that is the sentence.
 *
 * Never rendered where analytics does not run at all: no key, or a Desktop-local
 * install. Asking for consent to something that is not happening is worse than
 * not asking.
 */
export function ConsentBanner() {
    // Read through the store rather than an effect: the decision lives in
    // localStorage, which does not exist during SSR.
    const decision = useSyncExternalStore(
        subscribeToConsent,
        readConsentDecision,
        consentServerSnapshot,
    );

    // The server cannot know the answer, so its snapshot is `unanswered` — the
    // one value that renders this card. Without the gate below, the banner is
    // therefore prerendered into the HTML of every document, for everyone,
    // including the people who accepted months ago, and hydration then removes
    // it. Client-side navigation never shows it, because it never re-renders
    // from the server snapshot; a hard refresh shows the whole slide-up and
    // then swallows it. Same reason the switch on /privacy waits: `config`
    // resolves from `process.env` on the server and `window.__ENV` in the
    // browser, so a runtime-injected key disagrees across that boundary too.
    const hydrated = useHydrated();

    const analyticsRuns = !isLocalDeployment() && Boolean(config.ANALYTICS_KEY);
    if (!hydrated || !analyticsRuns || decision !== 'unanswered') return null;

    const decide = (choice: 'granted' | 'denied') => {
        recordConsentDecision(choice);
        applyAnalyticsPersistence(choice === 'granted');
    };

    return (
        <div
            role="dialog"
            aria-modal="false"
            aria-labelledby="consent-banner-title"
            aria-live="polite"
            className="lemma-pop-card animate-slide-up fixed bottom-4 left-4 right-4 z-[1200] p-5 sm:right-auto sm:max-w-[25rem]"
        >
            <h2
                id="consent-banner-title"
                className="text-sm leading-6 text-[var(--text-primary)]"
            >
                Can we remember this browser?
            </h2>

            <p className="mt-2.5 text-sm leading-6 text-[var(--text-secondary)]">
                We measure which parts of Lemma get used — never what is inside them. No records, no
                files, no conversations, not even their names.
            </p>
            <p className="mt-2.5 text-sm leading-6 text-[var(--text-secondary)]">
                Yes keeps one identifier here, so your visits join up. No leaves nothing on your
                device and every visit stays unlinked. Either way, we only ask once.
            </p>

            <div className="mt-4 flex items-center gap-2">
                <Button size="sm" variant="primary" onClick={() => decide('granted')}>
                    Allow
                </Button>
                {/* Same size, same prominence. A decline styled as a whisper is
                    an answer designed not to be given. */}
                <Button size="sm" variant="secondary" onClick={() => decide('denied')}>
                    No thanks
                </Button>
                <Link
                    href="/privacy#product-analytics"
                    className="ml-auto text-xs text-[var(--text-tertiary)] underline underline-offset-4 transition-colors hover:text-[var(--text-primary)]"
                >
                    What we collect
                </Link>
            </div>
        </div>
    );
}
