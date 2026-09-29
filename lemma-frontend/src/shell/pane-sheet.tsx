import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { CloseIcon, ExpandIcon } from "@/ui/icons";
import { settleSheet } from "./split-tabs";

const PHONE = "(max-width: 767px)";

export function usePhoneWidth(): boolean {
    const [phone, setPhone] = useState(false);
    useEffect(() => {
        const query = window.matchMedia(PHONE);
        const update = () => setPhone(query.matches);
        update();
        query.addEventListener("change", update);
        return () => query.removeEventListener("change", update);
    }, []);
    return phone;
}

type Drag = { pointer: number; startY: number; max: number; base: number; y: number; at: number; speed: number; moved: boolean };

/** The bar above a resource open beside the conversation.
 *
 *  On a phone it is also the top of a sheet. There is no width to put the
 *  resource beside anything, and stacking the two halves left a letterbox of
 *  each, so the resource rises over the conversation instead and the bar
 *  lowers it to a single row above the composer — the way back to it. Only the
 *  bar drags: most of what sits in the sheet is a frame whose scroll position
 *  this page cannot read, so there is no "scrolled to the top, now drag the
 *  sheet" hand-off to make, and trying turns every scroll into a fight.
 *
 *  The drag writes the body's `--sheet-y` directly rather than through state,
 *  and the release hands back to the `body--peek` class in the same commit that
 *  sets it, so the sheet eases from wherever the finger left it. */
export function RightPaneToolbar({ label, peeked, onPeek, onExpand, onClose, body }: {
    label: string;
    peeked: boolean;
    onPeek: (peeked: boolean) => void;
    onExpand: () => void;
    onClose: () => void;
    body: RefObject<HTMLDivElement | null>;
}) {
    const phone = usePhoneWidth();
    const stop = useRef<(() => void) | null>(null);
    const justDragged = useRef(false);

    const letGo = (element: HTMLDivElement | null) => {
        element?.style.removeProperty("--sheet-y");
        element?.style.removeProperty("--sheet-motion");
    };
    useLayoutEffect(() => letGo(body.current), [peeked, body]);
    useEffect(() => () => stop.current?.(), []);

    return <>
        <div className="pane-sheet-scrim" aria-hidden onClick={() => onPeek(true)} />
        <div
            className="right-pane-toolbar"
            onPointerDown={event => {
                justDragged.current = false;
                const element = body.current;
                if (!phone || !element || event.button !== 0) return;
                if ((event.target as Element).closest(".right-pane-toolbar__action")) return;
                const bar = event.currentTarget;
                const max = element.clientHeight - bar.offsetTop - bar.offsetHeight;
                const now: Drag = { pointer: event.pointerId, startY: event.clientY, max, base: peeked ? max : 0, y: event.clientY, at: event.timeStamp, speed: 0, moved: false };

                /* Followed on the window rather than captured: capturing on the
                   press retargets the click to the bar, so a tap on the title
                   would never reach the title. The window does not hear a
                   pointer over a frame, so the panes stop taking the pointer
                   while the bar is held — the divider's shield, for the same
                   reason. */
                const move = (moved: PointerEvent) => {
                    if (moved.pointerId !== now.pointer) return;
                    const travel = moved.clientY - now.startY;
                    if (!now.moved && Math.abs(travel) < 6) return;
                    now.moved = true;
                    const elapsed = moved.timeStamp - now.at;
                    if (elapsed > 0) now.speed = (moved.clientY - now.y) / elapsed;
                    now.y = moved.clientY;
                    now.at = moved.timeStamp;
                    element.style.setProperty("--sheet-motion", "0s");
                    element.style.setProperty("--sheet-y", Math.min(now.max, Math.max(0, now.base + travel)) + "px");
                };
                const up = (lifted: PointerEvent) => {
                    if (lifted.pointerId !== now.pointer) return;
                    stop.current?.();
                    if (!now.moved) return;
                    justDragged.current = true;
                    const next = settleSheet(peeked, lifted.clientY - now.startY, now.speed);
                    if (next === peeked) letGo(element);
                    else onPeek(next);
                };
                const cancel = () => { stop.current?.(); letGo(element); };
                stop.current?.();
                element.dataset.sheetHeld = "";
                window.addEventListener("pointermove", move);
                window.addEventListener("pointerup", up);
                window.addEventListener("pointercancel", cancel);
                stop.current = () => {
                    delete element.dataset.sheetHeld;
                    window.removeEventListener("pointermove", move);
                    window.removeEventListener("pointerup", up);
                    window.removeEventListener("pointercancel", cancel);
                    stop.current = null;
                };
            }}
        >
            {phone ? (
                <button
                    className="right-pane-toolbar__title"
                    aria-expanded={!peeked}
                    onClick={() => {
                        if (justDragged.current) { justDragged.current = false; return; }
                        onPeek(!peeked);
                    }}
                >
                    <span>{label}</span>
                </button>
            ) : <span>{label}</span>}
            <button className="icon-button right-pane-toolbar__action" title="View in full" aria-label="View in full" onClick={onExpand}><ExpandIcon size={17} /></button>
            <button className="icon-button right-pane-toolbar__action" title="Close right pane" aria-label="Close right pane" onClick={onClose}><CloseIcon size={17} /></button>
        </div>
    </>;
}
