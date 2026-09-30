"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useEditorState, type Editor } from "@tiptap/react";
import { BubbleMenu } from "@tiptap/react/menus";
import DragHandle from "@tiptap/extension-drag-handle-react";
import type { Node as PMNode } from "@tiptap/pm/model";
import type { SlashBridge, SlashId, SlashItem } from "./slash";
import {
    AttachIcon, UsageIcon, ChatIcon, ChevronDownIcon, CodeIcon, FileIcon, ImageIcon, LinkIcon, PlusIcon, SparkleIcon, TableIcon, TextIcon,
} from "@/ui/icons";

/* ── the slash menu ────────────────────────────────────────────────── */

function SlashGlyph({ id }: { id: SlashId }) {
    switch (id) {
        case "generate": return <SparkleIcon size={16} />;
        case "visualize": return <UsageIcon size={16} />;
        case "view": return <TableIcon size={16} />;
        case "html": return <CodeIcon size={16} />;
        case "page": return <FileIcon size={16} />;
        case "image": return <ImageIcon size={16} />;
        case "file": return <AttachIcon size={16} />;
        case "code": return <CodeIcon size={16} />;
        case "table": return <TableIcon size={16} />;
        case "h1": return <b className="slash__h">H1</b>;
        case "h2": return <b className="slash__h">H2</b>;
        case "h3": return <b className="slash__h">H3</b>;
        case "bullet": return <span className="slash__h">•</span>;
        case "number": return <span className="slash__h">1.</span>;
        case "todo": return <span className="slash__h">☐</span>;
        case "quote": return <span className="slash__h">”</span>;
        case "divider": return <span className="slash__h">—</span>;
        default: return <TextIcon size={16} />;
    }
}

export function SlashMenu({ bridge, host, botName }: { bridge: SlashBridge; host: HTMLElement | null; botName: string }) {
    const state = useSyncExternalStore(bridge.subscribe, bridge.get, bridge.get);
    const list = useRef<HTMLDivElement>(null);
    useEffect(() => {
        list.current?.querySelector("[data-active]")?.scrollIntoView({ block: "nearest" });
    }, [state.index]);
    if (!state.open || !state.rect || !host) return null;
    const box = host.getBoundingClientRect();
    const below = state.rect.bottom + 320 < window.innerHeight;
    const style = {
        left: Math.max(0, state.rect.left - box.left),
        top: below ? state.rect.bottom - box.top + 6 : undefined,
        bottom: below ? undefined : box.bottom - state.rect.top + 6,
    };
    let group = "";
    return (
        <div className="slash" style={style} ref={list} onMouseDown={(event) => event.preventDefault()} role="listbox" aria-label="Insert a block">
            {state.items.map((item: SlashItem, at) => {
                const heading = item.group !== group ? item.group : null;
                group = item.group;
                return (
                    <div key={item.id}>
                        {heading && <p className="slash__group">{heading === "Ask" ? "Ask " + botName : heading}</p>}
                        <button
                            className="slash__item"
                            role="option"
                            aria-selected={at === state.index}
                            data-active={at === state.index || undefined}
                            onMouseEnter={() => bridge.hover(at)}
                            onClick={() => bridge.pick(item)}
                        >
                            <span className="slash__glyph"><SlashGlyph id={item.id} /></span>
                            <span className="slash__title">{item.id === "generate" ? "Ask " + botName + " to write" : item.title}</span>
                            {item.hint && <span className="slash__hint">{item.hint}</span>}
                            {item.shortcut && <kbd>{item.shortcut}</kbd>}
                        </button>
                    </div>
                );
            })}
        </div>
    );
}

/* ── a prompt at the caret ─────────────────────────────────────────── */

export function CaretPrompt({ at, placeholder, onSubmit, onCancel }: {
    at: { top: number; left: number };
    placeholder: string;
    onSubmit: (text: string) => void;
    onCancel: () => void;
}) {
    const [text, setText] = useState("");
    return (
        <form className="caret-prompt" style={at} onSubmit={(event) => { event.preventDefault(); if (text.trim()) onSubmit(text.trim()); }}>
            <SparkleIcon size={16} />
            <input
                autoFocus
                value={text}
                placeholder={placeholder}
                onChange={(event) => setText(event.target.value)}
                onKeyDown={(event) => { if (event.key === "Escape") onCancel(); }}
                onBlur={() => { if (!text.trim()) onCancel(); }}
            />
            <button type="submit" disabled={!text.trim()}>Write</button>
        </form>
    );
}

/* ── the selection toolbar ─────────────────────────────────────────── */

type TurnInto = { id: string; label: string; run: (editor: Editor) => void; active: (editor: Editor) => boolean };
const TURN_INTO: TurnInto[] = [
    { id: "text", label: "Text", run: (e) => e.chain().focus().setParagraph().run(), active: (e) => e.isActive("paragraph") && !e.isActive("taskItem") && !e.isActive("listItem") },
    { id: "h1", label: "Heading 1", run: (e) => e.chain().focus().setHeading({ level: 1 }).run(), active: (e) => e.isActive("heading", { level: 1 }) },
    { id: "h2", label: "Heading 2", run: (e) => e.chain().focus().setHeading({ level: 2 }).run(), active: (e) => e.isActive("heading", { level: 2 }) },
    { id: "h3", label: "Heading 3", run: (e) => e.chain().focus().setHeading({ level: 3 }).run(), active: (e) => e.isActive("heading", { level: 3 }) },
    { id: "bullet", label: "Bulleted list", run: (e) => e.chain().focus().toggleBulletList().run(), active: (e) => e.isActive("bulletList") },
    { id: "number", label: "Numbered list", run: (e) => e.chain().focus().toggleOrderedList().run(), active: (e) => e.isActive("orderedList") },
    { id: "todo", label: "To-do list", run: (e) => e.chain().focus().toggleTaskList().run(), active: (e) => e.isActive("taskList") },
    { id: "quote", label: "Quote", run: (e) => e.chain().focus().toggleBlockquote().run(), active: (e) => e.isActive("blockquote") },
    { id: "code", label: "Code", run: (e) => e.chain().focus().toggleCodeBlock().run(), active: (e) => e.isActive("codeBlock") },
];

export function SelectionToolbar({ editor, botName, onAsk, onComment }: {
    editor: Editor;
    botName: string;
    /** Ask the bot to change the selected words; null without a conversation. */
    onAsk: ((request: string, quote: string) => void) | null;
    /** Start a comment thread on the selection; null without comments. */
    onComment: (() => void) | null;
}) {
    const [mode, setMode] = useState<"tools" | "ask" | "link" | "turn">("tools");
    const [text, setText] = useState("");
    const reset = () => { setMode("tools"); setText(""); };
    const selected = () => {
        const { from, to } = editor.state.selection;
        return editor.state.doc.textBetween(from, to, "\n");
    };
    /* Read on every transaction, so the pressed marks and the block type
       follow the selection rather than the moment the toolbar mounted. */
    const marks = useEditorState({
        editor,
        selector: ({ editor: e }) => ({
            bold: e.isActive("bold"),
            italic: e.isActive("italic"),
            strike: e.isActive("strike"),
            code: e.isActive("code"),
            link: e.isActive("link"),
            turn: TURN_INTO.find((one) => one.active(e))?.label ?? "Text",
        }),
    });
    const current = marks.turn;

    return (
        <BubbleMenu
            editor={editor}
            className="seltool"
            updateDelay={120}
            /* On the page body, so a narrow pane beside a list cannot clip it;
               fixed, flipped and shifted to stay on screen. */
            appendTo={() => document.body}
            options={{ strategy: "fixed", placement: "top", offset: 10, flip: true, shift: { padding: 12 } }}
            /* Text only: a selected block (an image, a view, a sub-page) is a
               NodeSelection, and bold does not mean anything to it. */
            shouldShow={({ editor: e, from, to, state }) => from !== to && e.isEditable && !e.isActive("codeBlock")
                && !("node" in state.selection && (state.selection as { node?: unknown }).node)}
            onMouseDown={(event) => { if ((event.target as HTMLElement).tagName !== "INPUT") event.preventDefault(); }}
        >
            {mode === "ask" || mode === "link" ? (
                <form
                    className="seltool__form"
                    onSubmit={(event) => {
                        event.preventDefault();
                        if (!text.trim()) return;
                        if (mode === "ask") onAsk?.(text.trim(), selected());
                        else editor.chain().focus().extendMarkRange("link").setLink({ href: text.trim() }).run();
                        reset();
                    }}
                >
                    <input
                        autoFocus
                        value={text}
                        placeholder={mode === "ask" ? "Ask " + botName + " to change this…" : "Paste a link…"}
                        onChange={(event) => setText(event.target.value)}
                        onKeyDown={(event) => { if (event.key === "Escape") { reset(); editor.commands.focus(); } }}
                    />
                    <button type="submit" disabled={!text.trim()}>{mode === "ask" ? "Ask" : "Link"}</button>
                </form>
            ) : mode === "turn" ? (
                <div className="seltool__menu" role="menu">
                    {TURN_INTO.map((one) => (
                        <button key={one.id} role="menuitemradio" aria-checked={one.active(editor)} onClick={() => { one.run(editor); reset(); }}>
                            {one.label}
                        </button>
                    ))}
                </div>
            ) : (
                <>
                    {onAsk && (
                        <>
                            <button className="seltool__ask" onClick={() => setMode("ask")}><ChatIcon size={15} /> Ask for change</button>
                            <i className="seltool__sep" />
                        </>
                    )}
                    <button className="seltool__icon" aria-pressed={marks.bold} title="Bold" onClick={() => editor.chain().focus().toggleBold().run()}><b>B</b></button>
                    <button className="seltool__icon" aria-pressed={marks.italic} title="Italic" onClick={() => editor.chain().focus().toggleItalic().run()}><i>I</i></button>
                    <button className="seltool__icon" aria-pressed={marks.strike} title="Strikethrough" onClick={() => editor.chain().focus().toggleStrike().run()}><s>S</s></button>
                    <button className="seltool__icon" aria-pressed={marks.code} title="Code" onClick={() => editor.chain().focus().toggleCode().run()}><CodeIcon size={15} /></button>
                    <button
                        className="seltool__icon"
                        aria-pressed={marks.link}
                        title={marks.link ? "Remove link" : "Link"}
                        onClick={() => (marks.link ? editor.chain().focus().unsetLink().run() : (setText(""), setMode("link")))}
                    >
                        <LinkIcon size={15} />
                    </button>
                    <i className="seltool__sep" />
                    <button className="seltool__turn" onClick={() => setMode("turn")}>{current} <ChevronDownIcon size={13} /></button>
                    {onComment && (
                        <>
                            <i className="seltool__sep" />
                            <button className="seltool__ask" onClick={onComment}><ChatIcon size={15} weight="duotone" /> Comment</button>
                        </>
                    )}
                </>
            )}
        </BubbleMenu>
    );
}

/* ── the block handle ──────────────────────────────────────────────── */

/** Beside the block under the pointer: `+` to add one below (opening the
 *  slash menu there), and a grip to drag the block somewhere else. */
export function BlockHandle({ editor }: { editor: Editor }) {
    const target = useRef<{ node: PMNode | null; pos: number }>({ node: null, pos: -1 });
    return (
        <DragHandle editor={editor} onNodeChange={({ node, pos }) => { target.current = { node, pos }; }}>
            <div className="bhandle">
                <button
                    className="bhandle__add"
                    title="Add a block below"
                    aria-label="Add a block below"
                    onClick={() => {
                        const { node, pos } = target.current;
                        if (!node || pos < 0) return;
                        const after = pos + node.nodeSize;
                        editor.chain().insertContentAt(after, { type: "paragraph", content: [{ type: "text", text: "/" }] }).focus(after + 2).run();
                    }}
                >
                    <PlusIcon size={15} />
                </button>
                <span className="bhandle__grip" title="Drag to move" aria-hidden="true">⋮⋮</span>
            </div>
        </DragHandle>
    );
}
