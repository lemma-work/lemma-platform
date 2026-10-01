"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { openExternal } from "@/desktop/open-external";
import { CloseIcon, DocsIcon, LibraryIcon, QuestionIcon, SparkleIcon, TourIcon } from "@/ui/icons";

/** How long the note after the tour stays up on its own. */
const NUDGE_MS = 7000;

/** The way back to the tour, to how the place on screen works, and to the
 *  two places that explain the rest.
 *
 *  In the rail's foot, above the account, as a glyph: the rail is faces, not
 *  words. Its menu is drawn over the page beside the rail — the rail scrolls,
 *  and anything drawn inside a scroller is clipped at its edge. Its first item
 *  follows where you are: on Pages it is "How pages work"; somewhere with no
 *  guide it is not there at all.
 *
 *  `nudge` is the one line shown next to it when a tour ends, so the person
 *  learns where it lives without having to go looking. */
export function HelpMenu({ onTour, guide, onGuide, nudge, onNudged }: {
    onTour: () => void;
    /** The guide to the place on screen, by its title, if it has one. */
    guide?: string | null;
    onGuide?: () => void;
    nudge: boolean;
    onNudged: () => void;
}) {
    const [open, setOpen] = useState(false);
    const button = useRef<HTMLButtonElement>(null);
    const menu = useRef<HTMLDivElement>(null);
    const [at, setAt] = useState<{ left: number; bottom: number } | null>(null);

    /* Measured from the button whenever something is drawn beside it. */
    useEffect(() => {
        if (!open && !nudge) return;
        const place = () => {
            const rect = button.current?.getBoundingClientRect();
            /* Folded away with the rail: nothing to point from. */
            setAt(rect && rect.width > 0 ? { left: rect.right + 10, bottom: Math.max(12, window.innerHeight - rect.bottom) } : null);
        };
        place();
        window.addEventListener("resize", place);
        return () => window.removeEventListener("resize", place);
    }, [open, nudge]);

    useEffect(() => {
        if (!open) return;
        menu.current?.querySelector<HTMLElement>("[role=menuitem]")?.focus();
        const away = (event: MouseEvent) => {
            const target = event.target as Node;
            if (button.current?.contains(target) || menu.current?.contains(target)) return;
            setOpen(false);
        };
        const keys = (event: KeyboardEvent) => {
            if (event.key === "Escape") { setOpen(false); button.current?.focus(); return; }
            if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
            const items = Array.from(menu.current?.querySelectorAll<HTMLElement>("[role=menuitem]") ?? []);
            const index = items.indexOf(document.activeElement as HTMLElement);
            if (index < 0) return;
            event.preventDefault();
            items[(index + (event.key === "ArrowDown" ? 1 : items.length - 1)) % items.length]?.focus();
        };
        document.addEventListener("mousedown", away);
        document.addEventListener("keydown", keys);
        return () => {
            document.removeEventListener("mousedown", away);
            document.removeEventListener("keydown", keys);
        };
    }, [open]);

    useEffect(() => {
        if (!nudge) return;
        const gone = window.setTimeout(onNudged, NUDGE_MS);
        return () => window.clearTimeout(gone);
    }, [nudge, onNudged]);

    const visit = (path: string) => {
        setOpen(false);
        openExternal(new URL(path, window.location.origin).href);
    };

    return (
        <>
            <button
                ref={button}
                className="trail__help"
                aria-label="Help"
                title="Help"
                aria-haspopup="menu"
                aria-expanded={open}
                data-tour="help"
                onClick={() => { onNudged(); setOpen((was) => !was); }}
            >
                <QuestionIcon size={18} />
            </button>
            {open && at && createPortal(
                <div ref={menu} className="helpmenu" role="menu" aria-label="Help" style={{ left: at.left, bottom: at.bottom }}>
                    {guide && onGuide && (
                        <button role="menuitem" onClick={() => { setOpen(false); onGuide(); }}>
                            <LibraryIcon size={17} />
                            <span>{guide}<small>What you can do here</small></span>
                        </button>
                    )}
                    <button role="menuitem" onClick={() => { setOpen(false); onTour(); }}>
                        <TourIcon size={17} />
                        <span>Show me around<small>A minute on where everything is</small></span>
                    </button>
                    <button role="menuitem" onClick={() => visit("/docs")}>
                        <DocsIcon size={17} />
                        <span>Documentation</span>
                    </button>
                    <button role="menuitem" onClick={() => visit("/changelog")}>
                        <SparkleIcon size={17} />
                        <span>What’s new</span>
                    </button>
                </div>,
                document.body,
            )}
            {nudge && !open && at && createPortal(
                <div className="helpnudge" role="status" style={{ left: at.left, bottom: at.bottom }}>
                    <span>The tour, and a guide to each place, live here.</span>
                    <button aria-label="Dismiss" onClick={onNudged}><CloseIcon size={13} /></button>
                </div>,
                document.body,
            )}
        </>
    );
}
