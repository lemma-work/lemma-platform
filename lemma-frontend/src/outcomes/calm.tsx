"use client";

import type { ReactNode } from "react";
import { Icon, type IconName } from "./parts";

/** What a page says in place of what it holds: nothing here yet, or
 *  something wrong (design.md, "States").
 *
 *  Drawn under the page's own header, never instead of it, so the reader
 *  still knows where they are. A short title, one or two sentences on what
 *  is still true, and a way forward. `tone="warn"` is for a failure: its mark
 *  is warm and it is announced; a quiet one is for an empty place, a missing
 *  thing, or a door. `art` stands in for the mark where something better
 *  says it -- a Lemma's face, the apps a place will hold. */
export function Calm({ title, children, icon, art, tone = "quiet", actions, note, className }: {
    title: string;
    /** What is still true, and what happens next. */
    children?: ReactNode;
    icon?: IconName;
    art?: ReactNode;
    tone?: "quiet" | "warn";
    /** Buttons, the way forward last. */
    actions?: ReactNode;
    /** A small line under it all: who is signed in, an error's reference. */
    note?: ReactNode;
    className?: string;
}) {
    const mark = art ?? (icon || tone === "warn"
        ? <span className="kcalm__i" data-tone={tone}><Icon name={icon ?? "warn"} size={22} /></span>
        : null);
    return (
        <div className={"kcalm" + (className ? " " + className : "")} role={tone === "warn" ? "alert" : undefined}>
            {mark}
            <h2 className="kcalm__t">{title}</h2>
            {children && <div className="kcalm__line">{children}</div>}
            {actions && <div className="kcalm__acts">{actions}</div>}
            {note && <p className="kcalm__note">{note}</p>}
        </div>
    );
}

/** A failure said where it happened -- under the field, in the row -- with
 *  the way to try again beside it. Nothing moves on as though it worked. */
export function Problem({ children, onRetry, retrying = false }: { children: ReactNode; onRetry?: () => void; retrying?: boolean }) {
    return (
        <p className="kproblem" role="alert">
            <Icon name="warn" size={15} />
            <span>{children}</span>
            {onRetry && (
                <button type="button" className="b b--s b--secondary" disabled={retrying} onClick={onRetry}>
                    {retrying ? "Trying…" : "Try again"}
                </button>
            )}
        </p>
    );
}
