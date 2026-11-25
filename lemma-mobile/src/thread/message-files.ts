import { NOTHING_TYPED } from "./attachments";
import type { DisplayResource } from "@web/thread/display-resource";
import { fileKind } from "./previews";
import type { TurnItem } from "@web/thread/turns";

/** Files in a chat drawn as files rather than as lines of text.
 *
 *  A person's attachments travel at the end of their message, one line per
 *  file under "Personal files available to this run:" (`withReferences`),
 *  which is what the agent reads; the phone draws those lines as photos and
 *  cards instead, and the words above them as the message. Pictures the
 *  Lemma put on screen one after another are one grid.
 *
 *  Pure, so the tests read it directly. */

const HEADER = "Personal files available to this run:";

export interface MessageFile {
    name: string;
    path: string;
}

export function filesOfMessage(text: string): { text: string; files: MessageFile[] } {
    const at = text.lastIndexOf(HEADER);
    if (at < 0) return { text, files: [] };
    const lines = text.slice(at + HEADER.length).split("\n").map((line) => line.trim()).filter(Boolean);
    const files: MessageFile[] = [];
    for (const line of lines) {
        const match = /^- (.+?): (\/\S.*)$/.exec(line);
        /* Anything else after the header: not the list this app writes. */
        if (!match) return { text, files: [] };
        files.push({ name: match[1], path: match[2] });
    }
    const said = text.slice(0, at).trimEnd();
    /* "Please use the attached files." is what the phone sends when nothing
       was typed: the files say it. */
    return { text: said === NOTHING_TYPED ? "" : said, files };
}

export function isPicture(path: string): boolean {
    return fileKind("", path) === "image";
}

export type DrawnItem = TurnItem | { kind: "photos"; id: string; paths: string[]; toolCallIds: (string | undefined)[] };

function pictureOf(item: TurnItem): DisplayResource | null {
    if (item.kind !== "resource" || item.resource.type !== "FILE" || !item.resource.path) return null;
    return isPicture(item.resource.path) ? item.resource : null;
}

/** Two or more pictures shown one after another, as one grid. */
export function withPhotoRuns(items: readonly TurnItem[]): DrawnItem[] {
    const out: DrawnItem[] = [];
    let run: (TurnItem & { kind: "resource" })[] = [];
    const flush = () => {
        if (run.length >= 2) {
            out.push({ kind: "photos", id: "photos:" + run[0].id, paths: run.map((one) => one.resource.path as string), toolCallIds: run.map((one) => one.toolCallId) });
        } else out.push(...run);
        run = [];
    };
    for (const item of items) {
        if (pictureOf(item) && item.kind === "resource") {
            run.push(item);
            continue;
        }
        flush();
        out.push(item);
    }
    flush();
    return out;
}
