"use client";

import type { ReactNode } from "react";
import { LemmaLogo } from "@/ui/icons";
import { productFace } from "@/ui/product-face";
import { TeammateFace } from "@/space/teammate-face";
import { STEP_NAMES, type ArrivalStepId } from "./steps";

/** What a Lemma is, in the one sentence every door opens on. Somebody
 *  arriving has never seen one, and every screen after this one asks them to
 *  pick, join or fill one. */
export const LEMMA_IS =
    "A Lemma is a shared space for part of your work: its people, chats, pages, tables and apps, with an AI that does the work in them.";

/** The way in, drawn the same on every door: the mark top left, the steps
 *  this door walks along the top, and one column under them. `inline` is the
 *  same column inside a page that already has a frame of its own -- a
 *  plugin's install panel -- with no bar and no height of its own. */
export function ArrivalShell({ steps, at, inline = false, label, children }: {
    steps: readonly ArrivalStepId[];
    at: ArrivalStepId | null;
    inline?: boolean;
    /** What the column is about, for a screen reader's landmark. */
    label: string;
    children: ReactNode;
}) {
    const now = at ? steps.indexOf(at) : -1;
    return (
        <div className={productFace.variable + " ar" + (inline ? " ar--inline" : "")}>
            {!inline && (
                <header className="ar__bar">
                    <LemmaLogo />
                    {steps.length > 1 && (
                        <ol className="ar__steps" aria-label="Steps">
                            {steps.map((step, index) => (
                                <li
                                    key={step}
                                    data-state={index < now ? "done" : index === now ? "now" : "next"}
                                    aria-current={index === now ? "step" : undefined}
                                >
                                    {STEP_NAMES[step]}
                                </li>
                            ))}
                        </ol>
                    )}
                </header>
            )}
            <main className="ar__col" aria-label={label}>{children}</main>
        </div>
    );
}

/** One thing on a step: an organization or a Lemma, what it is in a line,
 *  and the one thing to do about it, if any. */
export function ArrivalRow({ mark, name, tag, line, children }: {
    mark: ReactNode;
    name: string;
    /** A word set beside the name, such as "Everyone". */
    tag?: string;
    line: ReactNode;
    children?: ReactNode;
}) {
    return (
        <li className="ar__row">
            {mark}
            <span className="ar__said">
                <span className="ar__name">{name}{tag && <span className="ar__mark">{tag}</span>}</span>
                <span className="ar__line">{line}</span>
            </span>
            {children}
        </li>
    );
}

/** A Lemma's own face, at the size every row uses. */
export function LemmaMark({ id, name, iconUrl = null }: { id: string; name: string; iconUrl?: string | null }) {
    return <TeammateFace pod={{ id, name, iconUrl }} size={36} />;
}

/** The organization's mark: a building, since an organization has no face. */
export function OrgMark() {
    return (
        <span className="ar__tile" aria-hidden="true">
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 20V6l7-3v17M11 9h7v11M7 8v.01M7 12v.01M7 16v.01M14 13h1M14 16h1M3 20h18" />
            </svg>
        </span>
    );
}

/** Already in: said, not offered. */
export function InMark() {
    return (
        <span className="ar__in">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
            In
        </span>
    );
}
