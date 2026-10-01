/** What each place in a space says while there is nothing in it.
 *
 *  An empty list is the first thing most people see of every place in a new
 *  space, so it is where the model gets taught or does not: what lives here,
 *  and how it gets here — mostly because the teammate makes it. One sentence
 *  says that, and every place offers at least one way to fill it that costs a
 *  click and no typing. A line that only says "Nothing here yet" leaves a
 *  person to guess both.
 *
 *  Kept apart from the component so the words can be read, and tested, as a
 *  set: they are one voice, and one place drifting into a paragraph, or
 *  calling the teammate "the bot", is easiest to catch side by side.
 */

/** The picture above the words: what the place will look like once it has
 *  something in it, drawn in the page's own greys. */
export type EmptyArt = "pages" | "apps" | "tables" | "files" | "folder" | "workflows" | "chats" | "rows";

/** Something the person can do from an empty place. The shell decides what
 *  each one does; a place whose shell has no handler for an action simply
 *  does not show it. */
export type EmptyAction =
    | { kind: "page"; label: string }
    | { kind: "upload"; label: string }
    | { kind: "chat"; label: string }
    | { kind: "row"; label: string }
    | { kind: "reach"; label: string }
    | { kind: "pages"; label: string }
    /** Words for the teammate, put in the chat box to finish or send.
     *  Never sent from here: the person decides. */
    | { kind: "ask"; label: string; text: string };

export interface Empty {
    art: EmptyArt;
    title: string;
    /** One sentence. */
    line: string;
    primary: EmptyAction;
    /** Ready-made asks, each one line. At least one action on every place
     *  needs no typing at all: a button, or an ask that is a whole sentence
     *  and can be sent as it stands. An ask that ends mid-sentence is for
     *  somebody who already knows what they want. */
    starters: EmptyAction[];
    /** A catalog of ready-made starts the place shows beneath its empty
     *  state, where it has one: the page templates, the app ideas. Each is a
     *  single click, so a place with one needs no starters of its own. */
    follow?: "templates" | "ideas";
}

export type EmptyPlace =
    | { place: "pages" | "apps" | "tables" | "workflows" | "rows" | "all" }
    | { place: "files"; scope: "shared" | "personal"; folder: boolean }
    | { place: "chats"; filter: "all" | "chats" | "channels" | "automations" | "docs" };

/** `name` is the teammate's, which does the work "teammate" would otherwise
 *  do: inside the app the category word is only for naming the category. */
export function emptyFor(where: EmptyPlace, name: string): Empty {
    switch (where.place) {
        case "pages":
            return {
                art: "pages",
                title: "No pages yet",
                line: "Pages are docs everyone here can open, and " + name + " reads them and writes its own.",
                primary: { kind: "page", label: "New page" },
                starters: [
                    { kind: "ask", label: "Ask " + name + " to write one", text: "Write a page about " },
                ],
                follow: "templates",
            };
        case "apps":
            return {
                art: "apps",
                title: "No apps yet",
                line: name + " builds the apps the work needs, and your team works in them too.",
                primary: { kind: "ask", label: "Describe an app", text: "Build an app that " },
                starters: [],
                follow: "ideas",
            };
        case "tables":
            return {
                art: "tables",
                title: "No tables yet",
                line: "Tables keep what your team tracks, one row each; " + name + " sets them up and fills them in.",
                primary: { kind: "ask", label: "Ask " + name + " to set one up", text: "Set up a table to track " },
                starters: [
                    { kind: "ask", label: "Tasks", text: "Set up a table for our tasks, with a title, an owner, a due date and a status." },
                    { kind: "ask", label: "Contacts", text: "Set up a table for the people we work with: name, company, email and notes." },
                ],
            };
        case "files":
            if (where.folder) {
                return { art: "folder", title: "This folder is empty", line: "Upload files into it, or drop them here.", primary: { kind: "upload", label: "Upload files" }, starters: [] };
            }
            if (where.scope === "personal") {
                return {
                    art: "folder",
                    title: "Nothing of yours yet",
                    line: "Only you can open files here, and " + name + " reads them when you ask.",
                    primary: { kind: "upload", label: "Upload files" },
                    starters: [],
                };
            }
            return {
                art: "files",
                title: "No files yet",
                line: "Upload PDFs, docs, sheets or images for " + name + " to read; what it makes lands here too.",
                primary: { kind: "upload", label: "Upload files" },
                starters: [],
            };
        case "workflows":
            return {
                art: "workflows",
                title: "No workflows yet",
                line: "Say what should happen, step by step, and " + name + " builds a workflow that runs on its own and stops for your say.",
                primary: { kind: "ask", label: "Ask " + name + " to make one", text: "Make a workflow that " },
                starters: [
                    { kind: "ask", label: "A weekly summary", text: "Make a workflow that every Monday morning sums up what changed here last week and sends it to me." },
                    { kind: "ask", label: "Ask me first", text: "Make a workflow that asks for my approval before " },
                ],
            };
        case "rows":
            return {
                art: "rows",
                title: "No rows yet",
                line: "Add the first one yourself, or ask " + name + " to fill it in.",
                primary: { kind: "row", label: "New row" },
                starters: [
                    { kind: "ask", label: "Ask " + name + " to fill it in", text: "Add rows to this table from " },
                ],
            };
        case "chats":
            return chatsEmpty(where.filter, name);
        case "all":
            return {
                art: "pages",
                title: "Nothing here yet",
                line: "The pages, apps, tables and files " + name + " and your team make show up here.",
                primary: { kind: "page", label: "New page" },
                starters: [],
            };
    }
}

function chatsEmpty(filter: "all" | "chats" | "channels" | "automations" | "docs", name: string): Empty {
    if (filter === "channels") {
        return {
            art: "chats",
            title: "Nothing from channels yet",
            line: "Once " + name + " is on a channel, the conversations there land here.",
            primary: { kind: "reach", label: "Connect a channel" },
            starters: [],
        };
    }
    if (filter === "automations") {
        return {
            art: "chats",
            title: "Nothing has run on its own yet",
            line: "Schedules and workflows start their own conversations, and they land here.",
            primary: { kind: "ask", label: "Schedule a morning brief", text: "Every weekday at 9am, send me a short brief of what changed here since the day before." },
            starters: [],
        };
    }
    if (filter === "docs") {
        return {
            art: "chats",
            title: "No conversations on pages yet",
            line: "Ask about a page from the page itself, and the conversation lands here.",
            primary: { kind: "pages", label: "Open pages" },
            starters: [],
        };
    }
    return {
        art: "chats",
        title: "No chats yet",
        line: "Your conversations with " + name + " are kept here, with the ones a channel or a schedule starts.",
        primary: { kind: "chat", label: "New chat" },
        starters: [],
    };
}
