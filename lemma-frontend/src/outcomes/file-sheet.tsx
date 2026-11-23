"use client";

import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { Prose } from "@/thread/markdown";
import { saveFile } from "@/thread/save-file";
import { CloseIcon, DeleteIcon, DownloadIcon, EditIcon, FileIcon, FolderIcon, ImageIcon, MoveIcon, PlayIcon, TableIcon, TextIcon, VoiceIcon } from "@/ui/icons";
import { LoadingIndicator } from "@/ui/loading";
import { excerptOf, kindLine, kindOf, readStateOf, type FileItem, type MarkKind } from "./file-kinds";

const GLYPHS: Record<MarkKind, typeof FileIcon> = {
    folder: FolderIcon,
    spreadsheet: TableIcon,
    pdf: FileIcon,
    document: FileIcon,
    markdown: TextIcon,
    image: ImageIcon,
    audio: VoiceIcon,
    video: PlayIcon,
    other: FileIcon,
};

/** What kind of thing a file is, at a glance: a coloured square with a
 *  glyph, the colour from the kind (`lemma-knows.css`, `.ox-files-mark`) so
 *  a column of them reads as PDFs here and sheets there before any word is
 *  read. Decoration beside the words that say the same, so hidden from a
 *  screen reader. */
export function KindMark({ mark, large = false }: { mark: MarkKind; large?: boolean }) {
    const Glyph = GLYPHS[mark];
    return (
        <span className={"kmark ox-files-mark" + (large ? " kmark--lg" : "")} data-kind={mark} aria-hidden="true">
            <Glyph size={large ? 26 : 16} />
        </span>
    );
}

/** When something changed, in full, for the sheet's "Changed". */
function changedOn(iso: string): string {
    const at = new Date(iso);
    if (Number.isNaN(at.getTime())) return "";
    const sameYear = at.getFullYear() === new Date().getFullYear();
    return at.toLocaleString([], sameYear
        ? { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }
        : { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** Kinds worth a look before opening: text that reads in a few lines, and
 *  an image the browser draws from its link. Anything heavier (a PDF, a
 *  film) is one click away on Open, and is not fetched to be glanced at. */
function previewable(item: FileItem): boolean {
    const { mark, word } = kindOf(item);
    return mark === "markdown" || mark === "image" || word === "Text" || word === "Data";
}

/** One file, from the side: what it is, a look at it where that is cheap,
 *  the ways to use it, and what is known about it.
 *
 *  A sheet rather than the file's own page, because clicking a row is
 *  usually a question about it -- is this the one, who added it, has the
 *  Lemma read it -- and the list should still be there when the answer is
 *  no. Open takes it to its page. Rename and Move hand back to the list,
 *  where the change happens in its row; Delete asks first, over the sheet.
 *  In the sample nothing changes, and the foot says so rather than offering
 *  buttons that can only fail. */
export function FileSheet({ pod, item, where, by, live, busy, onOpen, onAsk, onRetry, onRename, onMove, onDelete, onClose }: {
    pod: Pod;
    item: FileItem;
    /** Where it is, from the Lemma down: "Sales › Files › Proposals". */
    where: string;
    /** Who added it, where the listing says and the person is known here. */
    by: string | null;
    live: boolean;
    /** A write on this file is under way. */
    busy: boolean;
    onOpen: () => void;
    onAsk: () => void;
    onRetry: () => void;
    onRename: () => void;
    onMove: () => void;
    onDelete: () => void;
    onClose: () => void;
}) {
    const panel = useRef<HTMLElement>(null);
    const titleId = useId();
    const closeRef = useRef(onClose);
    useEffect(() => {
        closeRef.current = onClose;
    });
    const [saving, setSaving] = useState<"idle" | "busy" | "failed">("idle");
    const kind = kindOf(item);
    const look = previewable(item);
    /* Under the file page's own key, so opening it after a look starts
       from what was read here. */
    const preview = useQuery({
        queryKey: ["file", pod.id, item.path],
        queryFn: () => source.readFile(pod.id, item.path),
        enabled: look,
        staleTime: 5 * 60_000,
        retry: false,
    });
    const size = typeof item.size === "number" ? item.size : preview.data?.size;
    const line = kindLine(typeof size === "number" ? { ...item, size } : item);
    const reading = readStateOf(item.status);

    /* Focus moves in, the keyboard stays in while it is open, Escape closes
       it, and focus goes back to the row that opened it. A dialog over it
       (Move, Delete) answers its own keys first. */
    useEffect(() => {
        const previous = document.activeElement as HTMLElement | null;
        panel.current?.querySelector<HTMLElement>(".ksheet__x")?.focus();
        const onKey = (event: KeyboardEvent) => {
            if (event.defaultPrevented || document.querySelector(".modal")) return;
            if (event.key === "Escape") { event.preventDefault(); closeRef.current(); return; }
            if (event.key !== "Tab" || !panel.current) return;
            const focusable = Array.from(panel.current.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input:not([disabled])')).filter((el) => el.getClientRects().length > 0);
            const first = focusable[0], last = focusable.at(-1);
            if (!first) { event.preventDefault(); return; }
            if (!panel.current.contains(document.activeElement)) { event.preventDefault(); first.focus(); return; }
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        };
        document.addEventListener("keydown", onKey);
        return () => {
            document.removeEventListener("keydown", onKey);
            if (previous?.isConnected) previous.focus();
        };
    }, []);

    async function download() {
        setSaving("busy");
        try {
            await saveFile(pod.id, preview.data ?? { name: item.name, path: item.path, mime: item.mime ?? "", size: size ?? 0, kind: "binary" });
            setSaving("idle");
        } catch {
            setSaving("failed");
        }
    }

    let shown = null;
    if (look) {
        if (preview.isPending) {
            shown = <p className="ox-files-quiet"><LoadingIndicator inline label="Reading it" /></p>;
        } else if (preview.data?.kind === "image" && preview.data.rawUrl) {
            shown = <img className="ox-files-look ox-files-look--img" src={preview.data.rawUrl} alt={"A look at " + item.name} />;
        } else if (preview.data?.text !== undefined) {
            const text = excerptOf(preview.data.text);
            /* A note reads as a note, its headings and bold drawn rather
               than spelled out in # and **. */
            shown = !text ? <p className="ox-files-quiet">It’s empty.</p>
                : kind.mark === "markdown" ? <div className="ox-files-look ox-files-look--md"><Prose text={text} /></div>
                    : <div className="ox-files-look">{text}</div>;
        } else if (preview.data?.note) {
            shown = <p className="ox-files-quiet">{preview.data.note}.</p>;
        }
    }

    return createPortal(
        <>
            <div className="ksheet-veil" aria-hidden="true" onMouseDown={() => { if (!document.querySelector(".modal")) onClose(); }} />
            <aside className="ksheet ox-files-sheet" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={panel}>
                <header className="ksheet__head">
                    <KindMark mark={kind.mark} large />
                    <h2 id={titleId}>{item.name}</h2>
                    <p>{line}</p>
                    <button type="button" className="ksheet__x b b--m b--quiet b--icon" aria-label="Close" onClick={onClose}>
                        <CloseIcon size={18} />
                    </button>
                </header>

                <div className="ksheet__body">
                    {shown}
                    <div className="ox-files-sheet__acts">
                        <button type="button" className="b b--m b--primary" onClick={onOpen}>Open</button>
                        <button type="button" className="b b--m b--secondary" disabled={saving === "busy"} onClick={() => void download()}>
                            <DownloadIcon size={16} />
                            {saving === "busy" ? "Downloading…" : "Download"}
                        </button>
                        <button type="button" className="b b--m b--secondary" onClick={onAsk}>Ask {pod.name} about it</button>
                    </div>
                    {saving === "failed" && <p className="ox-files-bad" role="alert">That didn’t download. Try again.</p>}

                    <section className="ox-files-about" aria-labelledby={titleId + "-about"}>
                        <h3 className="ox-files-sub" id={titleId + "-about"}>About it</h3>
                        <div className="klist">
                            <div className="krow krow--kv">
                                <span className="krow__k">Where</span>
                                <span className="ox-files-about__v">{where}</span>
                            </div>
                            {by && (
                                <div className="krow krow--kv">
                                    <span className="krow__k">Added by</span>
                                    <span className="ox-files-about__v">{by}</span>
                                </div>
                            )}
                            <div className="krow krow--kv">
                                <span className="krow__k">Changed</span>
                                <span className="ox-files-about__v">{changedOn(item.updated)}</span>
                            </div>
                            {reading && (
                                <div className="krow krow--kv">
                                    <span className="krow__k">Read by {pod.name}</span>
                                    <span className="ox-files-about__v">
                                        {reading === "read" && <span className="kstatus">Yes</span>}
                                        {reading === "reading" && <span className="ox-files-state"><i className="ox-knows-spin" aria-hidden="true" />Reading it now…</span>}
                                        {reading === "skipped" && <span className="kstatus kstatus--off">Not needed for this kind of file</span>}
                                        {reading === "failed" && (
                                            <>
                                                <span className="kstatus kstatus--bad">Couldn’t read it</span>
                                                {live && <button type="button" className="b b--s b--secondary" disabled={busy} onClick={onRetry}>Try again</button>}
                                            </>
                                        )}
                                    </span>
                                </div>
                            )}
                        </div>
                    </section>
                    {!live && <p className="ox-files-quiet">This is a sample, so nothing here can be changed.</p>}
                </div>

                <footer className="ksheet__foot ox-files-sheet__foot">
                    <button type="button" className="b b--m b--secondary" disabled={!live || busy} onClick={onRename}>
                        <EditIcon size={16} />
                        Rename
                    </button>
                    <button type="button" className="b b--m b--secondary" disabled={!live || busy} onClick={onMove}>
                        <MoveIcon size={16} />
                        Move
                    </button>
                    <span className="ox-files-grow" />
                    <button type="button" className="b b--m b--secondary b--red" disabled={!live || busy} onClick={onDelete}>
                        <DeleteIcon size={16} />
                        Delete
                    </button>
                </footer>
            </aside>
        </>,
        document.body,
    );
}
