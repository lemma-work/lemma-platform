"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { CloseIcon } from "@/ui/icons";
import type { Stop } from "./stops";

type Box = { top: number; left: number; width: number; height: number };

/** Around the control, inside the light. */
const PAD = 6;
/** Between the light and the card. */
const GAP = 14;
/** The card never closer than this to the window's edge. */
const EDGE = 12;
/** Below this the card is a sheet along the bottom (or the top) edge. */
const NARROW = 640;

/** What a stop points at: every control carrying its name that is on
 *  screen. Usually one. The bell has a neighbour when a workflow is waiting on
 *  you, and the two are lit together; copies drawn out of sight — the folded
 *  header keeps its own bell — have no box and are passed over. */
function findTargets(name: string): HTMLElement[] {
    return Array.from(document.querySelectorAll<HTMLElement>(`[data-tour="${name}"]`)).filter((element) => {
        const rect = element.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    });
}

/** The box around all of them. */
function boxOf(elements: HTMLElement[]): Box | null {
    if (elements.length === 0) return null;
    const rects = elements.map((element) => element.getBoundingClientRect());
    const top = Math.min(...rects.map((rect) => rect.top));
    const left = Math.min(...rects.map((rect) => rect.left));
    const bottom = Math.max(...rects.map((rect) => rect.bottom));
    const right = Math.max(...rects.map((rect) => rect.right));
    return { top, left, width: right - left, height: bottom - top };
}

const clamp = (value: number, low: number, high: number) => Math.min(Math.max(value, low), Math.max(low, high));

/** Where the card goes, given the control it is about. Flips to another side
 *  when the preferred one has no space, and becomes an edge sheet on a phone,
 *  on whichever edge the control is not near. */
function placeCard(stop: Stop, box: Box | null, card: { width: number; height: number }, view: { width: number; height: number }) {
    const centred = { top: Math.max(EDGE, (view.height - card.height) / 2), left: Math.max(EDGE, (view.width - card.width) / 2) };
    if (!box || stop.side === "center") return centred;
    if (view.width < NARROW) {
        const low = box.top + box.height / 2 > view.height * 0.5;
        return { top: low ? EDGE : view.height - card.height - EDGE, left: EDGE };
    }
    const lit = { top: box.top - PAD, left: box.left - PAD, right: box.left + box.width + PAD, bottom: box.top + box.height + PAD };
    let side = stop.side;
    if (side === "right" && lit.right + GAP + card.width > view.width - EDGE) side = "below";
    if (side === "below" && lit.bottom + GAP + card.height > view.height - EDGE) side = "above";
    if (side === "above" && lit.top - GAP - card.height < EDGE) side = "below";
    let top: number;
    let left: number;
    if (side === "right") {
        left = lit.right + GAP;
        /* A tall control — the rail runs the height of the window — is met
           near its top, where the eye already is, not at its middle. */
        top = box.height > card.height * 1.4 ? lit.top + 24 : box.top + box.height / 2 - card.height / 2;
    } else if (side === "below") {
        top = lit.bottom + GAP;
        left = box.left + box.width / 2 - card.width / 2;
    } else {
        top = lit.top - GAP - card.height;
        left = box.left + box.width / 2 - card.width / 2;
    }
    return {
        top: clamp(top, EDGE, view.height - card.height - EDGE),
        left: clamp(left, EDGE, view.width - card.width - EDGE),
    };
}

/** The tour, over the app.
 *
 *  A card beside the real control, which is lit while the rest of the window
 *  dims; nothing underneath can be clicked while it is up, so a stray click
 *  cannot open a sheet behind it. Back, Next, the arrow keys and Escape move
 *  through it; focus stays inside the card and goes back where it was when
 *  the tour ends. */
export function AppTour({ stops, onPrepare, onClose }: {
    stops: Stop[];
    /** Put a stop's control on screen: open the drawer, unfold the sidebar,
     *  go Home. Called before the stop is measured. */
    onPrepare: (stop: Stop) => void;
    /** `finished` when Done was pressed on the last stop. */
    onClose: (finished: boolean) => void;
}) {
    const [at, setAt] = useState(0);
    const stop = stops[Math.min(at, stops.length - 1)];
    const last = at === stops.length - 1;
    const [box, setBox] = useState<Box | null>(null);
    const [place, setPlace] = useState<{ top: number; left: number } | null>(null);
    const card = useRef<HTMLDivElement>(null);
    const primary = useRef<HTMLButtonElement>(null);
    const prepare = useRef(onPrepare);
    prepare.current = onPrepare;
    const found = box !== null;
    const placed = place !== null;

    const next = useCallback(() => {
        if (last) onClose(true);
        else setAt((was) => was + 1);
    }, [last, onClose]);
    const back = useCallback(() => setAt((was) => Math.max(0, was - 1)), []);

    /* Focus goes back to whatever had it, once the tour is over. */
    useEffect(() => {
        const before = document.activeElement as HTMLElement | null;
        return () => { if (before?.isConnected) before.focus({ preventScroll: true }); };
    }, []);

    /* Each stop: put its control on screen, then find it. The drawer and the
       sidebar slide in, so it is measured again once they have settled. */
    useEffect(() => {
        prepare.current(stop);
        if (!stop.target) { setBox(null); return; }
        const target = stop.target;
        let frame = 0;
        let tries = 0;
        const measure = () => {
            const elements = findTargets(target);
            if (elements.length === 0) {
                if (tries++ < 30) { frame = requestAnimationFrame(measure); return; }
                setBox(null);
                return;
            }
            elements[0].scrollIntoView({ block: "nearest", inline: "nearest" });
            setBox(boxOf(elements));
        };
        frame = requestAnimationFrame(() => { frame = requestAnimationFrame(measure); });
        const settled = window.setTimeout(() => { const elements = findTargets(target); if (elements.length) setBox(boxOf(elements)); }, 360);
        return () => { cancelAnimationFrame(frame); window.clearTimeout(settled); };
    }, [stop]);

    /* The window moving under it: a resize, a scroll, the control changing size. */
    useEffect(() => {
        if (!stop.target) return;
        const target = stop.target;
        const again = () => setBox(boxOf(findTargets(target)));
        window.addEventListener("resize", again);
        window.addEventListener("scroll", again, true);
        const watch = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(again);
        for (const element of findTargets(target)) watch?.observe(element);
        return () => {
            window.removeEventListener("resize", again);
            window.removeEventListener("scroll", again, true);
            watch?.disconnect();
        };
    }, [stop, found]);

    /* The card is placed once it has a size, then takes focus. */
    useLayoutEffect(() => {
        const element = card.current;
        if (!element) return;
        const where = placeCard(stop, box, { width: element.offsetWidth, height: element.offsetHeight }, { width: window.innerWidth, height: window.innerHeight });
        setPlace((was) => (was && was.top === where.top && was.left === where.left ? was : where));
    }, [stop, box]);
    useEffect(() => {
        if (placed) primary.current?.focus({ preventScroll: true });
    }, [placed, at]);

    /* The keyboard belongs to the tour while it is up. Captured, so the
       app's own shortcuts (⌘K, "/", Escape closing the drawer) do not fire
       underneath it. */
    useEffect(() => {
        const keys = (event: KeyboardEvent) => {
            const inside = card.current?.contains(event.target as Node) ?? false;
            if (event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                onClose(false);
                return;
            }
            if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
                event.preventDefault();
                event.stopPropagation();
                if (event.key === "ArrowRight") next(); else back();
                return;
            }
            if (event.key === "Tab") {
                const focusable = Array.from(card.current?.querySelectorAll<HTMLElement>("button:not([disabled])") ?? []);
                if (focusable.length === 0) return;
                const first = focusable[0];
                const final = focusable[focusable.length - 1];
                if (!inside || (event.shiftKey && document.activeElement === first) || (!event.shiftKey && document.activeElement === final)) {
                    event.preventDefault();
                    (event.shiftKey ? final : first).focus();
                }
                return;
            }
            if (!inside || event.metaKey || event.ctrlKey || event.key === "/") {
                event.preventDefault();
                event.stopPropagation();
            }
        };
        document.addEventListener("keydown", keys, true);
        return () => document.removeEventListener("keydown", keys, true);
    }, [next, back, onClose]);

    const light = box
        ? { top: box.top - PAD, left: box.left - PAD, width: box.width + PAD * 2, height: box.height + PAD * 2 }
        : { top: "50%", left: "50%", width: 0, height: 0 };
    const count = stops.length - 1;

    return createPortal(
        <div className="tour" data-stop={stop.id}>
            <div className={"tour__light" + (box ? "" : " tour__light--none")} style={light} aria-hidden="true" />
            <div
                ref={card}
                className={"tour__card" + (stop.id === "welcome" ? " tour__card--welcome" : "")}
                role="dialog"
                aria-modal="true"
                aria-labelledby="tour-title"
                aria-describedby="tour-line"
                style={place ? { top: place.top, left: place.left } : { visibility: "hidden" }}
            >
                {stop.id === "welcome" ? (
                    <>
                        <svg className="tour__mark" viewBox="0 0 32 32" aria-hidden="true">
                            <rect x="4" y="19" width="5" height="10" rx="1.5" />
                            <rect x="13" y="12" width="5" height="17" rx="1.5" />
                            <rect x="22" y="3" width="5" height="26" rx="1.5" />
                        </svg>
                        <span className="tour__eyebrow">Welcome to Lemma</span>
                    </>
                ) : (
                    <button className="tour__close" aria-label="End the tour" title="End the tour" onClick={() => onClose(false)}>
                        <CloseIcon size={15} />
                    </button>
                )}
                <h2 id="tour-title" className="tour__title">
                    {/* A sentence to a line, the way the landing's headline
                        sets "Hire an AI teammate. Give it a space." */}
                    {stop.title.split(/(?<=\.)\s+/).map((sentence) => <span key={sentence} className="tour__title-line">{sentence}</span>)}
                </h2>
                <p id="tour-line" className="tour__line">{stop.line}</p>
                <div className="tour__foot">
                    {stop.id === "welcome" ? (
                        <>
                            <button className="tour__quiet" onClick={() => onClose(false)}>Not now</button>
                            <button ref={primary} className="tour__next" onClick={next}>Show me around</button>
                        </>
                    ) : (
                        <>
                            <span className="tour__count" aria-label={"Stop " + at + " of " + count}>
                                {stops.slice(1).map((each, index) => (
                                    <i key={each.id} className={index + 1 === at ? "tour__pip tour__pip--here" : "tour__pip"} />
                                ))}
                            </span>
                            <button className="tour__quiet" onClick={back}>Back</button>
                            <button ref={primary} className="tour__next" onClick={next}>{last ? "Done" : "Next"}</button>
                        </>
                    )}
                </div>
                <p className="sr-only" aria-live="polite">{stop.title}. {stop.line}</p>
            </div>
        </div>,
        document.body,
    );
}
