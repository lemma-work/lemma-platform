"use client";

import type { MouseEvent, ReactNode } from "react";
import { writeView, type DayCount, type Go, type GoOptions, type View } from "./model";

/** A link to a place in the frame: a plain click stays in the page, and a
 *  modified one still opens a new tab, because every place has an address. */
export function to(view: View, go: Go, options?: GoOptions): { href: string; onClick: (event: MouseEvent<HTMLAnchorElement>) => void } {
    return {
        href: writeView(view),
        onClick: (event) => {
            if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
            event.preventDefault();
            go(view, options);
        },
    };
}

/** Lemma's mark, the three rising bars of the app icon. */
export function Bars({ size = 22, label }: { size?: number; label?: string }) {
    return (
        <svg className="ox-bars" width={size} height={size} viewBox="0 0 32 32" role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
            <rect x="4" y="19" width="5" height="10" rx="1.5" />
            <rect x="13" y="12" width="5" height="17" rx="1.5" />
            <rect x="22" y="3" width="5" height="26" rx="1.5" />
        </svg>
    );
}

const PATHS = {
    chevron: <path d="m7 10 5 5 5-5" />,
    search: <><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4 4" /></>,
    plus: <path d="M12 5v14M5 12h14" />,
    table: <><rect x="4" y="5" width="16" height="14" rx="2" /><path d="M4 10h16M10 10v9" /></>,
    app: <><rect x="3.5" y="5" width="17" height="14" rx="2" /><path d="M3.5 9h17" /></>,
    clock: <><circle cx="12" cy="12" r="8" /><path d="M12 8v4l3 2" /></>,
    flow: <><circle cx="6" cy="6" r="2.2" /><circle cx="18" cy="18" r="2.2" /><path d="M8.2 6H14a3 3 0 0 1 3 3v6.8" /></>,
    skill: <><path d="M5 5.5A2 2 0 0 1 7 3.5h11v14H7a2 2 0 0 0-2 2z" /><path d="M5 19.5a2 2 0 0 0 2 2h11v-4" /></>,
    agent: <><circle cx="12" cy="9" r="3.4" /><path d="M5.5 20c1-3.6 3.6-5.4 6.5-5.4s5.5 1.8 6.5 5.4" /></>,
    plug: <><path d="M9 4v5M15 4v5M6.5 9h11v3a5.5 5.5 0 0 1-11 0z" /><path d="M12 17.5V21" /></>,
    file: <><path d="M6 3h8l4 4v14H6z" /><path d="M14 3v4h4" /></>,
    link: <><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" /><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" /></>,
    out: <><path d="M14 4h6v6" /><path d="M20 4l-9 9" /><path d="M18 14v5H5V6h5" /></>,
    chat: <path d="M5 5.5h14a1.5 1.5 0 0 1 1.5 1.5v8.5A1.5 1.5 0 0 1 19 17H10l-4.5 3.5V17H5a1.5 1.5 0 0 1-1.5-1.5V7A1.5 1.5 0 0 1 5 5.5z" />,
    people: <><circle cx="9" cy="9" r="3" /><path d="M3.5 19c.8-3 2.9-4.5 5.5-4.5s4.7 1.5 5.5 4.5" /><path d="M15.5 6.2a3 3 0 0 1 0 5.6M17.5 14.8c1.5.6 2.5 2 3 4.2" /></>,
    back: <path d="m14.5 6-6 6 6 6" />,
    next: <path d="m9.5 6 6 6-6 6" />,
    home: <><path d="M4.5 10.5 12 4.5l7.5 6V19a1 1 0 0 1-1 1h-4v-5.5h-5V20h-4a1 1 0 0 1-1-1z" /></>,
    inbox: <><path d="M4 13.5 6.2 6a1.5 1.5 0 0 1 1.4-1h8.8a1.5 1.5 0 0 1 1.4 1L20 13.5V18a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18z" /><path d="M4 13.5h4.5l1.2 2h4.6l1.2-2H20" /></>,
    grid: <><rect x="4.5" y="4.5" width="6" height="6" rx="1.5" /><rect x="13.5" y="4.5" width="6" height="6" rx="1.5" /><rect x="4.5" y="13.5" width="6" height="6" rx="1.5" /><rect x="13.5" y="13.5" width="6" height="6" rx="1.5" /></>,
    menu: <path d="M4.5 7h15M4.5 12h15M4.5 17h15" />,
    send: <path d="M12 19V5M6 11l6-6 6 6" />,
    more: <><circle cx="6" cy="12" r="1.4" /><circle cx="12" cy="12" r="1.4" /><circle cx="18" cy="12" r="1.4" /></>,
    gear: <><circle cx="12" cy="12" r="3" /><path d="M12 3.5v2.2M12 18.3v2.2M3.5 12h2.2M18.3 12h2.2M6 6l1.6 1.6M16.4 16.4 18 18M6 18l1.6-1.6M16.4 7.6 18 6" /></>,
} satisfies Record<string, ReactNode>;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 15 }: { name: IconName; size?: number }) {
    return (
        <svg className="ox-icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            {PATHS[name]}
        </svg>
    );
}

export function Avatar({ name, size = 24 }: { name: string; size?: number }) {
    const initial = name.trim().charAt(0).toUpperCase() || "?";
    return <span className="ox-avatar" style={{ width: size, height: size, fontSize: Math.round(size * 0.45) }} aria-hidden="true">{initial}</span>;
}

/** Two weeks of work, a bar a day: violet for what Lemma started, dark for
 *  what a person did. Scaled to its own busiest day, with the total said in
 *  words beside it, so a quiet outcome still shows its shape. */
export function ActivityChart({ days }: { days: readonly DayCount[] }) {
    const most = Math.max(1, ...days.map((day) => day.lemma + day.people));
    const lemma = days.reduce((sum, day) => sum + day.lemma, 0);
    const people = days.reduce((sum, day) => sum + day.people, 0);
    const width = 20;
    const height = 48;
    return (
        <svg className="ox-chart" viewBox={`0 0 ${days.length * width} ${height}`} role="img" aria-label={`Last ${days.length} days: ${lemma} started by Lemma, ${people} by people`}>
            {days.map((day, index) => {
                const x = index * width + 3;
                const total = day.lemma + day.people;
                if (total === 0) return <rect key={day.day} className="ox-chart__none" x={x} y={height - 2} width={width - 6} height={2} rx={1} />;
                const peopleHeight = (day.people / most) * (height - 4);
                const lemmaHeight = (day.lemma / most) * (height - 4);
                return (
                    <g key={day.day}>
                        {day.people > 0 && <rect className="ox-chart__people" x={x} y={height - peopleHeight} width={width - 6} height={peopleHeight} rx={2} />}
                        {day.lemma > 0 && <rect className="ox-chart__lemma" x={x} y={height - peopleHeight - lemmaHeight} width={width - 6} height={lemmaHeight} rx={2} />}
                    </g>
                );
            })}
        </svg>
    );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
    return (
        <div className="ox-empty">
            <p className="ox-empty__title">{title}</p>
            {children && <div className="ox-empty__body">{children}</div>}
        </div>
    );
}
