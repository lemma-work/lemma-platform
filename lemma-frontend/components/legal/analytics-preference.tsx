'use client';

import { useSyncExternalStore } from 'react';

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
 * The analytics switch, on the page that explains analytics.
 *
 * A policy that says "you can change your mind" and then gives you nowhere to
 * do it is the same policy without the sentence. The banner asks once and never
 * comes back, so this is the only place the answer can be revisited — which
 * makes it part of the document, not a decoration on it.
 *
 * It reports three genuinely different situations rather than flattening them
 * into on/off: nobody has answered yet, someone answered, or analytics does not
 * run on this deployment at all and there is nothing here to switch.
 */

const alwaysTrue = () => true;
const alwaysFalse = () => false;

type Choice = 'granted' | 'denied';

export function AnalyticsPreference() {
    const decision = useSyncExternalStore(
        subscribeToConsent,
        readConsentDecision,
        consentServerSnapshot,
    );
    // `config` resolves differently on the server and in the browser — a
    // self-hosted deployment injects its values at runtime — so the branch on
    // whether analytics runs at all has to wait for hydration rather than be
    // decided during SSR and then contradicted.
    const hydrated = useSyncExternalStore(subscribeToConsent, alwaysTrue, alwaysFalse);
    const analyticsRuns = hydrated && !isLocalDeployment() && Boolean(config.ANALYTICS_KEY);

    const choose = (choice: Choice) => {
        recordConsentDecision(choice);
        applyAnalyticsPersistence(choice === 'granted');
    };

    return (
        <div className="surface-panel px-6 py-6 sm:px-8 sm:py-7">
            <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between lg:gap-10">
                <div className="min-w-0 max-w-[46ch]">
                    <p className="type-eyebrow text-[var(--text-tertiary)]">Your setting</p>
                    <h2 className="mt-3 [font-family:var(--font-landing-serif)] text-2xl font-normal leading-tight text-[var(--text-primary)]">
                        Analytics in this browser
                    </h2>
                    <p className="mt-3 text-sm leading-6 text-[var(--text-secondary)]">
                        <StatusCopy hydrated={hydrated} analyticsRuns={analyticsRuns} decision={decision} />
                    </p>
                </div>

                {analyticsRuns ? (
                    <div className="flex flex-none flex-col items-start gap-3 lg:items-end">
                        <div
                            role="group"
                            aria-label="Analytics in this browser"
                            className="inline-flex items-center gap-2"
                        >
                            <Button
                                size="sm"
                                variant={decision === 'granted' ? 'primary' : 'secondary'}
                                aria-pressed={decision === 'granted'}
                                onClick={() => choose('granted')}
                            >
                                Allow
                            </Button>
                            <Button
                                size="sm"
                                variant={decision === 'denied' ? 'primary' : 'secondary'}
                                aria-pressed={decision === 'denied'}
                                onClick={() => choose('denied')}
                            >
                                No thanks
                            </Button>
                        </div>
                        <p className="text-xs leading-6 text-[var(--text-tertiary)] lg:text-right">
                            Kept on this device, not on your account.
                        </p>
                    </div>
                ) : null}
            </div>
        </div>
    );
}

function StatusCopy({
    hydrated,
    analyticsRuns,
    decision,
}: {
    hydrated: boolean;
    analyticsRuns: boolean;
    decision: ReturnType<typeof readConsentDecision>;
}) {
    if (!hydrated) {
        return <>Checking what this browser is set to.</>;
    }
    if (!analyticsRuns) {
        return (
            <>
                Analytics is not running here. This build has no analytics key configured — which is
                the case on every self-hosted server and on Lemma Desktop in local mode — so nothing
                is being collected and there is nothing to switch off.
            </>
        );
    }
    if (decision === 'granted') {
        return (
            <>
                <Dot tone="on" /> On. One identifier is stored here, so your visits join up into a
                picture of how the product gets used. Turn it off and that identifier is removed, not
                just left to go stale.
            </>
        );
    }
    if (decision === 'denied') {
        return (
            <>
                <Dot tone="off" /> Off. Nothing analytics-related is stored in this browser and every
                visit stays unlinked from the last one.
            </>
        );
    }
    return (
        <>
            <Dot tone="pending" /> Not answered yet. This visit is being measured in memory only —
            nothing has been written to your device, and closing the tab ends it.
        </>
    );
}

const DOT_TONE = {
    on: 'bg-[var(--state-success)]',
    off: 'bg-[var(--text-soft)]',
    pending: 'bg-[var(--state-warning)]',
} as const;

function Dot({ tone }: { tone: keyof typeof DOT_TONE }) {
    return (
        <span
            aria-hidden="true"
            className={`mr-2 inline-block h-1.5 w-1.5 -translate-y-[2px] rounded-full align-middle ${DOT_TONE[tone]}`}
        />
    );
}
