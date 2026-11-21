"use client";

import { useEffect, useState, type CSSProperties } from "react";

/** Loading, by the rules in design.md ("States"): nothing for the first
 *  moment, so a quick read never flashes; then the shape of what is coming,
 *  under the page's real header; words only once it is slow. */

/** Whether `active` has lasted `after` ms: false at first, true once it has,
 *  and false again as soon as it stops. */
export function useLasted(active: boolean, after: number): boolean {
    const [lasted, setLasted] = useState(false);
    useEffect(() => {
        if (!active) {
            setLasted(false);
            return;
        }
        const timer = window.setTimeout(() => setLasted(true), after);
        return () => window.clearTimeout(timer);
    }, [active, after]);
    return active && lasted;
}

/** Shown after 300ms of loading: a quicker read draws nothing at all. */
export const SHOW_AFTER_MS = 300;
/** Said in words after 2.5s: "Still reading … It's slower than usual." */
export const SLOW_AFTER_MS = 2500;

/** One grey shape. Widths and heights are any CSS length. */
export function Sk({ w, h, r, style }: { w?: string | number; h?: string | number; r?: string | number; style?: CSSProperties }) {
    return <span className="ksk" aria-hidden="true" style={{ width: w, height: h, borderRadius: r, ...style }} />;
}

const WIDTHS = ["64%", "48%", "72%", "40%", "58%", "66%", "52%", "44%"];

/** A list loading: a mark, a name and its line, and a figure at the end,
 *  in rows like the list's own. `label` is what a screen reader hears. */
export function SkeletonRows({ label, rows = 6, slow }: { label: string; rows?: number; slow?: string | null }) {
    const shown = useLasted(true, SHOW_AFTER_MS);
    return (
        <div className="kskrows" role="status" aria-label={label}>
            {shown && Array.from({ length: rows }, (_, index) => (
                <div className="kskrows__row" key={index}>
                    <Sk w={32} h={32} r={9} />
                    <span className="kskrows__words"><Sk w={WIDTHS[index % WIDTHS.length]} /><Sk w="30%" h={9} /></span>
                    <Sk w={56} h={10} />
                </div>
            ))}
            <SlowLine words={slow} />
        </div>
    );
}

/** Cards loading: a cover and a name under it, in the grid's own columns. */
export function SkeletonCards({ label, count = 6, slow }: { label: string; count?: number; slow?: string | null }) {
    const shown = useLasted(true, SHOW_AFTER_MS);
    return (
        <div className="kskcards-wrap" role="status" aria-label={label}>
            {shown && (
                <div className="kskcards">
                    {Array.from({ length: count }, (_, index) => (
                        <span className="kskcards__card" key={index}>
                            <Sk r={14} style={{ aspectRatio: "16 / 10", height: "auto" }} />
                            <Sk w={WIDTHS[index % WIDTHS.length]} />
                        </span>
                    ))}
                </div>
            )}
            <SlowLine words={slow} />
        </div>
    );
}

/** The words a slow load earns: shown after 2.5s, and only then. */
export function SlowLine({ words }: { words?: string | null }) {
    const slow = useLasted(Boolean(words), SLOW_AFTER_MS);
    if (!slow || !words) return null;
    return <p className="kslow">{words}</p>;
}
