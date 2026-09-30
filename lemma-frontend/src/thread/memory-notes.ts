import type { LibraryItem } from "@/data/types";
import { toolKey } from "./tool-name";

/** What a teammate has written down, read from where it writes it.
 *
 *  The teammate keeps notes as files, in folders the backend fixes
 *  (`agent/domain/agent_memory_paths.py`): `/memory` is shared with everyone
 *  in the space, `/me` is private to one person, and each agent has a folder
 *  of its own under both. The space's own agent is `pod_default`, which
 *  slugifies to `pod-default`. `AGENTS.md` in each folder is an index of
 *  pointers, not a note, so it is never shown as one.
 *
 *  Nothing here asks the model anything. A note exists because a file does,
 *  and it changed when the file did. */

const TEAMMATE_FOLDER = "pod-default";

/** The folders that hold the teammate's own notes, and whether each is private. */
export const MEMORY_FOLDERS: readonly { path: string; private: boolean }[] = [
    { path: "/memory", private: false },
    { path: "/memory/agents/" + TEAMMATE_FOLDER, private: false },
    { path: "/me/agents/" + TEAMMATE_FOLDER, private: true },
];

export interface MemoryNote {
    path: string;
    /** "Launch checks", from `launch-checks.md`. */
    topic: string;
    /** The one line the file was written with, when it has one. */
    gloss: string;
    /** ISO. */
    updated: string;
    private: boolean;
}

const DAY = 86_400_000;

/** Whether a path is somewhere an agent keeps memory, in any agent's folder. */
export function isMemoryPath(path: string): boolean {
    return path === "/memory" || path.startsWith("/memory/") || path === "/me/AGENTS.md" || path.startsWith("/me/agents/");
}

function isIndex(path: string): boolean {
    return path === "AGENTS.md" || path.endsWith("/AGENTS.md");
}

/** "launch-checks.md" → "Launch checks". */
export function topicOf(path: string): string {
    const base = (path.split("/").pop() ?? path).replace(/\.(md|markdown|txt)$/i, "");
    const words = base.replace(/([a-z])([A-Z])/g, (_, before: string, after: string) => before + " " + after.toLowerCase()).replace(/[-_]+/g, " ").replace(/\s+/g, " ").trim();
    return words ? words[0].toUpperCase() + words.slice(1) : base;
}

/** Changed within the last week: the dot beside a note. */
export function changedLately(iso: string, now: number = Date.now()): boolean {
    const at = new Date(iso).getTime();
    return !Number.isNaN(at) && now - at < 7 * DAY && now >= at;
}

/** One list from the three folders: files only, indexes left out, the most
 *  recently changed first, and a note never listed twice. */
export function notesFrom(folders: readonly { private: boolean; items: readonly LibraryItem[] }[]): MemoryNote[] {
    const seen = new Set<string>();
    const notes: MemoryNote[] = [];
    for (const folder of folders) {
        for (const item of folder.items) {
            if (item.kind !== "file" || isIndex(item.path) || seen.has(item.path)) continue;
            seen.add(item.path);
            notes.push({
                path: item.path,
                topic: topicOf(item.name || item.path),
                gloss: (item.description ?? "").trim(),
                updated: item.updated,
                private: folder.private,
            });
        }
    }
    return notes.sort((a, b) => (new Date(b.updated).getTime() || 0) - (new Date(a.updated).getTime() || 0));
}

export interface Noted {
    path: string;
    topic: string;
    private: boolean;
}

const WRITES = new Set(["pod_write_file", "pod_edit_file"]);

/** A tool call that wrote a memory note and succeeded, as the line under a
 *  reply reads it. Null for anything else, including a write that failed or
 *  has not come back yet, and an update to an index alone. */
export function notedBy(toolName: unknown, args: unknown, result: unknown, metadata?: Record<string, unknown> | null): Noted | null {
    if (!WRITES.has(toolKey(toolName, metadata))) return null;
    if (!result || typeof result !== "object") return null;
    const returned = result as { success?: unknown; path?: unknown; error?: unknown };
    if (returned.success === false || returned.error) return null;
    const asked = args && typeof args === "object" ? (args as { path?: unknown }).path : undefined;
    const path = typeof returned.path === "string" ? returned.path : typeof asked === "string" ? asked : "";
    if (!path || !isMemoryPath(path) || isIndex(path)) return null;
    return { path, topic: topicOf(path), private: path.startsWith("/me/") };
}
