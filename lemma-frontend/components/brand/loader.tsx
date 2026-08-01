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
export function PageLoader() {
    return (
        <div
            className="lemma-page-loader flex min-h-screen items-center justify-center bg-transparent"
            role="status"
            aria-label="Loading Lemma"
            aria-live="polite"
        >
            <div className="lemma-page-loader-mark-shell">
                <LemmaMark size="lg" className="lemma-page-loader-mark" />
            </div>
        </div>
    );
}
