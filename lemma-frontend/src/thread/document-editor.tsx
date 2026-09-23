"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { EditorContent, useEditor, type Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Placeholder from "@tiptap/extension-placeholder";
import { Table, TableCell, TableHeader, TableRow } from "@tiptap/extension-table";
import { Markdown } from "tiptap-markdown";
import { source, type FileContent } from "@/data";
import { isForbidden } from "@/session/auth-state";
import { joinFrontmatter, splitFrontmatter } from "@/skills/skill-frontmatter";
import { SAVE_AFTER_MS, describeSave, sayLocked, type SaveState } from "./document-save";

/** A markdown file on the stage, which is to say a document you can write in.
 *
 *  There is no Edit button, because there is no mode: the document you were
 *  reading a second ago is the one the caret is in now, at the same size, in
 *  the same serif, on the same measure. That is the whole idea — the editor
 *  wears the reader's stylesheet rather than a stylesheet of its own, so the
 *  `md` class below is doing real work and is not decoration.
 *
 *  What it is not is a markdown *source* editor. `##` becomes a heading as you
 *  type it and stays a heading; the file on disk still has the `##` in it.
 *
 *  The round trip is the cost. The document is held as a tree and written back
 *  out of it, so every save re-emits the whole file in this editor's dialect —
 *  `*` bullets land as `-`, a setext heading lands as `##`. Harmless for prose
 *  and invisible to anyone reading it, and the reason two things are refused:
 *  formats where the bytes are the point (`editableKind`) and documents with
 *  HTML in them (`holdsMarkup`), both in `document-save.ts`.
 */

/** `tiptap-markdown` adds its storage at runtime and the editor's type does not
 *  know about it. */
type WithMarkdown = Editor & { storage: { markdown: { getMarkdown: () => string } } };

function markdownOf(editor: Editor): string {
    return (editor as WithMarkdown).storage.markdown.getMarkdown();
}

export function DocumentEditor({ podId, path, text }: { podId: string; path: string; text: string }) {
    const cache = useQueryClient();

    /** What this app believes is on disk. Everything else is measured from it. */
    const [saved, setSaved] = useState(text);
    /** What the editor holds, as the whole file rather than as the part it can
     *  see — the frontmatter has to be in the comparison or a document with a
     *  `---` block would read as changed the moment it opened. */
    const [draft, setDraft] = useState(text);
    const [state, setState] = useState<SaveState>("idle");
    const [forbidden, setForbidden] = useState(false);

    /* The frontmatter never reaches the editor: a `---` fence renders as a rule
       followed by a setext heading, and the round trip would write *that* back
       — quietly costing a SKILL.md the contract the loader reads it by. It is
       held aside here and re-attached to whatever comes out. */
    const front = useMemo(() => splitFrontmatter(saved).front, [saved]);
    const frontNow = useRef(front);
    frontNow.current = front;

    /* Read once. `useEditor` only takes `content` when it builds the editor, and
       every sync after this one goes through the effect below. */
    const opening = useRef(splitFrontmatter(text).body);

    const editor = useEditor({
        extensions: [
            StarterKit,
            /* Resizing writes column widths that markdown cannot carry, so the
               handle would be a control with no effect on the file. */
            Table.configure({ resizable: false, HTMLAttributes: { class: "md__table" } }),
            TableRow,
            TableHeader,
            TableCell,
            Placeholder.configure({ placeholder: "Write here…" }),
            Markdown.configure({ html: false, transformPastedText: true, transformCopiedText: true }),
        ],
        content: opening.current,
        editorProps: {
            /* The reader's class, deliberately. `.filecard--full .md` is what
               makes a document on the stage a document — Newsreader, 17.5px, a
               68ch measure — and the editor inherits all of it by being the
               same thing. */
            attributes: { class: "md doc" },
        },
        onUpdate: ({ editor, transaction }) => {
            /* Focus, not just a changed document. Opening a file runs it
               through the parser and back, and the markdown that comes out is
               normalised — so without this, reading a document with `*` bullets
               in it would rewrite them on disk without anybody touching a key. */
            if (!transaction.docChanged || !editor.isFocused) return;
            setDraft(joinFrontmatter(frontNow.current, markdownOf(editor)));
        },
        immediatelyRender: false,
    });

    const dirty = draft !== saved;

    /** One write, however it was asked for.
     *
     *  The counter is what stops a slow save from undoing a fast one: keep
     *  typing through a save and two are in flight, and only the last of them
     *  may say what is now on disk. */
    const attempt = useRef(0);
    const persist = useCallback(async (next: string) => {
        const mine = ++attempt.current;
        setState("saving");
        try {
            await source.writeFile(podId, path, next);
            if (attempt.current !== mine) return;
            setSaved(next);
            setState("saved");
            /* The same key `FileView` and `ViewActions` read. Without this,
               Download hands over the version this tab opened with, and the
               skills deck goes on describing a SKILL.md that has changed. */
            cache.setQueryData(["file", podId, path], (was?: FileContent) =>
                was ? { ...was, text: next, size: next.length } : was);
        } catch (error) {
            if (attempt.current !== mine) return;
            /* A 403 is not a failure to retry. The file is somebody else's to
               change, so the editor stops being one and says so — "Couldn’t
               save" beside a caret that still blinks invites somebody to keep
               typing into a document that will never be written. */
            if (isForbidden(error)) { setForbidden(true); setState("idle"); return; }
            setState("failed");
        }
    }, [cache, path, podId]);

    /* Typing stops, the file is written. No button, because a button on a
       paragraph is a chore and losing the paragraph to a closed tab is worse. */
    useEffect(() => {
        if (!dirty || forbidden) return;
        const timer = setTimeout(() => void persist(draft), SAVE_AFTER_MS);
        return () => clearTimeout(timer);
    }, [dirty, draft, forbidden, persist]);

    /* Closing the tab inside that pause must not cost the last sentence. Held
       in a ref so the unmount effect below never re-runs on a keystroke. */
    const unwritten = useRef<(() => void) | null>(null);
    unwritten.current = dirty && !forbidden ? () => void persist(draft) : null;
    useEffect(() => () => unwritten.current?.(), []);

    /* The file changed somewhere else — the agent rewrote it, or another tab
       did. Taken only when there is nothing of yours to lose; a refetch landing
       on a half-typed paragraph would be the app deleting your work on its own
       initiative. */
    useEffect(() => {
        if (!editor || text === saved || dirty) return;
        setSaved(text);
        setDraft(text);
        editor.commands.setContent(splitFrontmatter(text).body);
    }, [dirty, editor, saved, text]);

    useEffect(() => {
        editor?.setEditable(!forbidden);
    }, [editor, forbidden]);

    const status = describeSave({ state, dirty });

    return (
        <div className="doc-host">
            {/* Out of the document's flow entirely — see `document.css` for
                where it ends up and why. Announced politely rather than
                assertively: it is a report on housekeeping, and it must not
                interrupt a screen reader mid-sentence while somebody types. */}
            <div className="doc-host__status" aria-live="polite">
                {forbidden ? (
                    <span className="doc-status doc-status--bad">{sayLocked("forbidden")}</span>
                ) : status ? (
                    <span className={"doc-status" + (status.tone === "bad" ? " doc-status--bad" : "")}>
                        {status.label}
                        {status.tone === "bad" && (
                            <button className="doc-status__again" onClick={() => void persist(draft)}>
                                Try again
                            </button>
                        )}
                    </span>
                ) : null}
            </div>
            <EditorContent editor={editor} />
        </div>
    );
}
