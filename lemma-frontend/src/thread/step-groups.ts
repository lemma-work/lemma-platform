import type { Note } from "./turns";

/** What a run of steps did, as one line: "Ran 4 commands", "Read a file,
 *  searched the web", or -- for one step the agent described itself -- its
 *  own sentence.
 *
 *  Said the way a person reports work, not as the tools' names: "Exec command
 *  · Exec command · Web search" is a log, and the line is meant to be read
 *  in passing between two things the agent said. Thinking is part of the
 *  work but not a thing it did, so it is counted only when it is all there
 *  is. A tool this table does not know keeps its own name, with how many
 *  times.
 *
 *  Past tense for work that is over, and the present for the one step a live
 *  run is on: "Searched the web 5 times" above, "Searching the web" on the
 *  line that is still ticking. */

/** One kind of step as said: done once, done `{n}` times, and in hand. */
type Phrase = readonly [one: string, many: string, doing: string];

const RAN_COMMANDS = ["Ran a command", "Ran {n} commands", "Running a command"] as const;
const RAN_PYTHON = ["Ran some Python", "Ran Python {n} times", "Running Python"] as const;
const SEARCHED_WEB = ["Searched the web", "Searched the web {n} times", "Searching the web"] as const;
const READ_PAGES = ["Read a web page", "Read {n} web pages", "Reading a web page"] as const;
const READ_FILES = ["Read a file", "Read {n} files", "Reading a file"] as const;
const WROTE_FILES = ["Wrote a file", "Wrote {n} files", "Writing a file"] as const;
const EDITED_FILES = ["Edited a file", "Edited {n} files", "Editing a file"] as const;
const USED_BROWSER = ["Used the browser", "Used the browser {n} times", "Using the browser"] as const;

const PHRASES: Readonly<Record<string, Phrase>> = {
    exec_command: RAN_COMMANDS,
    shell: RAN_COMMANDS,
    terminal: RAN_COMMANDS,
    bash: RAN_COMMANDS,
    execute_python: RAN_PYTHON,
    python: RAN_PYTHON,
    web_search: SEARCHED_WEB,
    search: SEARCHED_WEB,
    websearch: SEARCHED_WEB,
    web_fetch: READ_PAGES,
    fetch: READ_PAGES,
    webfetch: READ_PAGES,
    read_file: READ_FILES,
    read: READ_FILES,
    write_file: WROTE_FILES,
    write: WROTE_FILES,
    edit_file: EDITED_FILES,
    edit: EDITED_FILES,
    multiedit: EDITED_FILES,
    delete_file: ["Deleted a file", "Deleted {n} files", "Deleting a file"],
    move_file: ["Moved a file", "Moved {n} files", "Moving a file"],
    list_files: ["Listed files", "Listed files {n} times", "Listing files"],
    glob: ["Looked for files", "Looked for files {n} times", "Looking for files"],
    grep: ["Searched in files", "Searched in files {n} times", "Searching in files"],
    browser: USED_BROWSER,
    browser_open: USED_BROWSER,
    browser_act: USED_BROWSER,
    browser_read: USED_BROWSER,
    browser_snapshot: USED_BROWSER,
    browser_screenshot: USED_BROWSER,
    view_image: ["Looked at an image", "Looked at {n} images", "Looking at an image"],
    task: ["Started a sub-agent", "Started {n} sub-agents", "Starting a sub-agent"],
    subagent: ["Started a sub-agent", "Started {n} sub-agents", "Starting a sub-agent"],
    spawn_subagent: ["Started a sub-agent", "Started {n} sub-agents", "Starting a sub-agent"],
    interact_subagent: ["Worked with a sub-agent", "Worked with sub-agents {n} times", "Working with a sub-agent"],
    query_subagents: ["Checked on sub-agents", "Checked on sub-agents {n} times", "Checking on sub-agents"],
    ask_teammate: ["Asked another Lemma", "Asked other Lemmas {n} times", "Asking another Lemma"],
    run_connector_operation: ["Used a connector", "Used connectors {n} times", "Using a connector"],
    search_connector_operations: ["Looked up a connector", "Looked up connectors {n} times", "Looking up a connector"],
    describe_connector_operation: ["Looked up a connector", "Looked up connectors {n} times", "Looking up a connector"],
    note_to_self: ["Made a note", "Made {n} notes", "Making a note"],
    wait_for: ["Waited", "Waited {n} times", "Waiting"],
    /* Lemma's own tools, on its files, tables and chats. */
    pod_read_file: READ_FILES,
    pod_write_file: WROTE_FILES,
    pod_edit_file: EDITED_FILES,
    pod_list_files: ["Listed files", "Listed files {n} times", "Listing files"],
    pod_search_files: ["Searched files", "Searched files {n} times", "Searching files"],
    pod_view_document_pages: ["Read a document", "Read {n} documents", "Reading a document"],
    pod_get_file_url: ["Got a file's link", "Got {n} file links", "Getting a file's link"],
    pod_query: ["Ran a query", "Ran {n} queries", "Running a query"],
    pod_get_records: ["Read records", "Read records {n} times", "Reading records"],
    pod_write_record: ["Saved a record", "Saved {n} records", "Saving a record"],
    pod_tables: ["Looked at the tables", "Looked at the tables {n} times", "Looking at the tables"],
    channel_read: ["Read a chat", "Read {n} chats", "Reading a chat"],
    channel_post: ["Posted in a chat", "Posted {n} times", "Posting in a chat"],
    channel_search: ["Searched chats", "Searched chats {n} times", "Searching chats"],
    channel_list: ["Listed chats", "Listed chats {n} times", "Listing chats"],
    load_skill: ["Loaded a skill", "Loaded {n} skills", "Loading a skill"],
    ask_a_person: ["Asked a person", "Asked people {n} times", "Asking a person"],
};

/** What a step was done to, read from its arguments: a file by its name, a
 *  page by its site, a search by its words, a command as typed, a skill by
 *  its name. */
type Of = "file" | "site" | "words" | "command" | "name";

/** A step said by what it was done to -- "Read design-direction.md",
 *  "Searching the web for “AI agents”" -- done and in hand. Better than "Read
 *  a file" whenever the step says which, and every one of these does. */
type Named = readonly [of: Of, done: string, doing: string];

const READ_NAMED: Named = ["file", "Read {x}", "Reading {x}"];
const WROTE_NAMED: Named = ["file", "Wrote {x}", "Writing {x}"];
const EDITED_NAMED: Named = ["file", "Edited {x}", "Editing {x}"];
const LISTED_NAMED: Named = ["file", "Listed {x}", "Listing {x}"];
const SEARCHED_WEB_NAMED: Named = ["words", "Searched the web for “{x}”", "Searching the web for “{x}”"];
const SEARCHED_FILES_NAMED: Named = ["words", "Searched files for “{x}”", "Searching files for “{x}”"];
const READ_PAGE_NAMED: Named = ["site", "Read {x}", "Reading {x}"];
const RAN_NAMED: Named = ["command", "Ran {x}", "Running {x}"];

const NAMED: Readonly<Record<string, Named>> = {
    read_file: READ_NAMED,
    read: READ_NAMED,
    pod_read_file: READ_NAMED,
    pod_view_document_pages: READ_NAMED,
    write_file: WROTE_NAMED,
    write: WROTE_NAMED,
    pod_write_file: WROTE_NAMED,
    edit_file: EDITED_NAMED,
    edit: EDITED_NAMED,
    multiedit: EDITED_NAMED,
    pod_edit_file: EDITED_NAMED,
    delete_file: ["file", "Deleted {x}", "Deleting {x}"],
    view_image: ["file", "Looked at {x}", "Looking at {x}"],
    list_files: LISTED_NAMED,
    pod_list_files: LISTED_NAMED,
    web_search: SEARCHED_WEB_NAMED,
    search: SEARCHED_WEB_NAMED,
    websearch: SEARCHED_WEB_NAMED,
    grep: SEARCHED_FILES_NAMED,
    pod_search_files: SEARCHED_FILES_NAMED,
    glob: ["words", "Looked for {x}", "Looking for {x}"],
    channel_search: ["words", "Searched chats for “{x}”", "Searching chats for “{x}”"],
    web_fetch: READ_PAGE_NAMED,
    fetch: READ_PAGE_NAMED,
    webfetch: READ_PAGE_NAMED,
    browser_open: ["site", "Opened {x}", "Opening {x}"],
    exec_command: RAN_NAMED,
    shell: RAN_NAMED,
    terminal: RAN_NAMED,
    bash: RAN_NAMED,
    load_skill: ["name", "Loaded the {x} skill", "Loading the {x} skill"],
};

function clip(text: string, most: number): string {
    return text.length > most ? text.slice(0, most - 1).trimEnd() + "…" : text;
}

/** What a step of this tool was done to, from the arguments it was called
 *  with; "" for a tool not said that way, or a call that does not say. */
export function subjectOf(key: string | undefined, args: unknown): string {
    const named = key ? NAMED[key] : undefined;
    if (!named || !args || typeof args !== "object") return "";
    const record = args as Record<string, unknown>;
    const first = (...keys: string[]) => {
        for (const one of keys) {
            const value = record[one];
            if (typeof value === "string" && value.trim()) return value.replace(/\s+/g, " ").trim();
        }
        return "";
    };
    switch (named[0]) {
        case "file":
            return first("path", "file_path", "filepath", "notebook_path").replace(/\/+$/, "").split("/").pop() ?? "";
        case "site": {
            const url = first("url");
            try {
                return new URL(url).host.replace(/^www\./, "");
            } catch {
                return url;
            }
        }
        case "words":
            return clip(first("query", "pattern", "q"), 60);
        case "command":
            return clip(first("command", "cmd"), 80);
        case "name":
            return first("name", "skill", "skill_name");
    }
}

/** A step said by what it was done to, when it can be: done (1) or in hand
 *  (2). */
function named(note: Note, tense: 1 | 2): string | null {
    const form = note.key && note.subject ? NAMED[note.key] : undefined;
    return form ? form[tense].replace("{x}", note.subject ?? "") : null;
}

/** How many phrases a line says before it trails off. */
const SAID = 3;

function lowerFirst(phrase: string): string {
    /* An acronym stays as it is: "API call", not "aPI call". */
    return /^[A-Z]{2}/.test(phrase) ? phrase : phrase.charAt(0).toLowerCase() + phrase.slice(1);
}

export function stepsSaid(notes: readonly Note[]): string {
    const tools = notes.filter((note) => note.kind === "tool");
    if (tools.length === 0) return notes.length > 0 ? "Thought" : "";
    /* One step, in the agent's own words: it said what it was doing. */
    if (tools.length === 1 && tools[0].said && tools[0].detail) return tools[0].detail;
    const counts = new Map<string, { phrase: Phrase | null; first: Note; n: number }>();
    for (const note of tools) {
        const phrase = note.key ? PHRASES[note.key] ?? null : null;
        /* Tools that read the same way are one phrase: three `shell`s and an
           `exec_command` "Ran 4 commands". */
        const id = phrase ? phrase[0] : "label:" + note.label;
        const held = counts.get(id) ?? { phrase, first: note, n: 0 };
        held.n += 1;
        counts.set(id, held);
    }
    const parts = [...counts.values()].map(({ phrase, first, n }) => {
        /* Done once, it is said by what it was done to: "read
           design-direction.md", not "read a file". */
        if (n === 1) return named(first, 1) ?? (phrase ? phrase[0] : first.label);
        return phrase ? phrase[1].replace("{n}", String(n)) : first.label + " ×" + n;
    });
    const shown = parts.slice(0, SAID).map((part, index) => (index === 0 ? part : lowerFirst(part)));
    return shown.join(", ") + (parts.length > SAID ? ", and more" : "");
}

/** One step as a line in an opened run: the agent's own sentence for it;
 *  else what it did to what -- "Read design-direction.md"; else, for a tool
 *  that does not say, what it did and its arguments after, quieter. A
 *  thought is a thought, with the start of it after. The rest -- a card, a
 *  whole thought -- is for opening the line, not for reading past. */
export function stepLine(note: Note): { what: string; on: string } {
    if (note.kind === "thought") return { what: "Thought", on: note.detail.trim().split("\n")[0] ?? "" };
    if (note.said && note.detail) return { what: note.detail, on: "" };
    const said = named(note, 1);
    if (said) return { what: said, on: "" };
    const phrase = note.key ? PHRASES[note.key] : undefined;
    return { what: phrase ? phrase[0] : note.label, on: note.detail };
}

/** What the run is doing right now, for the line under a live reply, said
 *  of the latest step only -- an earlier one is over, and naming it is how
 *  the line once sat on a search finished minutes before. The agent's own
 *  sentence for it when it gave one; else the step in the present tense,
 *  by what it is being done to when it says -- "Reading design-direction.md"
 *  -- while it is still running; else, between steps, that it is thinking. */
export function stepNow(notes: readonly Note[]): string {
    const now = notes[notes.length - 1];
    if (!now) return "Working";
    if (now.kind === "thought" || !now.running) return "Thinking";
    if (now.said && now.detail) return now.detail;
    return named(now, 2) ?? (now.key ? PHRASES[now.key]?.[2] : undefined) ?? now.label;
}
