"use client";

import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent as ReactMouseEvent } from "react";
import type { Pod } from "@/data";
import { markTint } from "@/shell/mark-tint";
import { TeammateFace } from "@/space/teammate-face";
import { AppCover } from "@/stage/app-cover";
import { AppMark, DRAWINGS, drawingOf } from "./app-mark";
import type { PodApp } from "./ask";
import { outcomeView, writeView, type Go, type View } from "./model";
import { Icon, to } from "./parts";

const MINUTE = 60_000;
const DAY = 24 * 60 * MINUTE;

function dayOf(at: number): number {
    const day = new Date(at);
    return new Date(day.getFullYear(), day.getMonth(), day.getDate()).getTime();
}

/** When an app last changed, as short as a card's foot allows: "Just now",
 *  "12 min", "Today", "Yesterday", "3 d", "2 w", then the date. Null when
 *  nobody said. */
export function shortWhen(iso: string | undefined, now: number): string | null {
    const at = iso ? Date.parse(iso) : Number.NaN;
    if (!Number.isFinite(at)) return null;
    const minutes = Math.floor((now - at) / MINUTE);
    if (minutes < 1) return "Just now";
    if (minutes < 60) return `${minutes} min`;
    const days = Math.round((dayOf(now) - dayOf(at)) / DAY);
    if (days <= 0) return "Today";
    if (days === 1) return "Yesterday";
    if (days < 7) return `${days} d`;
    if (days < 56) return `${Math.floor(days / 7)} w`;
    const then = new Date(at);
    const thisYear = then.getFullYear() === new Date(now).getFullYear();
    return then.toLocaleDateString(undefined, thisYear ? { day: "numeric", month: "short" } : { day: "numeric", month: "short", year: "numeric" });
}

/** The name an app is addressed by: its tab's id without the kind. */
export function appNameOf(app: PodApp["app"]): string {
    return app.id.replace(/^app:/, "");
}

/** Which card a just-landed app is, across Lemmas. */
export function cardKey(podId: string, app: string): string {
    return podId + "/" + app;
}

/** An app's cover: the picture its own build ships, over its tint with its
 *  drawing large in a paper circle -- what shows while the picture loads,
 *  and in place of one that never will. Never a blank grey tile. */
function Cover({ name, label, url }: { name: string; label: string; url: string }) {
    const tint = markTint("app:" + name);
    return (
        <span className="appsp-cover" style={{ background: tint.background, color: tint.color }}>
            <span className="appsp-cover__mk">
                <svg width={24} height={24} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d={DRAWINGS[drawingOf(name, label)]} />
                </svg>
            </span>
            <AppCover url={url} fallback={null} />
        </span>
    );
}

/** One app as a card: its cover, its mark and name, and whose it is and
 *  when it last changed. The card opens the app here, framed, in the Lemma
 *  that runs it; ⋯ holds the rest. `fresh` lights it once, for an app that
 *  just went in. */
export function AppCard({ pod, app, go, fresh, onSeen }: {
    pod: Pod;
    app: PodApp["app"];
    go: Go;
    fresh: boolean;
    onSeen: () => void;
}) {
    const name = appNameOf(app);
    const view: View = { name: "app", podId: pod.id, app: name };
    const when = shortWhen(app.updated, Date.now());
    return (
        <li
            className="appsp-card"
            data-new={fresh || undefined}
            onAnimationEnd={(event) => {
                if (event.animationName === "appsp-landed") onSeen();
            }}
        >
            <a className="appsp-card__open" {...to(view, go)}>
                <span className="appsp-card__c"><Cover name={name} label={app.label} url={app.url} /></span>
                <span className="appsp-card__w">
                    <AppMark name={name} label={app.label} size={30} className="appsp-card__mk" />
                    <span className="appsp-card__n">{app.label}</span>
                    <span className="appsp-card__s">
                        <TeammateFace pod={pod} size={16} className="appsp-card__face" />
                        <span className="appsp-card__l">{pod.name}</span>
                        {when && <span className="appsp-card__when">· {when}</span>}
                    </span>
                </span>
            </a>
            <CardMenu pod={pod} app={app} view={view} go={go} />
        </li>
    );
}

async function copied(text: string): Promise<boolean> {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch {
        return false;
    }
}

/** A card's ⋯: the app in a tab of its own, its address here to share, and
 *  the way to its Lemma. Opens with the first choice focused; the arrow keys
 *  move between them; Escape, a pick, or a click or Tab elsewhere closes
 *  it. */
function CardMenu({ pod, app, view, go }: { pod: Pod; app: PodApp["app"]; view: View; go: Go }) {
    const [open, setOpen] = useState(false);
    const [copy, setCopy] = useState<"done" | "failed" | null>(null);
    const box = useRef<HTMLDivElement>(null);
    const button = useRef<HTMLButtonElement>(null);

    useEffect(() => {
        if (!open) return;
        box.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
        const away = (event: PointerEvent) => {
            if (!box.current?.contains(event.target as Node)) setOpen(false);
        };
        document.addEventListener("pointerdown", away);
        return () => document.removeEventListener("pointerdown", away);
    }, [open]);

    /* Said in the menu, then the menu closes: no toast. */
    useEffect(() => {
        if (!copy) return;
        const timer = window.setTimeout(() => {
            setOpen(false);
            setCopy(null);
        }, copy === "done" ? 1100 : 2200);
        return () => window.clearTimeout(timer);
    }, [copy]);

    const close = (refocus: boolean) => {
        setOpen(false);
        setCopy(null);
        if (refocus) button.current?.focus();
    };

    const keys = (event: ReactKeyboardEvent<HTMLDivElement>) => {
        if (!open) return;
        if (event.key === "Escape") {
            event.preventDefault();
            close(true);
            return;
        }
        if (event.key === "Tab") {
            close(false);
            return;
        }
        const items = Array.from(box.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
        if (items.length === 0) return;
        const at = items.indexOf(document.activeElement as HTMLElement);
        let next: number | null = null;
        if (event.key === "ArrowDown") next = (at + 1) % items.length;
        else if (event.key === "ArrowUp") next = (at - 1 + items.length) % items.length;
        else if (event.key === "Home") next = 0;
        else if (event.key === "End") next = items.length - 1;
        if (next === null) return;
        event.preventDefault();
        items[next]?.focus();
    };

    const lemma = to(outcomeView(pod.id), go);
    return (
        <div className="appsp-card__more" ref={box} onKeyDown={keys} data-open={open || undefined}>
            <button
                ref={button}
                type="button"
                className="b b--s b--quiet b--icon"
                aria-label={"More for " + app.label}
                aria-haspopup="menu"
                aria-expanded={open}
                onClick={() => (open ? close(false) : setOpen(true))}
            >
                <Icon name="more" size={16} />
            </button>
            {open && (
                <div className="appsp-menu" role="menu" aria-label={app.label}>
                    <a role="menuitem" href={app.url} target="_blank" rel="noreferrer" onClick={() => close(false)}>
                        <Icon name="out" size={15} />Open in a new tab
                    </a>
                    <button
                        type="button"
                        role="menuitem"
                        onClick={async () => setCopy((await copied(new URL(writeView(view), window.location.origin).href)) ? "done" : "failed")}
                    >
                        <Icon name={copy === "done" ? "check" : "link"} size={15} />
                        <span aria-live="polite">{copy === "done" ? "Link copied" : copy === "failed" ? "Couldn’t copy it" : "Copy its link"}</span>
                    </button>
                    <a
                        role="menuitem"
                        href={lemma.href}
                        onClick={(event: ReactMouseEvent<HTMLAnchorElement>) => {
                            close(false);
                            lemma.onClick(event);
                        }}
                    >
                        <Icon name="back" size={15} />Go to {pod.name}
                    </a>
                </div>
            )}
        </div>
    );
}
