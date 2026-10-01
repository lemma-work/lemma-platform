"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { EditorContent, useEditor, type Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Placeholder from "@tiptap/extension-placeholder";
import { Table, TableCell, TableHeader, TableRow } from "@tiptap/extension-table";
import { Markdown } from "tiptap-markdown";
import TaskList from "@tiptap/extension-task-list";
import TaskItem from "@tiptap/extension-task-item";
import type { Range } from "@tiptap/core";
import { source, type FileContent } from "@/data";
import { lemma } from "@/session/client";
import { usePageTools, pageDirs, safeName } from "@/docpages/page-context";
import { SlashBridge, SlashCommand, type SlashId } from "@/docpages/editor/slash";
import { wantsRename } from "@/docpages/rename";
import { FileBlock } from "@/docpages/editor/file-block";
import { ViewBlock, WidgetBlock } from "@/docpages/editor/embeds";
import { createPortal } from "react-dom";
import { PodImage } from "@/docpages/editor/pod-image";
import { CommentHighlight, commentHighlightKey } from "@/docpages/editor/comment-highlight";
import { WritingMarker, writingMarker } from "@/docpages/editor/writing-marker";
import { BlockHandle, CaretPrompt, SelectionToolbar, SlashMenu } from "@/docpages/editor/page-chrome";
import { flatText, indexAt } from "@/docpages/editor/flat-text";
import { anchorOf } from "@/docpages/comments/model";
import { quoted } from "@/docs/doc-ask";
import { isForbidden } from "@/session/auth-state";
import { joinFrontmatter, splitFrontmatter } from "@/skills/skill-frontmatter";
import { SAVE_AFTER_MS, describeSave, sayLocked, type SaveState } from "./document-save";
import { useDocAsk } from "@/docs/doc-ask";
import { ChatIcon } from "@/ui/icons";

type Pick = { top: number; left: number; text: string };

/** Where the selection toolbar goes: centred over the selection, just above
 *  its first line, in the host's own coordinates. */
function placePick(editor: Editor, host: HTMLElement | null): Pick | null {
    const { from, to, empty } = editor.state.selection;
    if (empty || !host) return null;
    const text = editor.state.doc.textBetween(from, to, "\n");
    if (!text.trim()) return null;
    const start = editor.view.coordsAtPos(from);
    const end = editor.view.coordsAtPos(to);
    const box = host.getBoundingClientRect();
    return {
        top: Math.min(start.top, end.top) - box.top,
        left: (start.left + end.right) / 2 - box.left,
        text,
    };
}

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

/** The page's title: its opening heading, when it opens with one. */
function titleOf(editor: Editor): string | null {
    const first = editor.state.doc.firstChild;
    if (!first || first.type.name !== "heading" || first.attrs.level !== 1) return null;
    return first.textContent;
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

    const asker = useDocAsk();
    const host = useRef<HTMLDivElement>(null);
    const [pick, setPick] = useState<Pick | null>(null);

    /* A page — as opposed to a markdown file shown somewhere — gets the
       block tools: `/`, the selection toolbar, the handle, comments. Decided
       once, because an editor's extensions are fixed when it is built. */
    const tools = usePageTools();
    const toolsRef = useRef(tools);
    toolsRef.current = tools;
    const [asPage] = useState(() => Boolean(tools));
    const bridge = useMemo(() => new SlashBridge(), []);
    const [prompt, setPrompt] = useState<{ top: number; left: number; pos: number; kind: "write" | "visualize" } | null>(null);
    const picker = useRef<HTMLInputElement>(null);
    const picking = useRef<{ kind: "image" | "file"; at: number } | null>(null);

    /* The title is committed when the caret leaves it, or the page loses
       focus — then the file is renamed to follow it (see `commitTitle`). */
    const inTitle = useRef(false);
    const titleCommit = useRef<(() => void) | null>(null);
    /* The title as it arrived — on opening, and each time the file is read
       in from elsewhere. Only a title somebody typed here moves the file.
       Renaming on every blur moved a doc the moment anybody clicked into it
       and out again whenever its name and heading disagreed (an agent's
       `q3-report.md` titled "Q3 Report"), and moved it under the doc's own
       chat when the agent changed the heading — and the chat, its cards and
       the agent's next edit all still held the old path. */
    const titleAsFound = useRef<string | null>(null);

    const editor = useEditor({
        onCreate: ({ editor }) => {
            titleAsFound.current = titleOf(editor);
        },
        onSelectionUpdate: ({ editor }) => {
            setPick(asPage ? null : placePick(editor, host.current));
            if (!asPage) return;
            const now = editor.state.selection.$from.index(0) === 0;
            if (inTitle.current && !now) titleCommit.current?.();
            inTitle.current = now;
        },
        onBlur: () => {
            setPick(null);
            if (asPage) titleCommit.current?.();
        },
        extensions: [
            /* No underline: markdown has none, and a mark that vanishes on
               save is worse than a button that is not there. */
            StarterKit.configure({ underline: false, link: { openOnClick: false, autolink: true } }),
            TaskList,
            TaskItem.configure({ nested: true }),
            ...(asPage ? [
                PodImage,
                FileBlock,
                WidgetBlock,
                ViewBlock,
                SlashCommand.configure({ bridge }),
                CommentHighlight.configure({ onClick: (id: string) => toolsRef.current?.comments?.focus(id) }),
                WritingMarker,
            ] : []),
            /* Resizing writes column widths that markdown cannot carry, so the
               handle would be a control with no effect on the file. */
            Table.configure({ resizable: false, HTMLAttributes: { class: "md__table" } }),
            TableRow,
            TableHeader,
            TableCell,
            /* On a page the hint follows the caret, so an empty line always
               says what it can become — and an empty title says what it is.
               The first line under the title of a page with nothing else in
               it names the blocks a doc anywhere else does not have: that is
               where somebody decides what this page is for. */
            Placeholder.configure({
                placeholder: ({ editor, node, pos }) => {
                    if (!asPage) return "Write here…";
                    if (node.type.name === "heading") return node.attrs.level === 1 ? "Untitled" : "Heading";
                    const doc = editor.state.doc;
                    const firstLine = doc.childCount <= 2 && doc.firstChild?.type.name === "heading" && pos === doc.firstChild.nodeSize;
                    return firstLine ? "Write, or press / for a live table, a chart, a sub-page…" : "Write, or press / for blocks…";
                },
            }),
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

    /* A page just made by New page is a title and nothing else. It opens with
       that title selected, so the first thing typed names it — rather than a
       blank sheet with no caret, which reads as a button that did nothing. */
    useEffect(() => {
        if (!editor || !asPage) return;
        const doc = editor.state.doc;
        const first = doc.firstChild;
        if (!first || first.type.name !== "heading" || doc.textContent !== first.textContent || first.textContent !== "Untitled") return;
        editor.chain().focus().setTextSelection({ from: 1, to: 1 + first.content.size }).run();
    }, [editor, asPage]);

    /** One write, however it was asked for.
     *
     *  The counter is what stops a slow save from undoing a fast one: keep
     *  typing through a save and two are in flight, and only the last of them
     *  may say what is now on disk. */
    const attempt = useRef(0);
    /* Where saves go. A ref, not the prop: a rename moves the file before
       the new path arrives as a prop, and a save aimed at the old path would
       find it gone and upload a second copy under the old name. */
    const target = useRef(path);
    useEffect(() => { target.current = path; }, [path]);
    const renaming = useRef(false);
    const persist = useCallback(async (next: string) => {
        /* Held while the file moves; the autosave comes back round once the
           new path is in. */
        if (renaming.current) return;
        const mine = ++attempt.current;
        const at = target.current;
        setState("saving");
        try {
            await source.writeFile(podId, at, next);
            if (attempt.current !== mine) return;
            setSaved(next);
            setState("saved");
            /* The same key `FileView` and `ViewActions` read. Without this,
               Download hands over the version this tab opened with, and the
               skills deck goes on describing a SKILL.md that has changed. */
            cache.setQueryData(["file", podId, at], (was?: FileContent) =>
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
    /* `path` stays a dependency on purpose: a new one re-arms the autosave
       that was held while the file moved. */
    }, [cache, path, podId]);

    /** Write what the editor holds now, without waiting for the pause — for
     *  the moments the file has to be current before someone else reads it. */
    const savedNow = useRef(saved);
    savedNow.current = saved;
    const flush = useCallback(async () => {
        if (!editor) return;
        const next = joinFrontmatter(frontNow.current, markdownOf(editor));
        /* Nothing typed, nothing written: the same rule as `onUpdate`, which
           keeps a read from re-emitting the file in this editor's dialect. */
        if (next === savedNow.current) return;
        setDraft(next);
        await persist(next);
    }, [editor, persist]);

    /** Rename the file to follow the page's title, once the title is done
     *  being typed. Saved first, so the move carries every keystroke. */
    const commitTitle = useCallback(async () => {
        const page = toolsRef.current;
        if (!editor || !page?.renamePage || renaming.current || forbidden) return;
        const title = titleOf(editor);
        if (title === null || title === titleAsFound.current) return;
        if (!wantsRename(target.current, title)) return;
        await flush();
        renaming.current = true;
        try {
            target.current = await page.renamePage(target.current, title);
            titleAsFound.current = title;
        } catch {
            /* The name stays; the page is still saved where it was. */
        } finally {
            renaming.current = false;
        }
    }, [editor, flush, forbidden]);
    titleCommit.current = () => void commitTitle();

    /* What each `/` item does. Set on every render so it sees the current
       page tools; the plugin only ever calls through the bridge. */
    bridge.run = (id: SlashId, ed, range: Range) => {
        const page = toolsRef.current;
        const chain = ed.chain().focus().deleteRange(range);
        switch (id) {
            case "text": chain.setParagraph().run(); return;
            case "h1": chain.setHeading({ level: 1 }).run(); return;
            case "h2": chain.setHeading({ level: 2 }).run(); return;
            case "h3": chain.setHeading({ level: 3 }).run(); return;
            case "bullet": chain.toggleBulletList().run(); return;
            case "number": chain.toggleOrderedList().run(); return;
            case "todo": chain.toggleTaskList().run(); return;
            case "quote": chain.toggleBlockquote().run(); return;
            case "code": chain.toggleCodeBlock().run(); return;
            case "divider": chain.setHorizontalRule().run(); return;
            case "table": chain.insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run(); return;
            case "image":
            case "file":
                chain.run();
                picking.current = { kind: id, at: ed.state.selection.from };
                if (picker.current) {
                    picker.current.accept = id === "image" ? "image/*" : "";
                    picker.current.click();
                }
                return;
            case "page":
                chain.run();
                if (page) void newSubPage(ed.state.selection.from);
                return;
            case "generate":
            case "visualize": {
                chain.run();
                const box = host.current?.getBoundingClientRect();
                const caret = ed.view.coordsAtPos(ed.state.selection.from);
                if (box) setPrompt({ top: caret.bottom - box.top + 6, left: Math.max(0, caret.left - box.left), pos: ed.state.selection.from, kind: id === "visualize" ? "visualize" : "write" });
                return;
            }
            case "view":
                chain.insertContent({ type: "viewBlock", attrs: { code: "", fresh: true } }).run();
                return;
            case "html":
                chain.insertContent({ type: "widgetBlock", attrs: { code: "", fresh: true } }).run();
                return;
        }
    };

    /** A page inside this one: a new file in the page's own folder, a block
     *  pointing at it here, and the new page opened. */
    const newSubPage = async (at: number) => {
        const page = toolsRef.current;
        if (!editor || !page) return;
        const dir = pageDirs(page.path).children;
        const name = "Untitled-" + Date.now().toString(36) + ".md";
        const path = dir + "/" + name;
        try {
            if (source.label === "live") {
                await lemma(page.podId).files.upload(new Blob(["# Untitled\n\n"], { type: "text/markdown" }), { name, directoryPath: dir, searchEnabled: true });
            } else {
                await source.writeFile(page.podId, path, "# Untitled\n\n");
            }
        } catch {
            return;
        }
        editor.chain().focus().insertContentAt(at, { type: "fileBlock", attrs: { href: path, label: "Untitled" } }).run();
        await flush();
        void cache.invalidateQueries({ queryKey: ["library", page.podId] });
        page.openFile(path);
    };

    /** An image or a file put next to the page and placed where `/` was. */
    const placeUpload = async (file: File) => {
        const page = toolsRef.current;
        const target = picking.current;
        picking.current = null;
        if (!editor || !page || !target) return;
        let path: string;
        try {
            if (source.label === "live") {
                const made = await lemma(page.podId).files.upload(file, { name: safeName(file.name), directoryPath: pageDirs(page.path).assets, searchEnabled: target.kind === "file" });
                path = String((made as { path?: string }).path ?? "");
            } else {
                /* A data URL, not an object URL: the page is saved, and an
                   object URL is dead after a reload. */
                path = await new Promise<string>((resolve, reject) => {
                    const reader = new FileReader();
                    reader.onload = () => resolve(String(reader.result));
                    reader.onerror = () => reject(reader.error);
                    reader.readAsDataURL(file);
                });
            }
        } catch {
            return;
        }
        if (!path) return;
        const node = target.kind === "image"
            ? { type: "image", attrs: { src: path, alt: file.name } }
            : { type: "fileBlock", attrs: { href: path, label: file.name } };
        editor.chain().focus().insertContentAt(target.at, node).run();
    };

    /** The bot writes into the page: a marker where it should go, saved so the
     *  bot reads it, then asked in the page's conversation to replace it. */
    const writeHere = async (pos: number, ask: string, kind: "write" | "visualize") => {
        const page = toolsRef.current;
        setPrompt(null);
        if (!editor || !page?.sendToBot) return;
        const marker = writingMarker(page.botName, ask);
        editor.chain().focus().insertContentAt(pos, { type: "paragraph", content: [{ type: "text", text: marker }] }).run();
        await flush();
        /* Short on purpose. How a widget block works, how to edit the doc and
           how to read a terse ask all live in the conversation's instructions;
           repeating them here only gave the agent two versions to reconcile. */
        page.sendToBot(kind === "visualize"
            ? "Draw this as a ```lemma-widget block in " + page.path + ", replacing the line `" + marker + "`: " + ask
            : "Write this into " + page.path + ", replacing the line `" + marker + "`, as markdown that fits the page around it: " + ask,
        );
    };

    const commentOnSelection = () => {
        const page = toolsRef.current;
        if (!editor || !page?.comments) return;
        const { from, to } = editor.state.selection;
        const flat = flatText(editor.state.doc);
        const anchor = anchorOf(flat.text, indexAt(flat, from), indexAt(flat, to));
        page.comments.start(anchor.quote.trim() ? anchor : null);
        editor.commands.setTextSelection(to);
    };

    /* The open threads, drawn over their words. */
    const anchors = tools?.comments?.anchors;
    const activeComment = tools?.comments?.active ?? null;
    useEffect(() => {
        if (!editor || !asPage || !anchors) return;
        editor.view.dispatch(editor.state.tr.setMeta(commentHighlightKey, { anchors, active: activeComment }));
    }, [editor, asPage, anchors, activeComment]);

    /* An agent writing into this page is seen while it writes: the file's
       fingerprint is checked every few seconds while the page is on screen,
       and a change is read in — unless you have unsaved words of your own,
       which the adopt-effect below already refuses to overwrite. */
    useEffect(() => {
        if (!asPage || source.label !== "live") return;
        let last: string | null = null;
        let stopped = false;
        const tick = async () => {
            if (document.hidden) return;
            try {
                const meta = (await lemma(podId).files.get(path)) as { content_sha256?: string | null; updated_at?: string | null };
                if (stopped) return;
                const stamp = meta.content_sha256 ?? meta.updated_at ?? null;
                if (last !== null && stamp !== last) void cache.invalidateQueries({ queryKey: ["file", podId, path] });
                last = stamp;
            } catch {
                /* The next tick asks again. */
            }
        };
        void tick();
        const timer = setInterval(() => void tick(), 4000);
        return () => { stopped = true; clearInterval(timer); };
    }, [asPage, cache, path, podId]);

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
        /* A heading the agent wrote is not one typed here (see `titleAsFound`). */
        titleAsFound.current = titleOf(editor);
    }, [dirty, editor, saved, text]);

    useEffect(() => {
        editor?.setEditable(!forbidden);
    }, [editor, forbidden]);

    const status = describeSave({ state, dirty });
    const statusView = (
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
    );

    return (
        <div className={"doc-host" + (asPage ? " doc-host--page" : "")} ref={host}>
            {asPage && editor && (
                <>
                    <SlashMenu bridge={bridge} host={host.current} botName={tools?.botName ?? "the bot"} />
                    {!forbidden && <BlockHandle editor={editor} />}
                    <SelectionToolbar
                        editor={editor}
                        botName={tools?.botName ?? "the bot"}
                        onAsk={tools?.sendToBot ? (request, quote) => tools.sendToBot?.(quoted(quote) + request) : null}
                        onComment={tools?.comments ? commentOnSelection : null}
                    />
                    {prompt && (
                        <CaretPrompt
                            at={{ top: prompt.top, left: prompt.left }}
                            placeholder={!tools?.sendToBot ? "Open the page's chat to ask for writing"
                                : prompt.kind === "visualize" ? "What should " + tools.botName + " draw? e.g. deals by stage as a bar chart"
                                : "What should " + tools.botName + " write here?"}
                            onSubmit={(ask) => void writeHere(prompt.pos, ask, prompt.kind)}
                            onCancel={() => { setPrompt(null); editor.commands.focus(); }}
                        />
                    )}
                    <input
                        ref={picker}
                        type="file"
                        hidden
                        onChange={(event) => {
                            const file = event.target.files?.[0];
                            event.target.value = "";
                            if (file) void placeUpload(file);
                            else picking.current = null;
                        }}
                    />
                </>
            )}
            {!asPage && asker && pick && (
                /* Mouse-down is swallowed so the click does not blur the
                   editor, which would drop the selection it is about. */
                <div className="doc-pick" style={{ top: pick.top, left: pick.left }} onMouseDown={event => event.preventDefault()}>
                    <button onClick={() => { asker.ask(pick.text); setPick(null); }}>
                        <ChatIcon size={15} /> Ask {asker.label}
                    </button>
                </div>
            )}
            {/* Out of the document's flow entirely — see `document.css` for
                where it ends up and why. Announced politely rather than
                assertively: it is a report on housekeeping, and it must not
                interrupt a screen reader mid-sentence while somebody types. */}
            {asPage && tools?.statusSlot ? createPortal(statusView, tools.statusSlot) : statusView}
            <EditorContent editor={editor} />
        </div>
    );
}
