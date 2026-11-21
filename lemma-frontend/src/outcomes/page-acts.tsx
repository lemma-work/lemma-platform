"use client";

import { createContext, useContext, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** A page's own buttons, in its header.
 *
 *  A Lemma's page header -- the way back, the title, its line, and the
 *  buttons at the right (kit.css, "Page headers") -- is drawn by the frame,
 *  apart from the page under it, so a page whose buttons hold its own state
 *  (Files' Upload and its file picker) cannot hand them up as props. The
 *  header marks a place in its row of buttons (`PageActsSlot`) and the page
 *  puts its buttons there (`PageActs`). A page drawn with no such header --
 *  no provider, or no slot on screen yet -- keeps them in a row of its own
 *  at the top, as before. */
const Slot = createContext<{ at: HTMLElement | null; set: (at: HTMLElement | null) => void } | null>(null);

/** Around a header and the page under it. */
export function PageActsProvider({ children }: { children: ReactNode }) {
    const [at, set] = useState<HTMLElement | null>(null);
    return <Slot.Provider value={{ at, set }}>{children}</Slot.Provider>;
}

/** Where in the header the page's buttons stand. */
export function PageActsSlot() {
    const slot = useContext(Slot);
    if (!slot) return null;
    return <span className="ph__slot" ref={slot.set} />;
}

/** The page's buttons: in its header when it has one, else where they are
 *  drawn, wrapped in `className`. */
export function PageActs({ className, children }: { className?: string; children: ReactNode }) {
    const slot = useContext(Slot);
    if (slot?.at) return createPortal(children, slot.at);
    return <div className={className}>{children}</div>;
}
