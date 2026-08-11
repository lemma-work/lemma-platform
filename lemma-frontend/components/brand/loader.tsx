"use client";

import React from "react";
import { LemmaMark } from "@/components/brand/logo";
import { cn } from "@/lib/utils";

type LoaderSize = "xs" | "sm" | "md" | "lg";

/**
 * StepLoader — Loader A: "Proof Steps"
 * Five vertical bars that rise sequentially, each one a step in a proof.
 * Use for: in-page loading states, list skeletons, section transitions.
 */
export function StepLoader({
    size = "md",
    className,
}: {
    size?: LoaderSize;
    className?: string;
}) {
    return (
        <span
            className={cn("lemma-step-loader", `lemma-step-loader-${size}`, className)}
            aria-label="Loading…"
            role="status"
        >
            {[0, 1, 2, 3, 4].map((i) => (
                <span
                    key={i}
                    className="lemma-step-bar"
                />
            ))}
        </span>
    );
}

export function InlineLoader({
    size = "sm",
    label = "Working",
    className,
}: {
    size?: LoaderSize;
    label?: string;
    className?: string;
}) {
    return (
        <span className={cn("inline-flex items-center gap-2 text-[var(--text-secondary)]", className)}>
            <StepLoader size={size} />
            <span>{label}</span>
        </span>
    );
}

/*
 * `LoadingState` and `LoadingSkeleton` used to live here: a centred panel with
 * a caption and a generic bar-chart of placeholders. They were removed because
 * they matched no screen in the product — a region that settles into a card
 * grid was showing three grey rectangles in a box, so data arrival always
 * re-flowed the page. Skeletons now live next to the shapes they imitate, in
 * `components/shared/loading`, and are built from the same class names as the
 * real thing.
 */

/**
 * WordmarkLoader — Loader D: "Wordmark Build"
 * The letters of "lemma" arrive one by one, left to right.
 * Use for: full-page loading splash, route transitions.
 */
export function WordmarkLoader({
    size = "md",
    className,
}: {
    size?: "sm" | "md" | "lg";
    className?: string;
}) {
    const letters = ["l", "e", "m", "m", "a"];

    return (
        <span
            className={cn("lemma-wordmark-loader", `lemma-wordmark-loader-${size}`, className)}
            aria-label="lemma — loading…"
            role="status"
        >
            {letters.map((letter, i) => (
                <span
                    key={i}
                    className="lemma-wordmark-letter"
                >
                    {letter}
                </span>
            ))}
        </span>
    );
}

/**
 * PageLoader — full-page centered loading splash.
 * Used for route-level Suspense boundaries.
 */
/**
 * How long a mark alone is an acceptable answer to "what is happening".
 *
 * Every route that is not ready yet renders this, so when something upstream
 * stalls it is the only thing on screen — and it says nothing, forever. A first
 * run that never reached account creation looked exactly like one that was
 * about to. Past this point the loader stops implying progress it cannot see and
 * offers the user something to do.
 */
const PAGE_LOADER_PATIENCE_MS = 10_000;

export function PageLoader() {
    const [waitedTooLong, setWaitedTooLong] = React.useState(false);

    React.useEffect(() => {
        const timer = window.setTimeout(
            () => setWaitedTooLong(true),
            PAGE_LOADER_PATIENCE_MS,
        );
        return () => window.clearTimeout(timer);
    }, []);

    return (
        <div
            className="lemma-page-loader flex min-h-screen flex-col items-center justify-center gap-6 bg-transparent"
            role="status"
            aria-label="Loading Lemma"
            aria-live="polite"
        >
            <div className="lemma-page-loader-mark-shell">
                <LemmaMark size="lg" className="lemma-page-loader-mark" />
            </div>
            {waitedTooLong ? (
                <div className="flex flex-col items-center gap-3 text-center">
                    <p className="text-sm text-[var(--text-tertiary)]">
                        Still starting Lemma. This can take a while the first time.
                    </p>
                    <button
                        type="button"
                        className="rounded-md border border-[var(--border-subtle)] px-3 py-1.5 text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                        onClick={() => window.location.reload()}
                    >
                        Reload
                    </button>
                </div>
            ) : null}
        </div>
    );
}
