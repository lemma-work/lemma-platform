"use client";

import type { CSSProperties } from "react";
import { markTint } from "@/shell/mark";

/** An app's mark: a line drawing of what it is, on its own tint, in a
 *  circle. An app carries no icon of its own, so the drawing is read from
 *  its name -- a mail app gets an envelope, a deal app a pipeline -- and the
 *  tint from the same hash a Lemma's comes from, so two apps never look the
 *  same even when their names say nothing a drawing could show. */

export const DRAWINGS: Record<string, string> = {
    mail: "M3.5 6.5A2.5 2.5 0 0 1 6 4h12a2.5 2.5 0 0 1 2.5 2.5v11A2.5 2.5 0 0 1 18 20H6a2.5 2.5 0 0 1-2.5-2.5zM4.5 7l7.5 6 7.5-6",
    calendar: "M3.5 7.5A2.5 2.5 0 0 1 6 5h12a2.5 2.5 0 0 1 2.5 2.5v11A2.5 2.5 0 0 1 18 21H6a2.5 2.5 0 0 1-2.5-2.5zM3.5 10h17M8 3v4M16 3v4",
    sun: "M12 4v2M12 18v2M4 12h2M18 12h2M6.3 6.3l1.4 1.4M16.3 16.3l1.4 1.4M6.3 17.7l1.4-1.4M16.3 7.7l1.4-1.4M12 8.5a3.5 3.5 0 1 1 0 7 3.5 3.5 0 0 1 0-7z",
    people: "M9 8m-3.5 0a3.5 3.5 0 1 0 7 0a3.5 3.5 0 1 0-7 0M2.5 20c.8-3.5 3.4-5.5 6.5-5.5s5.7 2 6.5 5.5M16 4.6a3.5 3.5 0 0 1 0 6.8M18 14.8c1.9.8 3.1 2.6 3.5 5.2",
    list: "M9 6h11M9 12h11M9 18h11M4 6l1.2 1.2L7 5.4M4 12l1.2 1.2L7 11.4M4 18l1.2 1.2L7 17.4",
    pipeline: "M4 4h4v16H4zM10 4h4v11h-4zM16 4h4v7h-4z",
    book: "M3.5 7.5A2.5 2.5 0 0 1 6 5h12a2.5 2.5 0 0 1 2.5 2.5v11A2.5 2.5 0 0 1 18 21H6a2.5 2.5 0 0 1-2.5-2.5zM3.5 10h17M12 13v5M9.5 15.5h5",
    search: "M10.5 4a6.5 6.5 0 1 1 0 13 6.5 6.5 0 0 1 0-13zM15.5 15.5L20 20",
    palette: "M12 3.5a8.5 8.5 0 1 0 0 17c1.2 0 1.8-.8 1.8-1.7 0-1.1-.9-1.4-.9-2.4 0-1 .8-1.6 1.8-1.6h2.1a3.7 3.7 0 0 0 3.7-3.7C20.5 7 16.7 3.5 12 3.5zM7.5 11.5h.01M10 7.5h.01M15 7.5h.01",
    deck: "M4 5h16v11H4zM12 16v4M8 20h8",
    building: "M5 20V5.5A1.5 1.5 0 0 1 6.5 4h7A1.5 1.5 0 0 1 15 5.5V20M15 10h3.5A1.5 1.5 0 0 1 20 11.5V20M3.5 20h17M8.5 8h3M8.5 11.5h3M8.5 15h3",
    renew: "M19.5 12a7.5 7.5 0 0 1-13.2 4.9M4.5 12a7.5 7.5 0 0 1 13.2-4.9M17.7 3.5v3.6h-3.6M6.3 20.5v-3.6h3.6",
    review: "M5 4.5h10.5L19 8v11.5H5zM8.5 13l2.3 2.3 4.7-4.8",
    gear: "M12 9a3 3 0 1 1 0 6 3 3 0 0 1 0-6zM12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8",
    map: "M9 4.5 3.5 6.5v13l5.5-2 6 2 5.5-2v-13l-5.5 2zM9 4.5v13M15 6.5v13",
    server: "M4 5h16v5H4zM4 14h16v5H4zM7.5 7.5h.01M7.5 16.5h.01",
    chart: "M4 20V4M4 20h16M8 16l3.5-4 3 2.5L19 9",
    doc: "M6.5 3.5h7l4 4v13h-11zM13.5 3.5v4h4M9 12h6M9 15.5h6",
    chat: "M4 5.5h16v10H9l-5 4z",
    bag: "M5 8h14l-1.2 12H6.2zM9 8V6.5a3 3 0 0 1 6 0V8",
    star: "M12 4l2.4 5 5.3.6-4 3.6 1.1 5.3L12 15.8 7.2 18.5l1.1-5.3-4-3.6 5.3-.6z",
    bolt: "M13 3 5.5 13.5H12L11 21l7.5-10.5H12z",
};

/** Words in an app's name, and the drawing each one means. First match wins. */
const WORDS: readonly [RegExp, string][] = [
    [/mail|inbox|email/, "mail"],
    [/meeting|calendar|schedul/, "calendar"],
    [/today|daily|morning/, "sun"],
    [/people|person|contact|team|crew|hiring|candidate/, "people"],
    [/task|todo|to-do|checklist/, "list"],
    [/deal|pipeline|sales|crm/, "pipeline"],
    [/book|appointment/, "book"],
    [/lead|research|search|find/, "search"],
    [/brand|design|palette/, "palette"],
    [/deck|slide|present/, "deck"],
    [/compan|account|org/, "building"],
    [/renew|subscri/, "renew"],
    [/review|approv|check/, "review"],
    [/factory|build|engine/, "gear"],
    [/tour|map|guide/, "map"],
    [/server|infra|deploy/, "server"],
    [/report|metric|score|analytic|dashboard/, "chart"],
    [/doc|page|wiki|note/, "doc"],
    [/chat|support|help|ticket/, "chat"],
    [/shop|order|store|invoice|bill/, "bag"],
];

/** Drawings for a name that says nothing a drawing could show, picked by
 *  its hash so the same app always gets the same one. */
const SPARE = ["star", "bolt", "doc", "chart", "gear", "map"];

function hashOf(text: string): number {
    let hash = 0;
    for (let index = 0; index < text.length; index += 1) hash = (hash * 31 + text.charCodeAt(index)) | 0;
    return Math.abs(hash);
}

/** The drawing for an app, by its name and its label. */
export function drawingOf(name: string, label = ""): string {
    const words = (label + " " + name).toLowerCase().replace(/[-_]+/g, " ");
    for (const [match, drawing] of WORDS) if (match.test(words)) return drawing;
    return SPARE[hashOf(name) % SPARE.length];
}

/** The way in names its drawings after the plugins they stand for; an app
 *  that is one of those keeps that name, so the scene draws it the same. */
const PLUGIN_NAMES: Record<string, string> = {
    mail: "mail",
    calendar: "meetings",
    sun: "today",
    people: "people-map",
    list: "tasks",
    pipeline: "deal-tracker",
    book: "booking-page",
    search: "lead-research",
    palette: "brand-kit",
    deck: "deck-maker",
};

/** The name the scene around a Lemma's face draws an app by. */
export function sceneSlug(name: string, label = ""): string {
    const drawing = drawingOf(name, label);
    return PLUGIN_NAMES[drawing] ?? drawing;
}

/** An app's mark: its drawing in a circle of its own tint. */
export function AppMark({ name, label, size = 20, className }: { name: string; label?: string; size?: number; className?: string }) {
    const tint = markTint("app:" + name);
    const stroke = Math.max(10, Math.round(size * 0.58));
    return (
        <span
            className={"ox-appmark" + (className ? " " + className : "")}
            style={{ width: size, height: size, background: tint.background, color: tint.color } as CSSProperties}
            aria-hidden="true"
        >
            <svg width={stroke} height={stroke} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
                <path d={DRAWINGS[drawingOf(name, label)]} />
            </svg>
        </span>
    );
}

/** An app with no picture of itself yet: its tint, its drawing large, its
 *  name -- so a row of them still tells one from another. */
export function AppPoster({ name, label }: { name: string; label: string }) {
    const tint = markTint("app:" + name);
    return (
        <span className="ox-appposter" style={{ background: tint.background, color: tint.color }} aria-hidden="true">
            <svg width={34} height={34} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d={DRAWINGS[drawingOf(name, label)]} />
            </svg>
            <span className="ox-appposter__t">{label}</span>
        </span>
    );
}
