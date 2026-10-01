/** How a place works, for somebody standing in it.
 *
 *  The tour says where everything is; a guide says what one place can do —
 *  the gestures nothing on screen advertises until you know them. Opened from
 *  the help menu, whose first item follows where you are, and from a place's
 *  empty state; never on its own. Somebody who opened a page came to write
 *  in it, not to be taught pages.
 *
 *  A line is a gesture and what it does, in one sentence. A place whose
 *  every part already says what it is has no guide: About carries a note
 *  under each of its sections, and a card would only repeat the page. */

export type GuidePlace = "pages" | "workflows" | "tables" | "apps";

export interface GuideLine {
    /** What you do: a key, a word to type, or the name of a control. */
    gesture: string;
    /** What happens, in one sentence. */
    what: string;
}

/** Learning a place by using it. */
export type GuideTry =
    /** "Your guide to pages": a page that explains pages by being one. */
    | { kind: "page-guide"; label: string }
    /** Words for the teammate, put in the chat box and never sent from here. */
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

/** `name` is the teammate's, which does the work "teammate" would otherwise
 *  do: inside the app the category word is only for naming the category. */
export function guideFor(place: GuidePlace, name: string): Guide {
    const title = TITLES[place];
    switch (place) {
        case "pages":
            return {
                place, title,
                lines: [
                    { gesture: "/", what: "On any line, add a block: a heading, a to-do, a sub-page, a live table view, or a chart " + name + " draws." },
                    { gesture: "Select", what: "Select words to ask " + name + " to change them, or to comment on them." },
                    { gesture: "@" + name, what: "Mention " + name + " in a comment, and it answers in the thread." },
                    { gesture: "Ask", what: "Bottom right, talk about the page you have open, and " + name + " edits it in place." },
                ],
                tryIt: { kind: "page-guide", label: "Open the guide page" },
            };
        case "workflows":
            return {
                place, title,
                lines: [
                    { gesture: "Steps", what: "A workflow runs its steps in order, and a step can be " + name + ", a function, a decision or a person." },
                    { gesture: "Waits", what: "A step for a person waits until it is answered, and shows under Waiting on you." },
                    { gesture: "Each person", what: "Everyone turns it on for themselves, and it runs as them, with their own accounts." },
                    { gesture: "Admin", what: "One switch for everyone, and it runs as whoever turned it on." },
                    { gesture: "Runs", what: "Every run has a page that shows what each step did and where it stopped." },
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
                    { gesture: "Views", what: "A table shows its rows as a board, a timeline or a checklist when they suit one, and Show as table switches back." },
                    { gesture: "Filter", what: "Narrow the rows with the filters above them, or search the rows that have loaded." },
                    { gesture: "Rows", what: "Open a row to see the whole of it, with everything it links to." },
                    { gesture: "RLS", what: "On a table marked RLS, each person sees only their own rows." },
                    { gesture: "Ask", what: "Bottom right, ask about the rows, or ask " + name + " to change them." },
                ],
                tryIt: {
                    kind: "ask", label: "Try one",
                    text: "Set up a small practice table of our tasks with a few example rows, so I can try its views.",
                },
            };
        case "apps":
            return {
                place, title,
                lines: [
                    { gesture: "Describe", what: "Tell " + name + " what the app is for, and it builds it here." },
                    { gesture: "Ideas", what: "Under the list, choose an example, and its request goes in the chat for you to send." },
                    { gesture: "Tables", what: "An app reads and writes the tables here, as whoever is using it." },
                    { gesture: "Ask", what: "Bottom right, ask about the app on screen, or ask " + name + " to change it." },
                ],
            };
    }
}

/** The guide for what is on screen, if it has one. A page is any markdown
 *  file, wherever it is kept; the lists and the things opened from them
 *  share their list's guide. */
export function placeOf(tab: { id: string; kind: string; path?: string } | undefined): GuidePlace | null {
    if (!tab) return null;
    if (tab.id === "space:pages" || (tab.kind === "file" && /\.(md|markdown)$/i.test(tab.path ?? ""))) return "pages";
    if (tab.id === "space:workflows" || tab.kind === "workflow" || tab.kind === "run") return "workflows";
    if (tab.id === "space:tables" || tab.kind === "table" || tab.kind === "record") return "tables";
    if (tab.id === "space:apps" || tab.kind === "app") return "apps";
    return null;
}
