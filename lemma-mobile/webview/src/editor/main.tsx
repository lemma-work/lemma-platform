import { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { DocumentEditor } from "@/thread/document-editor";
import { PageToolsContext, type PageTools } from "@/docpages/page-context";
import { PaneVisibleContext } from "@/shell/pane-visible";
import { splitFrontmatter } from "@/skills/skill-frontmatter";
import { source, type FileContent } from "@/data";
import type { EditorInit, HighlightAnchor, PageTheme, ToPage } from "../../../src/areas/files/web/protocol";
import { call, listen, pending, post } from "./bridge";
import "./phone.css";

/** A page, as the phone shows it: the web's editor, whole, inside the app.
 *
 *  This file is the frame the web's `DocSpace` would be: it hands the editor
 *  its page tools (where to go, whom to ask, the comments to draw) and its
 *  file, and it carries what the editor says about saving out to the app's
 *  own header. Everything else is the web's code, unchanged. */

/* ── the theme: the app's palette, under the web's names ─────────────── */

const STATUS = {
    light: { ok: "#2f7d57", wait: "#b5811f", bad: "#b3261e", info: "#0169cc", shadow: "0 0 0 1px rgb(0 0 0 / 0.07)" },
    dark: { ok: "#6ecf9c", wait: "#dcb06a", bad: "#f08a7e", info: "#48aaff", shadow: "0 0 0 1px rgb(255 255 255 / 0.08)" },
};

function applyTheme(theme: PageTheme) {
    const p = theme.palette;
    const s = STATUS[theme.scheme];
    const root = document.documentElement;
    const vars: Record<string, string> = {
        /* The app's own names, for this page's stylesheet. */
        ...Object.fromEntries(Object.entries(p).map(([name, value]) => ["--" + name, value])),
        /* The web's names, which a widget's theme is read from (`widget-theme.ts`). */
        "--canvas": p.paper, "--wash-b": p.wash, "--ink-2": p.ink2, "--ink-3": p.ink3,
        "--line-2": p.line2, "--accent": p.ink, "--accent-soft": p.faint,
        "--ok": s.ok, "--wait": s.wait, "--bad": s.bad, "--info": s.info,
        "--widget-shadow-rest": s.shadow, "--widget-shadow-raise": s.shadow, "--widget-inset": "1px",
        "--r-sm": "8px", "--r-md": "12px", "--r-lg": "16px", "--r-xl": "24px", "--ease-out": "cubic-bezier(.2,.8,.2,1)",
    };
    for (const [name, value] of Object.entries(vars)) root.style.setProperty(name, value);
    root.dataset.theme = theme.scheme;
    root.style.colorScheme = theme.scheme;
}

/* ── the save line, carried to the app ───────────────────────────────── */

/** The editor writes its save status into a slot it is given. The slot is
 *  never shown here; what is in it is read and sent to the app's header. */
function watchStatus(slot: HTMLElement): () => void {
    let last = "";
    const report = () => {
        const status = slot.querySelector(".doc-status");
        const label = status ? Array.from(status.childNodes).filter((node) => node.nodeType === Node.TEXT_NODE).map((node) => node.textContent ?? "").join("").trim() : "";
        const bad = Boolean(status?.classList.contains("doc-status--bad"));
        const said = label + "|" + bad;
        if (said === last) return;
        last = said;
        post({ type: "status", label: label || null, bad });
    };
    const observer = new MutationObserver(report);
    observer.observe(slot, { subtree: true, childList: true, characterData: true, attributes: true });
    report();
    return () => observer.disconnect();
}

/* ── edits made in a block's own controls ────────────────────────────── */

type Focusable = { isFocused: boolean; view: { hasFocus: () => boolean } };

/** The editor writes a change only while it has focus (`onUpdate` in
 *  `document-editor.tsx`), so opening a file, which re-reads it, never
 *  rewrites it. A block's own controls take focus from the editor, though:
 *  "Show view" on a live table view, "Show it" on an HTML block. Their change
 *  was dropped unless somebody went on to type, which on a phone they rarely
 *  do. A tap or a change inside a block lends the editor its focus for as
 *  long as the block's own handler runs, and no longer, so the same rule
 *  holds everywhere else. (The web has the same gap; this closes it here.) */
function keepBlockEdits(): void {
    const editorOf = () => (document.querySelector(".ProseMirror") as (HTMLElement & { editor?: Focusable }) | null)?.editor;
    const inBlock = (target: EventTarget | null) => target instanceof Element && Boolean(target.closest(".ProseMirror [contenteditable=false]"));
    const settle = () => {
        const editor = editorOf();
        if (editor) editor.isFocused = editor.view.hasFocus();
    };
    const lend = (event: Event) => {
        const editor = editorOf();
        if (!editor || !inBlock(event.target)) return;
        editor.isFocused = true;
        /* Taken back after the handlers, even one that stops the event. */
        setTimeout(settle, 0);
    };
    for (const type of ["click", "change"]) {
        window.addEventListener(type, lend, true);
        window.addEventListener(type, settle, false);
    }
}

/* ── the page ────────────────────────────────────────────────────────── */

const cache = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } });

function Page({ init }: { init: EditorInit }) {
    const [path, setPath] = useState(init.path);
    const [visible, setVisible] = useState(true);
    const [comments, setComments] = useState<{ anchors: HighlightAnchor[]; active: string | null; enabled: boolean }>({ anchors: [], active: null, enabled: !init.personal });
    const [slot, setSlot] = useState<HTMLElement | null>(null);
    const pathNow = useRef(path);
    pathNow.current = path;

    /* The file under the key the editor writes to and the change check
       invalidates, so a change from elsewhere is read in the web's way. */
    const file = useQuery({
        queryKey: ["file", init.podId, path],
        queryFn: () => source.readFile(init.podId, path),
        initialData: path === init.path ? fileOf(init) : undefined,
        staleTime: 5 * 60_000,
    });

    useEffect(() => listen((message: ToPage) => {
        switch (message.type) {
            case "text":
                cache.setQueryData<FileContent>(["file", init.podId, pathNow.current], (was) => (was ? { ...was, text: message.text } : was));
                return;
            case "theme": applyTheme(message); return;
            case "visible": setVisible(message.visible); return;
            case "comments": setComments({ anchors: message.anchors, active: message.active, enabled: message.enabled && !init.personal }); return;
            case "retry": slot?.querySelector<HTMLButtonElement>(".doc-status__again")?.click(); return;
            case "blur": (document.activeElement as HTMLElement | null)?.blur(); return;
        }
    }), [init.podId, init.personal, slot]);

    useEffect(() => (slot ? watchStatus(slot) : undefined), [slot]);

    const tools: PageTools = useMemo(() => ({
        podId: init.podId,
        path,
        botName: init.botName,
        openFile: (to: string) => post({ type: "open-file", path: to }),
        renamePage: init.personal ? undefined : async (from: string, title: string) => {
            const to = await call<string>("renamePage", from, title);
            if (to !== from) {
                const was = cache.getQueryData<FileContent>(["file", init.podId, from]);
                if (was) cache.setQueryData<FileContent>(["file", init.podId, to], { ...was, path: to, name: to.slice(to.lastIndexOf("/") + 1) });
                setPath(to);
            }
            return to;
        },
        openTable: (name: string) => post({ type: "open-table", name }),
        statusSlot: slot,
        sendToBot: (text: string) => post({ type: "ask", text }),
        comments: comments.enabled ? {
            anchors: comments.anchors,
            active: comments.active,
            start: (anchor) => post({ type: "comment-start", anchor }),
            focus: (id) => post({ type: "comment-focus", id }),
        } : null,
    }), [init.podId, init.botName, init.personal, path, slot, comments]);

    const text = file.data?.text ?? init.text;
    const front = splitFrontmatter(text).front;
    return (
        <PaneVisibleContext.Provider value={visible}>
            <PageToolsContext.Provider value={tools}>
                <div className="phone-page">
                    <div className="phone-page__slot" ref={setSlot} aria-hidden="true" />
                    {front && <pre className="doc-front" aria-label="Frontmatter">{front}</pre>}
                    {/* Keyed by where the editor began: a rename moves the
                        file under it without building a new editor. */}
                    <DocumentEditor key={init.path} podId={init.podId} path={path} text={text} />
                </div>
            </PageToolsContext.Provider>
        </PaneVisibleContext.Provider>
    );
}

function fileOf(init: EditorInit): FileContent {
    const name = init.path.slice(init.path.lastIndexOf("/") + 1);
    return { name, path: init.path, mime: "text/markdown", size: init.text.length, kind: "markdown", text: init.text };
}

/* ── starting, and leaving ───────────────────────────────────────────── */

const root = createRoot(document.getElementById("root")!);
const wait = (ms: number) => new Promise((done) => setTimeout(done, ms));

/** Hand over what is typed before the page goes. The editor writes what it
 *  holds when it is taken down, and the app carries the write through from
 *  the moment the call reaches it, whether or not this page is still there. */
async function flush() {
    (document.activeElement as HTMLElement | null)?.blur();
    await wait(40);
    root.unmount();
    for (let waited = 0; waited < 400 && pending() === 0; waited += 20) await wait(20);
    post({ type: "flushed" });
}

keepBlockEdits();

let started = false;
listen((message) => {
    if (message.type === "flush") { void flush(); return; }
    if (message.type !== "init" || started) return;
    started = true;
    source.label = message.sample ? "sample" : "live";
    applyTheme(message.theme);
    root.render(
        <QueryClientProvider client={cache}>
            <Page init={message} />
        </QueryClientProvider>,
    );
});

document.addEventListener("focusin", () => post({ type: "focus", focused: true }));
document.addEventListener("focusout", () => setTimeout(() => {
    if (!document.activeElement || document.activeElement === document.body) post({ type: "focus", focused: false });
}, 0));
window.addEventListener("error", (event) => post({ type: "problem", message: String(event.message) }));
post({ type: "ready" });
