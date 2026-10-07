import { toolKey } from "./tool-name";
import type { RawMessage } from "./turns";

/** What a finished run may have changed in the space's lists.
 *
 *  Every run used to refresh every list — the library, the apps, the
 *  workflows, the schedules and each open table, every page of rows it had
 *  loaded — and most runs are an answer: no tool at all, or tools that only
 *  read. Those change nothing a list shows. File tools change the library and
 *  nothing else. Anything that runs a command can do anything the CLI can,
 *  and so can a tool this does not know; both still refresh everything. */
export type RunTouched = "nothing" | "files" | "everything";

const READS = new Set([
    "read_file", "list_files", "glob", "grep", "view_image",
    "web_search", "web_fetch",
    "browser_open", "browser_read", "browser_snapshot", "browser_screenshot", "browser_act", "browser_sign_in",
    "ask_user", "display_resource", "note_to_self", "wait", "wait_for",
]);
const FILE_WRITES = new Set(["write_file", "edit_file", "delete_file", "move_file"]);

/** Read over the messages the run added: the ones not already there when it
 *  started, by id. Ids rather than positions, because a reload replaces the
 *  list and a message sent mid-run lands in the middle of it. */
export function runTouched(messages: readonly RawMessage[], before: ReadonlySet<string>): RunTouched {
    let touched: RunTouched = "nothing";
    for (const message of messages) {
        if (!message.tool_name || (message.id && before.has(message.id))) continue;
        const key = toolKey(message.tool_name, message.metadata);
        /* Somebody else's tool — GitHub's, Linear's — writes somewhere else. */
        if (!key || READS.has(key) || key.startsWith("plan") || key.startsWith("approval")) continue;
        if (FILE_WRITES.has(key)) { touched = "files"; continue; }
        return "everything";
    }
    return touched;
}

/** The ids a run starts beside. */
export function messageIds(messages: readonly RawMessage[]): Set<string> {
    return new Set(messages.flatMap((message) => message.id ? [message.id] : []));
}
