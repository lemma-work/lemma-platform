import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";

/** The behaviour behind `.pick`: open, close, and which way to open.
 *
 *  Two controls on the profile use it — what a teammate runs on, and who may
 *  join it — and both had the same dismissal effect written out, which is two
 *  copies of a keyboard contract that has to stay identical to be learnable.
 *
 *  Opening upward is the part that could not be written twice by hand. A menu
 *  is positioned against the control, but it is *clipped* by whichever
 *  ancestor scrolls — and the profile scrolls in a pane, not in the page. The
 *  join control is the last thing on that pane, so a menu below it opened into
 *  eighty pixels of nothing and lost a row: the third rung was simply not
 *  there, and the two that were looked like the whole list.
 */

/** The bottom edge of whatever would cut a menu off, in viewport coordinates. */
function clippedAt(node: HTMLElement): number {
    for (let walk = node.parentElement; walk; walk = walk.parentElement) {
        const style = getComputedStyle(walk);
        if (style.overflowY !== "visible") return walk.getBoundingClientRect().bottom;
    }
    return document.documentElement.clientHeight;
}

export interface Picker<T extends HTMLElement> {
    open: boolean;
    setOpen: (open: boolean | ((was: boolean) => boolean)) => void;
    /** Goes on the `.pick` wrapper: both the dismissal and the measuring read
     *  the control and its menu out of it. */
    wrap: RefObject<T | null>;
    /** `"pick__menu"`, plus the modifier when it has to open upward. */
    menuClass: string;
}

export function usePicker<T extends HTMLElement = HTMLDivElement>(): Picker<T> {
    const wrap = useRef<T>(null);
    const [open, setOpen] = useState(false);
    const [up, setUp] = useState(false);

    useEffect(() => {
        if (!open) return;
        const away = (event: MouseEvent) => {
            if (!wrap.current?.contains(event.target as Node)) setOpen(false);
        };
        const escape = (event: KeyboardEvent) => {
            if (event.key === "Escape") setOpen(false);
        };
        document.addEventListener("mousedown", away);
        document.addEventListener("keydown", escape);
        return () => {
            document.removeEventListener("mousedown", away);
            document.removeEventListener("keydown", escape);
        };
    }, [open]);

    /* Measured before the frame is painted, so the menu is never seen in the
       wrong place. Downward unless it does not fit and upward does — a menu
       that flips when either direction would have fitted is a menu that moves
       for no reason anybody watching can name. */
    useLayoutEffect(() => {
        if (!open) { setUp(false); return; }
        const face = wrap.current?.querySelector<HTMLElement>(".pick__face");
        const menu = wrap.current?.querySelector<HTMLElement>(".pick__menu");
        if (!face || !menu) return;
        const box = face.getBoundingClientRect();
        const wanted = menu.offsetHeight + 12;
        setUp(wanted > clippedAt(face) - box.bottom && wanted <= box.top);
    }, [open]);

    return { open, setOpen, wrap, menuClass: up ? "pick__menu pick__menu--up" : "pick__menu" };
}
