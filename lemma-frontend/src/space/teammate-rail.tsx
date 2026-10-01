"use client";

import { useEffect, useRef, useState, type ReactNode, type SyntheticEvent } from "react";
import type { Pod } from "@/data";
import { MATES, NEW_MATE } from "@/copy";
import { PlusIcon } from "@/ui/icons";
import { TeammateFace } from "./teammate-face";
import { needsYou, railLine, type Owed } from "./teammates";

type Tip = { top: number; left: number; name: string; line: string };

/** The rail: who. Every teammate you are in, as its face, and the same at
 *  both altitudes — above them the way out to every teammate at once, below
 *  them the way to hire another.
 *
 *  Small faces only, so it stays a quiet strip beside the space it opens:
 *  names under them were tried, and real names are too long for any rail
 *  narrow enough to keep. A name arrives on hover or focus, in a tip placed
 *  over the page rather than inside the rail: the list scrolls, and anything
 *  drawn inside a scroller is clipped at its edge. */
export function TeammateRail({ pods, activeId, atTeam, hiring = false, owed, orgName, onTeam, onPick, onHire, foot }: {
    pods: Pod[];
    /** The teammate whose space is open, if one is. */
    activeId: string | null;
    /** Zoomed out, on the page with every teammate. */
    atTeam: boolean;
    /** Hiring somebody new: the one place none of the faces is where you are. */
    hiring?: boolean;
    owed: ReadonlyMap<string, Owed>;
    orgName: string;
    onTeam: () => void;
    onPick: (podId: string) => void;
    /** Null until there is an organization to hire into. */
    onHire: (() => void) | null;
    /** The account, and the allowance when it is worth a word. */
    foot?: ReactNode;
}) {
    const [tip, setTip] = useState<Tip | null>(null);

    /* More faces below the fold than fit: the list's lower edge fades, so the
       last one visible reads as "keeps going" rather than as a face cut in
       half. Measured, because a fade on a list that ends is a face nobody
       can see clearly for no reason. */
    const list = useRef<HTMLDivElement>(null);
    const [more, setMore] = useState(false);
    const measure = () => {
        const el = list.current;
        if (el) setMore(el.scrollTop + el.clientHeight < el.scrollHeight - 1);
    };
    useEffect(() => {
        const el = list.current;
        if (!el) return;
        measure();
        const watch = new ResizeObserver(measure);
        watch.observe(el);
        return () => watch.disconnect();
    }, [pods.length]);
    const show = (name: string, line: string) => (event: SyntheticEvent<HTMLElement>) => {
        const box = event.currentTarget.getBoundingClientRect();
        setTip({ top: box.top + box.height / 2, left: box.right + 10, name, line });
    };
    const hide = () => setTip(null);
    const hint = (name: string, line: string) => ({
        onMouseEnter: show(name, line),
        onFocus: show(name, line),
        onMouseLeave: hide,
        onBlur: hide,
    });

    return (
        <nav className="trail" aria-label={MATES} data-tour="rail">
            <button
                className="trail__home"
                aria-current={atTeam ? "page" : undefined}
                aria-label={"All " + MATES.toLowerCase() + " in " + orgName}
                onClick={() => { hide(); onTeam(); }}
                {...hint(MATES, orgName)}
            >
                {/* Lemma's own mark, the three rising bars of the app icon. */}
                <svg className="trail__mark" viewBox="0 0 32 32" aria-hidden="true">
                    <rect x="4" y="19" width="5" height="10" rx="1.5" />
                    <rect x="13" y="12" width="5" height="17" rx="1.5" />
                    <rect x="22" y="3" width="5" height="26" rx="1.5" />
                </svg>
            </button>
            <span className="trail__rule" aria-hidden="true" />
            <div ref={list} className={"trail__list" + (more ? " trail__list--more" : "")} onScroll={() => { hide(); measure(); }}>
                {pods.map((pod) => {
                    const line = railLine(pod, owed);
                    const needs = needsYou(pod, owed);
                    return (
                        <button
                            key={pod.id}
                            className="trail__mate"
                            aria-current={pod.id === activeId ? "page" : undefined}
                            aria-label={pod.name + (needs && line ? " — " + line : "")}
                            onClick={() => { hide(); onPick(pod.id); }}
                            {...hint(pod.name, line)}
                        >
                            <span className="trail__slot">
                                <TeammateFace pod={pod} size={32} />
                                {needs && <span className="trail__badge" aria-hidden="true" />}
                            </span>
                        </button>
                    );
                })}
            </div>
            {onHire && (
                <button className="trail__mate trail__add" aria-label={NEW_MATE} aria-current={hiring ? "page" : undefined} onClick={() => { hide(); onHire(); }} {...hint(NEW_MATE, "")}>
                    <span className="trail__slot trail__plus"><PlusIcon size={16} /></span>
                </button>
            )}
            <div className="trail__foot">{foot}</div>
            {tip && (
                <span className="trail__tip" role="tooltip" style={{ top: tip.top, left: tip.left }}>
                    {tip.name}
                    {tip.line && <small>{tip.line}</small>}
                </span>
            )}
        </nav>
    );
}
