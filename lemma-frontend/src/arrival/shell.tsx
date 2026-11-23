"use client";

import { useSyncExternalStore, type ReactNode } from "react";
import { LemmaLogo } from "@/ui/icons";
import { productFace } from "@/ui/product-face";
import { TeammateFace } from "@/space/teammate-face";
import { ArrivalStage, StageStrip, type StageModel } from "./stage";
import { STEP_NAMES, type ArrivalStepId } from "./steps";

/** What a Lemma is, in one sentence, for a door with no space for the
 *  Lemma's own hello (a plugin's install page). */
export const LEMMA_IS =
    "A Lemma is a shared place for one part of your work: an AI you can ask, apps for the work, and the people in it.";

/** Whether the speaker stands beside the question rather than in a strip
 *  over it; the same width the stylesheet turns the strip on at. */
const WIDE = "(min-width: 901px)";
function useWide(): boolean {
    return useSyncExternalStore(
        (changed) => {
            const query = window.matchMedia(WIDE);
            query.addEventListener("change", changed);
            return () => query.removeEventListener("change", changed);
        },
        () => window.matchMedia(WIDE).matches,
        () => true,
    );
}

/** The way in, as one living scene. On the left, whoever is asking, big --
 *  Lemma's bars until your Lemma exists, then its face -- with everything
 *  you choose attaching to it, what it is doing in a word, and how far
 *  along you are. On the right, the question in its own words, your answer,
 *  and the way on. `inline` is the right side alone, inside a page that
 *  already has a frame of its own (a plugin's install panel). */
export function ArrivalShell({ steps, at, inline = false, label, stage, status = "", children }: {
    steps: readonly ArrivalStepId[];
    at: ArrivalStepId | null;
    inline?: boolean;
    /** What the step is about, for a screen reader's landmark. */
    label: string;
    /** The scene on the left; absent for the inline variant. */
    stage?: StageModel;
    /** What the speaker is doing, in a word: "Listening", "Working". */
    status?: string;
    children: ReactNode;
}) {
    /* Beside the question the face is the page's one live rig -- the same
       sculpture, breathing, that the Lemma's own Home draws -- and in the
       strip over it a still. One rig, never two: a column of them is what
       once stopped iPhones painting text. */
    const wide = useWide();
    if (inline || !stage) {
        return (
            <div className={productFace.variable + " ar ar--inline"}>
                <main className="ar__col" aria-label={label}>{children}</main>
            </div>
        );
    }
    const now = at ? steps.indexOf(at) : -1;
    const name = stage.pod?.name ?? "Lemma";
    return (
        <div className={productFace.variable + " ar"}>
            <aside className="arh" aria-label={`${name}, ${status || "setting you up"}`}>
                {wide ? (
                    <>
                        <LemmaLogo />
                        <div className="arh__hero">
                            <ArrivalStage model={stage} />
                            <span className="arh__said">
                                <span key={name} className="arh__name">{name}</span>
                                {status && <span className="arh__status" role="status"><i aria-hidden="true" />{status}</span>}
                            </span>
                        </div>
                    </>
                ) : (
                    <StageStrip model={stage} name={name} status={status} />
                )}
                {steps.length > 1 && now >= 0 && (
                    <ol className="arh__dots" aria-label="Steps">
                        {steps.map((step, index) => (
                            <li key={step} data-state={index <= now ? "on" : "off"} aria-current={index === now ? "step" : undefined}>
                                <span className="ar__hidden">{STEP_NAMES[step]}</span>
                            </li>
                        ))}
                    </ol>
                )}
            </aside>
            <main className="arq" aria-label={label}>
                <div key={at ?? "none"} className="arq__body">{children}</div>
            </main>
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
export function LemmaMark({ id, name, iconUrl = null, size = 36 }: { id: string; name: string; iconUrl?: string | null; size?: number }) {
    return <TeammateFace pod={{ id, name, iconUrl }} size={size} />;
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
            <TickIcon />
            In
        </span>
    );
}

export function TickIcon({ size = 13 }: { size?: number }) {
    return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M5 12.5l4.5 4.5L19 7.5" />
        </svg>
    );
}

/** The three parts of a Lemma, each with its small mark. */
export function AiIcon() {
    return (
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
            <path d="M12 3v5M12 16v5M3 12h5M16 12h5M6.3 6.3l2.8 2.8M14.9 14.9l2.8 2.8M6.3 17.7l2.8-2.8M14.9 9.1l2.8-2.8" />
        </svg>
    );
}

export function AppsIcon() {
    return (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <rect x="4" y="4" width="7" height="7" rx="1.5" />
            <rect x="13" y="4" width="7" height="7" rx="1.5" />
            <rect x="4" y="13" width="7" height="7" rx="1.5" />
            <rect x="13" y="13" width="7" height="7" rx="1.5" />
        </svg>
    );
}

export function PeopleIcon() {
    return (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
            <circle cx="9" cy="8" r="3.5" />
            <path d="M2.5 20c.8-3.5 3.4-5.5 6.5-5.5s5.7 2 6.5 5.5M16 4.6a3.5 3.5 0 0 1 0 6.8M18 14.8c1.9.8 3.1 2.6 3.5 5.2" />
        </svg>
    );
}
