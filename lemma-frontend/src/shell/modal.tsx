import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { CloseIcon } from "@/ui/icons";

/** Open dialogs, innermost last.
 *
 *  Each dialog listens on the document, and a dialog opened from inside
 *  another — Connect over Settings — used to hear Escape alongside its parent:
 *  `stopPropagation` does not stop a second listener on the same node, so one
 *  keypress closed both. Only the innermost answers the keyboard now. */
const open: symbol[] = [];

export function Modal({ title, subtitle, narrow, wide, flush, onClose, children }: {
    title: string; subtitle?: string; narrow?: boolean; wide?: boolean; flush?: boolean;
    onClose: () => void; children: ReactNode;
}) {
    const panel = useRef<HTMLDivElement>(null);
    const close = useRef(onClose);
    close.current = onClose;
    const titleId = useId();
    useEffect(() => {
        const me = Symbol("modal");
        open.push(me);
        const previous = document.activeElement as HTMLElement | null;
        const overflow = document.body.style.overflow;
        document.body.style.overflow = "hidden";
        panel.current?.focus();
        const onKey = (event: KeyboardEvent) => {
            if (open.at(-1) !== me) return;
            if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close.current(); }
            if (event.key !== "Tab") return;
            const focusable = Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input:not([disabled]), select, textarea, [tabindex="0"]') ?? []).filter(el => el.getClientRects().length > 0);
            const first = focusable[0], last = focusable.at(-1);
            if (!first) { event.preventDefault(); return; }
            if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { event.preventDefault(); last?.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        };
        document.addEventListener("keydown", onKey, true);
        return () => {
            open.splice(open.indexOf(me), 1);
            document.body.style.overflow = overflow;
            document.removeEventListener("keydown", onKey, true);
            if (previous?.isConnected) previous.focus();
        };
    }, []);
    return createPortal(<div className="modal" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
        <div className={`modal__panel${narrow ? " modal__panel--narrow" : ""}${wide ? " modal__panel--wide" : ""}`} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1} ref={panel}>
            <header className="modal__head"><div><h2 id={titleId}>{title}</h2>{subtitle && <p>{subtitle}</p>}</div><button className="modal__close" onClick={onClose} aria-label="Close"><CloseIcon size={20} /></button></header>
            <div className={`modal__body${flush ? " modal__body--flush" : ""}`}>{children}</div>
        </div>
    </div>, document.body);
}
