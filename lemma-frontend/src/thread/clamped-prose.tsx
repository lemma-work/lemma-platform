"use client";

import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Prose } from "./markdown";

/** Markdown held to a few lines until somebody asks for the rest.
 *
 *  For text that arrives rather than text that was asked for: a notification
 *  in the transcript, a body in the bell's panel. Those were a raw string in a
 *  box — the asterisks of `**Tagline**` printed as asterisks, and a paragraph
 *  from an agent took over the whole thread above the message it interrupted.
 *
 *  Clipped by height, not `line-clamp`: rendered markdown is a stack of blocks
 *  and `line-clamp` only counts the lines of one. The height is the lines
 *  times the line height of the box, and the markdown inside takes the box's
 *  type so the two agree.
 *
 *  The toggle is there only when something is actually hidden, asked of the
 *  element and asked again when its width changes — two lines is a sentence
 *  in a side pane and half a paragraph on a wide screen.
 */
export function ClampedProse({ text, lines = 2 }: { text: string; lines?: number }) {
    const [open, setOpen] = useState(false);
    const [clipped, setClipped] = useState(false);
    const node = useRef<HTMLDivElement | null>(null);

    useEffect(() => {
        const element = node.current;
        if (!element) return;
        /* Only while collapsed: opened, the box is exactly as tall as its
           content and would report itself as fitting, taking the way back
           with it. */
        const measure = () => { if (!open) setClipped(element.scrollHeight > element.clientHeight + 1); };
        measure();
        const watch = new ResizeObserver(measure);
        watch.observe(element);
        return () => watch.disconnect();
    }, [open, text]);

    return (
        <div className="clamp">
            <div
                ref={node}
                className="clamp__body"
                data-collapsed={open ? undefined : ""}
                style={{ "--clamp-lines": lines } as CSSProperties}
            >
                <Prose text={text} />
            </div>
            {clipped && (
                <button
                    type="button"
                    className="clamp__toggle"
                    aria-expanded={open}
                    onClick={(event) => {
                        /* Inside a row that may be clickable itself. */
                        event.stopPropagation();
                        setOpen(!open);
                    }}
                >
                    {open ? "Show less" : "Show more"}
                </button>
            )}
        </div>
    );
}
