"use client";

import { useEffect, useRef, type CSSProperties, type ReactNode } from "react";
import k from "./kit.module.css";

/** A small, looping moment of the product, told in beats.
 *
 *  A vignette is markup in the product's look plus CSS that says what each
 *  beat shows. This component only keeps time: while the vignette is on
 *  screen it switches on `data-b1`, `data-b2`… one after another (each beat
 *  stays on once reached, so CSS reads "from beat 3 on" as `[data-b3]`),
 *  holds the last frame, then clears them and starts again.
 *
 *  Off screen, it stops. With reduced motion every beat is on from the start
 *  and nothing moves, so the visitor sees the finished frame: write each
 *  vignette so its last beat is a complete, readable picture.
 *
 *  `beats[0]` is how long the opening frame shows before beat 1; `beats[n]`
 *  how long beat n shows before beat n + 1. The last beat is held for `hold`.
 *
 *  It is drawn at one size, `width` × `height` px, and scaled to whatever box
 *  it is given: position things in those pixels.
 *
 *  On a phone the whole canvas would shrink to half size and its text with
 *  it, so a vignette can name the strip of its canvas that carries the
 *  story, `phone: { x, width }`. When the whole canvas would be drawn below
 *  `SMALLEST` of its size, it shows that strip instead, scaled to fill the
 *  box, and leaves the rest out. Keyed on the scale rather than a width, so
 *  a box just under some breakpoint never blows a strip up past life size. */
const SMALLEST = 0.6;

export function Vignette({ beats, hold = 2600, label, width = 640, height = 400, phone, className, children }: {
    beats: number[];
    hold?: number;
    label: string;
    width?: number;
    height?: number;
    phone?: { x: number; width: number };
    className?: string;
    children: ReactNode;
}) {
    const root = useRef<HTMLDivElement>(null);
    const total = beats.length;

    /* The scale the canvas is drawn at, and on a phone the strip it shows,
       kept in step with the box. */
    useEffect(() => {
        const node = root.current;
        if (!node) return;
        const fit = new ResizeObserver(([entry]) => {
            const box = entry.contentRect.width;
            const strip = phone && box / width < SMALLEST ? phone : { x: 0, width };
            const scale = box / strip.width;
            node.style.setProperty("--v-scale", String(scale));
            node.style.setProperty("--v-shift", -strip.x * scale + "px");
            node.style.aspectRatio = `${strip.width} / ${height}`;
        });
        fit.observe(node);
        return () => fit.disconnect();
    }, [width, height, phone]);

    useEffect(() => {
        const node = root.current;
        if (!node) return;
        const show = (count: number) => {
            for (let beat = 1; beat <= total; beat++) {
                if (beat <= count) node.setAttribute("data-b" + beat, "");
                else node.removeAttribute("data-b" + beat);
            }
        };
        /* `?beat=3` holds every vignette on the page at beat 3, still: how
           the capture script and a reviewer look at one beat at a time. */
        const held = Number(new URLSearchParams(window.location.search).get("beat"));
        if (window.location.search.includes("beat=") && Number.isFinite(held)) {
            show(Math.max(0, Math.min(total, held)));
            return;
        }
        if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
            show(total);
            node.setAttribute("data-still", "");
            return;
        }
        let timer = 0;
        let reached = 0;
        let playing = false;
        const next = () => {
            if (!playing) return;
            if (reached < total) {
                reached += 1;
                show(reached);
                timer = window.setTimeout(next, reached < total ? beats[reached] : hold);
            } else {
                reached = 0;
                show(0);
                timer = window.setTimeout(next, beats[0]);
            }
        };
        const seen = new IntersectionObserver(([entry]) => {
            if (entry.isIntersecting && !playing) {
                playing = true;
                timer = window.setTimeout(next, beats[0]);
            } else if (!entry.isIntersecting && playing) {
                playing = false;
                window.clearTimeout(timer);
            }
        }, { threshold: 0.35 });
        seen.observe(node);
        return () => { seen.disconnect(); window.clearTimeout(timer); };
    }, [beats, hold, total]);

    return (
        <div ref={root} className={`${k.vignette} ${className ?? ""}`} style={{ aspectRatio: `${width} / ${height}` } as CSSProperties} role="img" aria-label={label}>
            <div className={k.canvas} style={{ width, height }}>{children}</div>
        </div>
    );
}
