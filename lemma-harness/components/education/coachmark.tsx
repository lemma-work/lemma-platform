'use client';

import { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';

import { Button } from '@/components/ui/button';
import { useEducationEnabled } from '@/lib/education/use-education-audience';
import { TOURS, type TourDefinition, type TourStep } from '@/lib/education/tours';
import { useEducationKey, useMarkEducation } from '@/lib/education/use-education-state';

const SETTLE_DELAY_MS = 700;
const RESOLVE_TIMEOUT_MS = 30_000;
const SPOTLIGHT_PADDING = 8;
const CARD_WIDTH = 332;
const CARD_GAP = 12;

interface TourLayerProps {
    tour: TourDefinition['id'];
}

/**
 * First-run coachmarks for a screen. Mount once on the host screen; it arms
 * itself when the matching `coachmarks:*` education key is unset, resolves
 * anchors from the live DOM (silently skipping missing ones), and persists
 * completion or skip. Un-setting the key (help menu "Replay feature tours")
 * re-arms it automatically.
 */
export function TourLayer({ tour }: TourLayerProps) {
    const definition = TOURS[tour];
    const educationKey = `coachmarks:${tour}`;
    const seen = useEducationKey(educationKey) !== undefined;
    const enabled = useEducationEnabled();
    const armable = enabled && !seen;
    const markEducation = useMarkEducation();

    const [steps, setSteps] = useState<TourStep[] | null>(null);
    const [stepIndex, setStepIndex] = useState(0);
    const [targetRect, setTargetRect] = useState<DOMRect | null>(null);

    // Arm once the anchors are in the DOM. Anchors can mount well after this
    // layer (tab switches, slow compiles), so we observe mutations rather than
    // polling a fixed window — and settle briefly first so layout is stable.
    useEffect(() => {
        if (!armable) return;
        let resolved = false;
        let observer: MutationObserver | null = null;

        const tryResolve = () => {
            if (resolved) return true;
            const present = definition.steps.filter((step) => document.querySelector(step.anchor));
            if (present.length === 0) return false;
            resolved = true;
            setSteps(present);
            setStepIndex(0);
            return true;
        };

        const settleTimer = setTimeout(() => {
            if (tryResolve()) return;
            observer = new MutationObserver(() => {
                if (tryResolve() && observer) observer.disconnect();
            });
            observer.observe(document.body, { childList: true, subtree: true });
        }, SETTLE_DELAY_MS);

        const timeoutTimer = setTimeout(() => observer?.disconnect(), RESOLVE_TIMEOUT_MS);

        return () => {
            clearTimeout(settleTimer);
            clearTimeout(timeoutTimer);
            observer?.disconnect();
        };
    }, [definition, armable]);

    const overflowed = steps !== null && stepIndex >= steps.length;
    const activeStep = steps && armable && !overflowed ? steps[stepIndex] : null;

    useEffect(() => {
        if (!activeStep) return;
        const measure = () => {
            const element = document.querySelector(activeStep.anchor);
            setTargetRect(element ? element.getBoundingClientRect() : null);
        };
        const element = document.querySelector(activeStep.anchor);
        element?.scrollIntoView({ block: 'nearest', behavior: 'auto' });
        const frame = requestAnimationFrame(measure);
        window.addEventListener('resize', measure);
        window.addEventListener('scroll', measure, true);
        return () => {
            cancelAnimationFrame(frame);
            window.removeEventListener('resize', measure);
            window.removeEventListener('scroll', measure, true);
        };
    }, [activeStep]);

    // Persisting flips the `seen` flag through the external store, which hides
    // the tour on the next render — no local setState needed here.
    const finish = useCallback(() => {
        markEducation(educationKey);
    }, [educationKey, markEducation]);

    useEffect(() => {
        if (!activeStep) return;
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === 'Escape') finish();
        };
        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [activeStep, finish]);

    // If the index ever runs past the last step, persist completion rather than
    // silently vanishing (which would re-fire the tour on the next visit).
    useEffect(() => {
        if (overflowed && !seen) finish();
    }, [overflowed, seen, finish]);

    if (!activeStep || !targetRect || !steps) return null;

    const isLast = stepIndex === steps.length - 1;
    const top = targetRect.top - SPOTLIGHT_PADDING;
    const left = targetRect.left - SPOTLIGHT_PADDING;
    const width = targetRect.width + SPOTLIGHT_PADDING * 2;
    const height = targetRect.height + SPOTLIGHT_PADDING * 2;

    const spaceBelow = window.innerHeight - (top + height);
    const cardTop = spaceBelow > 220 ? top + height + CARD_GAP : Math.max(16, top - CARD_GAP - 200);
    const cardLeft = Math.min(Math.max(16, left), window.innerWidth - CARD_WIDTH - 16);

    return createPortal(
        <div className="fixed inset-0 z-[1200]" role="dialog" aria-label={`${activeStep.title} — feature tour`}>
            <div
                aria-hidden="true"
                className="absolute rounded-xl transition-all duration-300"
                /* eslint-disable-next-line no-restricted-syntax -- spotlight position is runtime geometry measured from the anchor */
                style={{
                    top,
                    left,
                    width,
                    height,
                    boxShadow: '0 0 0 9999px color-mix(in srgb, var(--bg-canvas) 62%, transparent)',
                    border: '1.5px solid var(--delight)',
                }}
            />
            <div
                className="absolute rounded-lg border border-[var(--card-border)] bg-[var(--card-bg)] p-4 shadow-[var(--shadow-lg)]"
                /* eslint-disable-next-line no-restricted-syntax -- card position is runtime geometry measured from the anchor */
                style={{ top: cardTop, left: cardLeft, width: CARD_WIDTH }}
            >
                <h3 className="text-sm font-semibold text-[var(--text-primary)]">{activeStep.title}</h3>
                <p className="mt-1.5 text-sm leading-6 text-[var(--text-secondary)]">{activeStep.body}</p>
                <div className="mt-3 flex items-center justify-between gap-3">
                    <div className="flex items-center gap-1.5" aria-label={`Step ${stepIndex + 1} of ${steps.length}`}>
                        {steps.map((step, index) => (
                            <span
                                key={step.anchor}
                                className={
                                    index === stepIndex
                                        ? 'h-1.5 w-4 rounded-full bg-[var(--delight)]'
                                        : 'h-1.5 w-1.5 rounded-full bg-[var(--border-strong,var(--border-subtle))]'
                                }
                            />
                        ))}
                    </div>
                    <div className="flex items-center gap-2">
                        <Button size="xs" variant="quiet" onClick={finish}>
                            Skip tour
                        </Button>
                        <Button
                            size="xs"
                            variant="secondary"
                            onClick={() => (isLast ? finish() : setStepIndex((index) => index + 1))}
                        >
                            {isLast ? 'Done' : 'Next'}
                        </Button>
                    </div>
                </div>
            </div>
        </div>,
        document.body
    );
}
