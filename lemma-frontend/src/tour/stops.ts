/** The tour: what Lemma is, in one card, and then where everything is —
 *  each stop pointing at the real control on screen rather than a drawing of
 *  it, so what is learned is the app itself.
 *
 *  The words echo the landing page ("Hire an AI teammate. Give it a space.",
 *  give it a job, let your people in, the tools the job needs) so somebody who
 *  arrives from there hears the same story told about their own screen. One
 *  sentence per stop: every extra one is a stop somebody skips.
 *
 *  "Teammate" appears where the tour names the category, as first contact
 *  and the rail do; everywhere else the teammate's own name does the work. */

export type StopId = "welcome" | "rail" | "places" | "ask" | "needs" | "share" | "about";

/** Where a stop's control lives, so the shell can put it on screen first:
 *  on a phone the sidebar is a drawer, and on a desktop it may be folded. */
export type StopArea = "none" | "sidebar" | "stage";

/** Which side of its control the card prefers; it flips when there is no
 *  space there. */
export type StopSide = "center" | "right" | "below" | "above";

export interface Stop {
    id: StopId;
    /** The `data-tour` value on the control it points at; none for the
     *  welcome, which points at nothing. */
    target: string | null;
    area: StopArea;
    side: StopSide;
    title: string;
    /** One sentence. */
    line: string;
}

export function tourStops({ name, org }: { name: string; org: string }): Stop[] {
    return [
        {
            id: "welcome", target: null, area: "none", side: "center",
            title: name + " is an AI teammate. This is its space.",
            line: "Give it work in a sentence; what it makes stays here, and everyone you let in works on it too.",
        },
        {
            id: "rail", target: "rail", area: "sidebar", side: "right",
            title: "Your teammates",
            line: "Each face is an AI teammate in " + org + ", and the mark at the top shows every one of them at once.",
        },
        {
            id: "places", target: "places", area: "sidebar", side: "right",
            title: "What " + name + " builds",
            line: "Pages, apps, tables, files and workflows, kept in " + name + "’s space, where your team works in them too.",
        },
        {
            id: "ask", target: "ask", area: "stage", side: "above",
            title: "Ask for anything",
            line: "Say what you need in plain words; your chats with " + name + " are kept in the sidebar.",
        },
        {
            id: "needs", target: "needs", area: "stage", side: "below",
            title: "What needs you",
            line: "When " + name + " needs an answer or your approval, it asks here and waits for you.",
        },
        {
            id: "share", target: "share", area: "stage", side: "below",
            title: "Let your people in",
            line: "Add the people " + name + " works with; each of them sees their part.",
        },
        {
            id: "about", target: "about", area: "sidebar", side: "right",
            title: "Get to know " + name,
            line: "Its name opens what it has been taught, the work it does on its own, and the channels where you can reach it.",
        },
    ];
}
