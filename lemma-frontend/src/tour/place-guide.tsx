"use client";

import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import { CloseIcon } from "@/ui/icons";
import type { Guide } from "./guides";

/** Below this the card is a sheet along the bottom edge. */
const NARROW = 640;

/** One place's guide: a gesture and what it does, a line each, and a way to
 *  try it.
 *
 *  Not a tour. Nothing steps and nothing dims; it sits beside the help button
 *  it belongs to — the one place to come back to — and closes on Escape or a
 *  click anywhere else, putting focus back where it was. On a phone, where
 *  the help button is inside the closed drawer, it is a sheet along the
 *  bottom instead. */
export function PlaceGuide({ guide, onTry, onClose }: {
    guide: Guide;
    /** Learning by doing. A rejection is shown on the card; anything else
       closes it, since the thing tried is now on screen. */
    onTry?: () => Promise<void> | void;
    onClose: () => void;
}) {
    const card = useRef<HTMLDivElement>(null);
    const heading = useId();
    const [at, setAt] = useState<CSSProperties | null>(null);
    const [busy, setBusy] = useState(false);
    const [problem, setProblem] = useState<string | null>(null);
    const close = useRef(onClose);
    close.current = onClose;

    useLayoutEffect(() => {
        const place = () => {
            const rect = document.querySelector<HTMLElement>('[data-tour="help"]')?.getBoundingClientRect();
            setAt(rect && rect.width > 0 && window.innerWidth >= NARROW
                ? { left: rect.right + 10, bottom: Math.max(12, window.innerHeight - rect.bottom) }
                : { left: 12, right: 12, bottom: 12 });
        };
        place();
        window.addEventListener("resize", place);
        return () => window.removeEventListener("resize", place);
    }, []);

    useEffect(() => {
        const before = document.activeElement as HTMLElement | null;
        card.current?.focus({ preventScroll: true });
        const keys = (event: KeyboardEvent) => {
            if (event.key !== "Escape") return;
            event.stopPropagation();
            close.current();
        };
        const away = (event: MouseEvent) => {
            if (!card.current?.contains(event.target as Node)) close.current();
        };
        document.addEventListener("keydown", keys);
        document.addEventListener("mousedown", away);
        return () => {
            document.removeEventListener("keydown", keys);
            document.removeEventListener("mousedown", away);
            if (before?.isConnected) before.focus({ preventScroll: true });
        };
    }, []);

    const tryIt = guide.tryIt && onTry ? async () => {
        setBusy(true);
        setProblem(null);
        try {
            await onTry();
            close.current();
        } catch (failure) {
            setProblem(failure instanceof Error ? failure.message : "That did not work.");
        } finally {
            setBusy(false);
        }
    } : null;

    return createPortal(
        <div ref={card} className="guide" role="dialog" aria-labelledby={heading} tabIndex={-1} style={at ?? { visibility: "hidden" }}>
            <header className="guide__head">
                <h2 id={heading}>{guide.title}</h2>
                <button className="guide__close" aria-label={"Close " + guide.title.toLowerCase()} title="Close" onClick={onClose}>
                    <CloseIcon size={14} />
                </button>
            </header>
            <dl className="guide__lines">
                {guide.lines.map((line) => (
                    <div key={line.gesture} className="guide__line">
                        <dt><span className="guide__gesture">{line.gesture}</span></dt>
                        <dd>{line.what}</dd>
                    </div>
                ))}
            </dl>
            {guide.tryIt && tryIt && (
                <div className="guide__foot">
                    <button className="guide__try" disabled={busy} onClick={() => void tryIt()}>
                        {busy ? "Making…" : guide.tryIt.label}
                    </button>
                    {problem && <p className="guide__problem" role="alert">{problem}</p>}
                </div>
            )}
        </div>,
        document.body,
    );
}
