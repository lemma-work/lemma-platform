"use client";

import type { CSSProperties } from "react";
import { markTint } from "@/shell/mark";
import { DRAWINGS, drawingOf } from "./app-drawings";

export { DRAWINGS, drawingOf, sceneSlug } from "./app-drawings";

/** An app's mark: a line drawing of what it is, on its own tint, in a
 *  circle. An app carries no icon of its own, so the drawing is read from
 *  its name -- a mail app gets an envelope, a deal app a pipeline -- and the
 *  tint from the same hash a Lemma's comes from, so two apps never look the
 *  same even when their names say nothing a drawing could show. */
export function AppMark({ name, label, size = 20, className }: { name: string; label?: string; size?: number; className?: string }) {
    const tint = markTint("app:" + name);
    const stroke = Math.max(10, Math.round(size * 0.58));
    return (
        <span
            className={"ox-appmark" + (className ? " " + className : "")}
            style={{ width: size, height: size, background: tint.background, color: tint.color } as CSSProperties}
            aria-hidden="true"
        >
            <svg width={stroke} height={stroke} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
                <path d={DRAWINGS[drawingOf(name, label)]} />
            </svg>
        </span>
    );
}

/** An app with no picture of itself yet: its tint, its drawing large, its
 *  name -- so a row of them still tells one from another. */
export function AppPoster({ name, label }: { name: string; label: string }) {
    const tint = markTint("app:" + name);
    return (
        <span className="ox-appposter" style={{ background: tint.background, color: tint.color }} aria-hidden="true">
            <svg width={34} height={34} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d={DRAWINGS[drawingOf(name, label)]} />
            </svg>
            <span className="ox-appposter__t">{label}</span>
        </span>
    );
}
