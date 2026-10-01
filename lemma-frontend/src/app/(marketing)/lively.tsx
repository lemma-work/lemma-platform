"use client";

import { useEffect } from "react";

/** A little life on the marketing pages, and only a little.
 *
 *  Anything marked `data-rise` rises in once as it arrives, and a margin
 *  note's arrow draws itself; recordings marked `data-loop` play while they
 *  are on screen and stop when they leave. Nothing follows the scroll after
 *  that, and with reduced motion none of it happens.
 *
 *  The page is drawn whole and only hidden once this has run (`data-motion`
 *  on the root), so a visitor whose script never loads still sees all of it. */
export function Lively() {
    useEffect(() => {
        const calm = window.matchMedia("(prefers-reduced-motion: reduce)");
        if (calm.matches) return;
        const root = document.documentElement;
        root.dataset.motion = "on";
        const seen = new IntersectionObserver((entries) => {
            for (const entry of entries) {
                const node = entry.target as HTMLElement;
                if (entry.isIntersecting) node.dataset.in = "";
                const video = node instanceof HTMLVideoElement ? node : null;
                if (video) {
                    if (entry.isIntersecting) void video.play().catch(() => undefined);
                    else video.pause();
                }
            }
        }, { threshold: 0.25 });
        document.querySelectorAll("[data-rise], video[data-loop]").forEach((node) => seen.observe(node));
        return () => {
            seen.disconnect();
            delete root.dataset.motion;
        };
    }, []);
    return null;
}
