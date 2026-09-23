"use client";

import React from "react";
import { cn } from "@/lib/utils";

type LogoSize = "xs" | "sm" | "md" | "lg" | "xl";
type LogoVariant = "mark-wordmark" | "wordmark-only" | "mark-only";

interface LogoProps {
    size?: LogoSize;
    variant?: LogoVariant;
    className?: string;
}

export function LemmaMark({
    size = "md",
    className,
}: {
    size?: LogoSize;
    className?: string;
}) {
    return (
        <span
            className={cn("lemma-mark", `lemma-mark-${size}`, className)}
            aria-hidden="true"
        >
            {[0, 1, 2].map((i) => (
                <span
                    key={i}
                    className={cn("lemma-mark-bar", `lemma-mark-bar-${i}`)}
                />
            ))}
        </span>
    );
}

export function Logo({
    size = "md",
    variant = "mark-wordmark",
    className,
}: LogoProps) {
    return (
        <span
            className={cn("lemma-logo", `lemma-logo-${size}`, className)}
        >
            {variant !== "wordmark-only" && <LemmaMark size={size} />}

            {variant !== "mark-only" && (
                <span className={cn("lemma-logo-wordmark", `lemma-logo-wordmark-${size}`)}>
                    Lemma
                </span>
            )}
        </span>
    );
}
