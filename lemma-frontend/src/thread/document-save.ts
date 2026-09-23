/** When a document on the stage is written back, and what the reader is told.
 *
 *  A document here saves itself. There is no Edit button and no Save button:
 *  you click into the prose and type, and a second later it is on disk. The
 *  alternative — a mode you enter and leave — asks somebody to declare an
 *  intention they have already demonstrated by typing, and it puts a modal
 *  state on a surface whose whole point is that it is just the document.
 *
 *  That is a promise about *markdown only*, and the reason is in
 *  `editableKind` below.
 *
 *  Kept apart from the editor because these are decisions rather than
 *  rendering, and because the test runner strips types from `.ts` and cannot
 *  load a `.tsx` at all — anything living beside JSX in this app is untestable.
 */

import type { FileContent } from "@/data";

/** How long typing has to stop before the document is written. */
export const SAVE_AFTER_MS = 700;

export type SaveState = "idle" | "saving" | "saved" | "failed";

/** Which files this app will let you type into.
 *
 *  Markdown, and nothing else. The editor holds a document as a tree and
 *  writes markdown back out of it, so every save rewrites the whole file in
 *  the editor's own dialect — a `*` bullet becomes `-`, a setext heading
 *  becomes `##`, trailing whitespace goes. For prose that is harmless and
 *  nobody notices. For a file where the exact bytes are the point — a JSON
 *  config, a CSV, a log — it is damage, and it would arrive silently on a
 *  700ms timer. So those stay a `<pre>` you can read and download.
 */
export function editableKind(kind: FileContent["kind"]): boolean {
    return kind === "markdown";
}

export interface SaveStatus {
    label: string;
    /** A failure is the only state worth colouring. The rest is a whisper —
     *  it is telling you about housekeeping you asked for by typing. */
    tone: "quiet" | "bad";
}

/** The one line a document shows about itself, or nothing at all.
 *
 *  Nothing is the normal case. A document you opened and read has nothing to
 *  report, and printing "Saved" over it would claim you changed something.
 *  Unsaved work says so rather than staying quiet, so a stalled network is
 *  visible while you can still do something about it — copy the paragraph out
 *  — rather than after the tab is closed.
 */
export function describeSave({ state, dirty }: { state: SaveState; dirty: boolean }): SaveStatus | null {
    if (state === "failed") return { label: "Couldn’t save", tone: "bad" };
    if (state === "saving") return { label: "Saving…", tone: "quiet" };
    if (dirty) return { label: "Unsaved changes", tone: "quiet" };
    if (state === "saved") return { label: "Saved", tone: "quiet" };
    return null;
}

/** Does this document carry markup the editor would not hand back?
 *
 *  The editor parses markdown into a tree of the nodes it knows about and
 *  writes that tree back out. Raw HTML is not one of those nodes, so a
 *  `<details>` block or a styled `<div>` in a file goes in and does not come
 *  out — and the file the reader is looking at is exactly the kind an agent
 *  writes that way, because this app renders agent HTML on purpose (see
 *  `markdown.tsx`).
 *
 *  Losing it would be silent, permanent, and triggered by a single keystroke
 *  landing on a 700ms timer. So a document with markup in it is read-only
 *  here, and says so. That is a smaller thing to explain than a report whose
 *  status strips vanished while somebody fixed a typo.
 *
 *  Code is stripped before the check. A document *about* HTML — a fenced
 *  example, an inline `<div>` — is prose, and refusing to let anybody edit it
 *  would be the check misreading its own subject.
 */
export function holdsMarkup(text: string): boolean {
    const prose = text
        .replace(/^ {0,3}(```|~~~)[\s\S]*?^ {0,3}\1[^\n]*$/gm, "")
        .replace(/`[^`\n]*`/g, "");
    /* `<https://example.com>` and `<sam@example.com>` are autolinks, which are
       markdown rather than markup and survive the round trip. */
    return /<\/?[a-zA-Z][a-zA-Z0-9-]*(\s[^<>]*)?\/?>/.test(prose.replace(/<[a-z]+:[^>\s]*>|<[^>\s@]+@[^>\s]+>/gi, ""));
}

/** Why a markdown document is being shown rather than offered to write in.
 *
 *  Two reasons, and they are found at different moments: markup is visible in
 *  the bytes before anybody touches anything, and a refusal only arrives from
 *  the server after a save has been tried. So the first decides which view to
 *  draw at all, and the second turns a live editor back into a page. */
export type Locked = "markup" | "forbidden";

export function lockedBecause(text: string): Locked | null {
    return holdsMarkup(text) ? "markup" : null;
}

/** The one sentence a locked document carries. It sits under something
 *  somebody came here to read, so it stays one line and says what would
 *  happen rather than naming a rule. */
export function sayLocked(locked: Locked): string {
    return locked === "forbidden"
        ? "This file is read-only because you don’t have permission to edit it."
        : "This file is read-only because the editor cannot preserve its HTML.";
}
