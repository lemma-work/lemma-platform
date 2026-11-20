"use client";

import Image from "next/image";
import { useEffect, useRef } from "react";
import type { Visual as VisualData, VisualSize } from "../catalog";
import s from "./landing.module.css";

/** A capture of the real app: a close-up, or a short loop that plays while it
 *  is on screen.
 *
 *  Every version is in the page (light and dark, and the phone layout when
 *  there is one) and CSS shows the one that fits, so it follows a visitor's
 *  own theme setting as well as their system's. A hidden picture is lazy and
 *  never fetched; a hidden video never comes into view, so it never plays and,
 *  with `preload="none"`, never loads. With reduced motion a loop stays on its
 *  poster. */
export function Visual({ visual, sizes }: { visual: VisualData; sizes: string }) {
    const ref = useRef<HTMLSpanElement>(null);
    const loop = visual.kind === "loop";

    useEffect(() => {
        const root = ref.current;
        if (!loop || !root) return;
        const calm = window.matchMedia("(prefers-reduced-motion: reduce)");
        const videos = [...root.querySelectorAll("video")];
        const inView = new Map<Element, boolean>();
        const update = () => {
            for (const video of videos) {
                if (inView.get(video) && !calm.matches) {
                    video.muted = true;
                    void video.play().catch(() => undefined);
                } else video.pause();
            }
        };
        const watch = new IntersectionObserver(
            (entries) => {
                for (const entry of entries) inView.set(entry.target, entry.isIntersecting);
                update();
            },
            { threshold: 0.25 },
        );
        videos.forEach((video) => watch.observe(video));
        calm.addEventListener("change", update);
        return () => {
            watch.disconnect();
            calm.removeEventListener("change", update);
        };
    }, [loop]);

    const themed = (size: VisualSize) =>
        (["light", "dark"] as const).map((theme) => {
            const files = size[theme];
            const className = `${s.capture} ${theme === "light" ? s.light : s.dark}`;
            const box = { aspectRatio: `${size.width} / ${size.height}`, maxWidth: size.width };
            if (!loop) {
                return (
                    <Image
                        key={theme}
                        className={className}
                        style={box}
                        src={files.still}
                        width={size.width}
                        height={size.height}
                        sizes={sizes}
                        alt={visual.alt}
                        loading="lazy"
                    />
                );
            }
            return (
                <video
                    key={theme}
                    className={className}
                    style={box}
                    width={size.width}
                    height={size.height}
                    poster={files.still}
                    muted
                    loop
                    playsInline
                    preload="none"
                    aria-label={visual.alt}
                >
                    {files.webm && <source src={files.webm} type="video/webm" />}
                    {files.mp4 && <source src={files.mp4} type="video/mp4" />}
                </video>
            );
        });

    return (
        <span ref={ref} className={s.visual}>
            {visual.phone ? (
                <>
                    <span className={s.desk}>{themed(visual)}</span>
                    <span className={s.phone}>{themed(visual.phone)}</span>
                </>
            ) : (
                themed(visual)
            )}
        </span>
    );
}
