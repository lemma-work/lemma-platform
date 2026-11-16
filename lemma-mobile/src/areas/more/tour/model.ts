/** The tour and the guides, for a phone (the web's `tour/stops.ts`,
 *  `when.ts` and `guides.ts`).
 *
 *  The web's stops point at a sidebar, a rail and a space's tabs, none of
 *  which a phone has, so the stops are the phone's own: each one lights the
 *  real control on Home or in the tab bar, never a drawing of it, and says
 *  what it is for in one sentence. When the tour offers itself is the web's
 *  rule unchanged: once per person, in the account's first week. Pure, so
 *  `tests/more-tour.test.ts` holds it. */

/** A control a stop lights, registered by `TourTarget` where it is drawn. */
export type TargetId = "needs" | "ask" | "lemmas" | "apps" | "search" | "you";

export type StopId = "welcome" | TargetId;

export interface Stop {
    id: StopId;
    /** The control it points at; none for the welcome, which is centred. */
    target: TargetId | null;
    title: string;
    /** One sentence. */
    line: string;
}

export function tourStops(org: string | null): Stop[] {
    const at = org ? " at " + org : "";
    return [
        {
            id: "welcome", target: null,
            title: "Each Lemma owns something, and does the work it takes.",
            line: "A bar it holds every week, or a finish line by a date. It builds what it needs, runs it, and asks a person when it needs one.",
        },
        {
            id: "needs", target: "needs",
            title: "What needs you",
            line: "When a Lemma needs an answer or your yes, it asks here and waits, and you answer in place.",
        },
        {
            id: "ask", target: "ask",
            title: "Ask for anything",
            line: "Say what you need in plain words. The name in the box is the Lemma you are asking; tap it to ask another.",
        },
        {
            id: "lemmas", target: "lemmas",
            title: "Your Lemmas",
            line: "Every Lemma" + at + ", as faces or as where each stands. Tap one for what it needs from you, and open it for its chats, tables, files, runs and people.",
        },
        {
            id: "apps", target: "apps",
            title: "What they built",
            line: "The apps your Lemmas made, by Lemma, each one full screen.",
        },
        {
            id: "search", target: "search",
            title: "Find anything",
            line: "Files, tables, rows, chats, apps, agents, workflows, schedules and people, in every Lemma at once.",
        },
        {
            id: "you", target: "you",
            title: "You and your organization",
            line: "Your profile, the organization and its settings, and this tour again, under Help.",
        },
    ];
}

/** An account's first week: the web's `TOUR_WINDOW_MS`. */
export const TOUR_WINDOW_MS = 7 * 24 * 60 * 60 * 1000;

/** Whether the tour offers itself rather than waiting to be asked for:
 *  once, in the account's first week (the web's `offersTour`). */
export function offersTour(createdAt: string | null | undefined, { seen, now = Date.now() }: { seen: boolean; now?: number }): boolean {
    if (seen || !createdAt) return false;
    const created = Date.parse(createdAt);
    if (Number.isNaN(created)) return false;
    return now - created <= TOUR_WINDOW_MS;
}

export interface Box {
    x: number;
    y: number;
    width: number;
    height: number;
}

/** Around the control, inside the light. */
export const PAD = 6;
/** The card never closer than this to the screen's edge. */
export const EDGE = 12;

/** The lit box: the control's, padded, kept on screen. */
export function lit(box: Box, view: { width: number; height: number }): Box {
    const x = Math.max(2, box.x - PAD);
    const y = Math.max(2, box.y - PAD);
    const right = Math.min(view.width - 2, box.x + box.width + PAD);
    const bottom = Math.min(view.height - 2, box.y + box.height + PAD);
    return { x, y, width: Math.max(0, right - x), height: Math.max(0, bottom - y) };
}

/** Where the card goes: a sheet along whichever edge the control is not
 *  near, as the web places it on a narrow screen; centred when there is
 *  nothing to point at. `top` is null for "along the bottom". */
export function cardPlace(box: Box | null, view: { width: number; height: number }, insets: { top: number; bottom: number }): { edge: "center" | "top" | "bottom"; top: number | null; bottom: number | null } {
    if (!box) return { edge: "center", top: null, bottom: null };
    const low = box.y + box.height / 2 > view.height * 0.5;
    return low
        ? { edge: "top", top: insets.top + EDGE, bottom: null }
        : { edge: "bottom", top: null, bottom: insets.bottom + EDGE };
}

/** A rounded rectangle as an SVG path, for the hole the light is. */
function roundRect(box: Box, radius: number): string {
    const r = Math.max(0, Math.min(radius, box.width / 2, box.height / 2));
    const { x, y, width: w, height: h } = box;
    return `M${x + r},${y}H${x + w - r}A${r},${r} 0 0 1 ${x + w},${y + r}V${y + h - r}A${r},${r} 0 0 1 ${x + w - r},${y + h}H${x + r}A${r},${r} 0 0 1 ${x},${y + h - r}V${y + r}A${r},${r} 0 0 1 ${x + r},${y}Z`;
}

/** The dimmed screen with the control left lit: the whole screen, minus a
 *  rounded hole, filled even-odd. */
export function scrimPath(view: { width: number; height: number }, hole: Box | null): string {
    const outer = `M0,0H${view.width}V${view.height}H0Z`;
    return hole && hole.width > 0 && hole.height > 0 ? outer + roundRect(hole, 12) : outer;
}

/* ── how a place works ──────────────────────────────────────────────── */

export type GuidePlace = "pages" | "workflows" | "tables" | "apps";

export const GUIDE_PLACES: readonly GuidePlace[] = ["pages", "workflows", "tables", "apps"];

export function isGuidePlace(value: unknown): value is GuidePlace {
    return typeof value === "string" && (GUIDE_PLACES as readonly string[]).includes(value);
}

export interface GuideLine {
    /** What you do: a word to type, or the name of a control. */
    gesture: string;
    /** What happens, in one sentence. */
    what: string;
}

export type GuideTry =
    /** The page that explains pages by being one. */
    | { kind: "page-guide"; label: string }
    /** Words for the Lemma, put in a new chat's box and never sent from here. */
    | { kind: "ask"; label: string; text: string };

export interface Guide {
    place: GuidePlace;
    title: string;
    lines: GuideLine[];
    tryIt?: GuideTry;
}

const TITLES: Record<GuidePlace, string> = {
    pages: "How pages work",
    workflows: "How workflows work",
    tables: "How tables work",
    apps: "How apps work",
};

export function guideTitle(place: GuidePlace): string {
    return TITLES[place];
}

/** The web's guides, said for a phone: the gestures are a thumb's, and the
 *  places are a Lemma's sections. `name` is the Lemma's when one is chosen. */
export function guideFor(place: GuidePlace, name: string | null): Guide {
    const it = name ?? "the Lemma";
    const title = TITLES[place];
    switch (place) {
        case "pages":
            return {
                place, title,
                lines: [
                    { gesture: "Files", what: "Pages come first in a Lemma’s Files, and + starts a new one, blank or from a template." },
                    { gesture: "/", what: "On an empty line, add a block: a heading, a to-do, a live table view, or a chart " + it + " draws." },
                    { gesture: "Select", what: "Select words to ask " + it + " to change them, or to comment on them." },
                    { gesture: "@" + (name ?? "name"), what: "Mention " + it + " in a comment, and it answers in the thread." },
                    { gesture: "Hold", what: "Hold a file for Share, Rename, Move and the rest." },
                ],
                tryIt: { kind: "page-guide", label: "Open the guide page" },
            };
        case "workflows":
            return {
                place, title,
                lines: [
                    { gesture: "Steps", what: "A workflow runs its steps in order, and a step can be " + it + ", a function, a decision or a person." },
                    { gesture: "Waits", what: "A step for a person waits until it is answered, and shows on Home under what needs you." },
                    { gesture: "Each person", what: "Everyone turns it on for themselves, and it runs as them, with their own accounts." },
                    { gesture: "For everyone", what: "One switch for everyone, and it runs as whoever turned it on." },
                    { gesture: "Runs", what: "Every run has its own screen, with what each step did and where it stopped." },
                ],
                tryIt: {
                    kind: "ask", label: "Try one",
                    text: "Make a small practice workflow that asks me one question, waits for my answer, and writes it on a page; then run it once so I can see it wait for me.",
                },
            };
        case "tables":
            return {
                place, title,
                lines: [
                    { gesture: "Layouts", what: "A table shows its rows as a board, a timeline, a checklist or cards when they suit one, and as plain rows when not." },
                    { gesture: "Rows", what: "Tap a row to see the whole of it, with everything it links to; hold it to edit or delete it." },
                    { gesture: "Own rows", what: "On a table marked so, each person sees only their own rows." },
                    { gesture: "Collect", what: "Collect responses lets people outside add rows through a form you share as a link." },
                    { gesture: "Ask", what: "The box at the foot of a Lemma asks about the rows, or asks " + it + " to change them." },
                ],
                tryIt: {
                    kind: "ask", label: "Try one",
                    text: "Set up a small practice table of our tasks with a few example rows, so I can try its layouts.",
                },
            };
        case "apps":
            return {
                place, title,
                lines: [
                    { gesture: "Describe", what: "Tell " + it + " what the app is for, and it builds it." },
                    { gesture: "Apps", what: "The Apps tab, and each Lemma’s own, open every app full screen." },
                    { gesture: "Tables", what: "An app reads and writes the Lemma’s tables, as whoever is using it." },
                    { gesture: "Ask", what: "Ask " + it + " to change an app in plain words, and open it again to see." },
                ],
                tryIt: {
                    kind: "ask", label: "Try one",
                    text: "Build me a small app that lists our open tasks and lets me tick one off.",
                },
            };
    }
}
