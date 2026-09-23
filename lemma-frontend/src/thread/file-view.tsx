import { PdfPreview } from "./pdf-preview";
import { EmbedPreview } from "./embed-preview";
import { framedDocument } from "./framed-document";
import { ExternalIcon, FileIcon } from "@/ui/icons";
import { Suspense, lazy, useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { source } from "@/data";
import type { FileContent } from "@/data";
import { Prose } from "./markdown";
import { editableKind, lockedBecause, sayLocked } from "./document-save";
import { splitFrontmatter } from "@/skills/skill-frontmatter";
import { appThemeMessage, onAppearanceChange, widgetThemeMessage } from "./widget-theme";

/** A page taking the whole pane.
 *
 *  Its own component only because it needs hooks, and the view around it decides
 *  what to draw after several early returns. What it adds over a bare iframe is
 *  the theme: the same document `EmbedPreview` builds, and the same message on
 *  load and on every appearance change after it — so a page opened on the stage
 *  follows light and dark instead of staying whatever it was written as. It does
 *  not reload to do it, which matters for a page that is doing something. */
function PageFrame({ title, html }: { title: string; html: string }) {
    const frame = useRef<HTMLIFrameElement>(null);
    const id = useId();
    const document_ = useMemo(() => framedDocument(html, id), [html, id]);

    /* `srcDoc` has an opaque origin, so the theme is addressed at "*". It is
       always srcDoc here: the bytes are the only way a pod file reaches a
       frame at all. */
    const sendTheme = useCallback(() => {
        const view = frame.current?.contentWindow;
        if (!view) return;
        try {
            view.postMessage(widgetThemeMessage(), "*");
            view.postMessage(appThemeMessage(), "*");
        } catch { /* frame not ready yet */ }
    }, []);
    useEffect(() => {
        sendTheme();
        return onAppearanceChange(sendTheme);
    }, [sendTheme]);

    return (
        <div className="fileview--bleed">
            <iframe
                ref={frame}
                className="fileview__frame"
                title={title}
                sandbox="allow-scripts"
                onLoad={sendTheme}
                srcDoc={document_}
            />
        </div>
    );
}

/** The editor, which most sessions never need.
 *
 *  It is a rich-text engine and its markdown bridge — the single largest thing
 *  this app would ship — and it is only ever wanted when a markdown file is
 *  open as a tab. Static, it rode along in the bundle for everybody who came
 *  to read a conversation, which is nearly everybody. The same reasoning the
 *  character rigs and the sample fixtures are already loaded this way.
 *
 *  The fallback is the document itself, rendered the way it always was. So the
 *  wait is not a blank pane or a spinner: you are reading within the first
 *  paint, on the same type at the same measure, and the page quietly becomes
 *  one you can type into a moment later. */
const DocumentEditor = lazy(() =>
    import("./document-editor").then((module) => ({ default: module.DocumentEditor })),
);

function DocumentSurface({ podId, path, text }: { podId: string; path: string; text: string }) {
    return (
        <Suspense fallback={<div className="doc-host"><Prose text={splitFrontmatter(text).body} /></div>}>
            <DocumentEditor podId={podId} path={path} text={text} />
        </Suspense>
    );
}

/** The `---` block at the top of a file, shown as the thing it is.
 *
 *  Not prose, so it is not set as prose: a fence and some keys are a header
 *  the agent runtime reads, and a markdown renderer turns them into a
 *  horizontal rule and a very large heading — which would open every SKILL.md
 *  on this surface with its own frontmatter as the headline.
 *
 *  Verbatim rather than parsed into fields. The parser this app uses copies
 *  the loader's, and the loader skips lines it does not understand; printing
 *  what it kept would quietly drop whatever it skipped. The bytes cannot be
 *  wrong about themselves.
 *
 *  Read-only in both paths, including beside a document you can type in. What
 *  is under the fence is prose and edits like prose; what is above it is a
 *  contract — the loader refuses a skill whose `name` stops matching its
 *  folder — and that is a different kind of edit than fixing a sentence.
 */
function DocumentFront({ raw }: { raw: string }) {
    return (
        <pre className="doc-front" aria-label="Frontmatter">{raw}</pre>
    );
}

function readableSize(bytes: number): string {
    if (!bytes) return "";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

/** A file the agent put on screen.
 *
 *  In the transcript it is a preview with one action: open it, which puts it
 *  on the stage as a tab of this teammate. On the stage it has no header at
 *  all — the tab is already its name, and repeating that above the content is
 *  a row that says nothing.
 *
 *  Markdown is the common case: an agent writes a report and shows it. HTML
 *  goes in a sandbox for the same reason any markup this app did not write
 *  does. Images use signed URLs. PDFs use an authenticated download and a
 *  component-owned object URL so attachment headers do not prevent preview.
 *  Unsupported formats keep explicit download/open actions. */
export function FileView({
    podId,
    path,
    full,
    onOpenTab,
}: {
    podId: string;
    path: string;
    full?: boolean;
    onOpenTab?: (path: string) => void;
}) {
    const [expanded, setExpanded] = useState(full ?? false);
    /* If the browser refuses the bytes, the card below is the answer — a link
       the reader can follow beats a broken-image glyph or a dead player. It
       covers more than a bad URL: a signed URL is short-lived, and a codec the
       browser will not take (.mov in Chrome) fails exactly the same way. */
    const [mediaFailed, setMediaFailed] = useState(false);

    const file = useQuery({
        queryKey: ["file", podId, path],
        queryFn: () => source.readFile(podId, path),
        staleTime: 5 * 60_000,
    });

    const name = path.split("/").filter(Boolean).pop() ?? path;

    if (file.isPending || file.isError || !file.data) {
        return (
            <div className="resource">
                <span className="resource__glyph"><FileIcon size={22} /></span>
                <span className="resource__body">
                    <span className="resource__name">{name}</span>
                    <span className="resource__type">
                        {file.isPending ? "Loading file…" : "Unable to read this file. It may have moved or you may not have access."}
                    </span>
                </span>
                {file.isError && <button onClick={() => void file.refetch()}>Retry</button>}
            </div>
        );
    }

    const data: FileContent = file.data;
    const meta = [data.kind, readableSize(data.size)].filter(Boolean).join(" · ");

    /** Only a preview carries a header; on the stage the tab is the title. */
    const head = full ? null : (
        <figcaption className="filecard__head">
            <span className="filecard__name">{data.name}</span>
            <span className="filecard__meta">{meta}</span>
            {onOpenTab && (
                <button className="filecard__open" onClick={() => onOpenTab(data.path)}>
                    Open
                </button>
            )}
        </figcaption>
    );

    const shell = "filecard" + (full ? " filecard--full" : "");

    if (data.kind === "markdown" || data.kind === "text") {
        const long = !full && (data.text ?? "").length > 1400;
        /* A document is writable where it is the whole screen, and nowhere
           else. In the transcript this card is one thing in a column of
           things — a preview of what the agent made, beside what it said about
           it — and a caret in the middle of that column is an invitation to
           edit the conversation. So the preview stays a preview, and Open is
           still the way in. */
        const locked = full && data.kind === "markdown" ? lockedBecause(data.text ?? "") : null;
        const writable = full && editableKind(data.kind) && !locked;
        /* Split for the reader too, not only for the editor. A `---` block fed
           to a markdown renderer comes out as a rule followed by a setext
           heading. Holding it aside in one path and not the other would make
           the same file look like two different files depending on whether it
           happened to be editable. */
        const front = data.kind === "markdown" ? splitFrontmatter(data.text ?? "").front : null;
        return (
            <figure className={shell + (long && !expanded ? " filecard--clipped" : "")}>
                {head}
                <div className="filecard__body">
                    {front && <DocumentFront raw={front} />}
                    {writable ? (
                        <DocumentSurface podId={podId} path={data.path} text={data.text ?? ""} />
                    ) : data.kind === "markdown" ? (
                        <Prose text={splitFrontmatter(data.text ?? "").body} />
                    ) : (
                        <pre className="filecard__pre">{data.text}</pre>
                    )}
                    {/* Under the document rather than over it: the document is
                        why anybody opened the tab, and this is a footnote about
                        what cannot be done to it. */}
                    {locked && <p className="doc-locked" role="note">{sayLocked(locked)}</p>}
                </div>
                {long && (
                    <button className="filecard__more" onClick={() => setExpanded((was) => !was)}>
                        {expanded ? "Show less" : "Show all"}
                    </button>
                )}
            </figure>
        );
    }

    /* On the stage an HTML file is already a page: it has its own margins, its
       own type and its own idea of a header. Boxing it in a card with a preview
       toolbar is a frame around a frame, and the toolbar repeats what the tab
       strip and the view actions already say. So it takes the pane. In the
       transcript it stays a preview, because there it really is one thing among
       many in a column.
     *
     *  Both need the bytes. A file with no `text` is one this app could not
     *  read, and it belongs on the card at the bottom saying so — not in a
     *  frame pointed at a URL that answers with a download. */
    if (data.kind === "html" && data.text) {
        if (full) return <PageFrame title={data.name} html={data.text} />;
        return (
            <figure className={shell}>
                {head}
                <EmbedPreview
                    title={data.name}
                    html={data.text}
                    onOpen={onOpenTab ? () => onOpenTab(data.path) : undefined}
                />
            </figure>
        );
    }

    /* Media is looked at, so it is shown rather than described. No card, no
       header, no size — the thing itself with its own corners, and the name and
       the way out only when the pointer is on it. What an agent says about a
       video it just made is in the message above it; a row repeating the
       filename and "6.4 MB" over the top of that is chrome about chrome. */
    if ((data.kind === "image" || data.kind === "video" || data.kind === "audio") && data.rawUrl && !mediaFailed) {
        if (full) {
            return (
                <div className={"fileview--bleed fileview--" + data.kind}>
                    {data.kind === "image" ? (
                        <img src={data.rawUrl} alt={data.name} onError={() => setMediaFailed(true)} />
                    ) : data.kind === "video" ? (
                        <video src={data.rawUrl} controls playsInline preload="metadata" onError={() => setMediaFailed(true)} />
                    ) : (
                        <audio src={data.rawUrl} controls preload="metadata" onError={() => setMediaFailed(true)} />
                    )}
                </div>
            );
        }
        return (
            <figure className={"media media--" + data.kind}>
                {data.kind === "image" ? (
                    <img
                        className="media__el"
                        src={data.rawUrl}
                        alt={data.name}
                        loading="lazy"
                        onError={() => setMediaFailed(true)}
                    />
                ) : data.kind === "video" ? (
                    <video
                        className="media__el"
                        src={data.rawUrl}
                        controls
                        playsInline
                        preload="metadata"
                        onError={() => setMediaFailed(true)}
                    />
                ) : (
                    <audio
                        className="media__el"
                        src={data.rawUrl}
                        controls
                        preload="metadata"
                        onError={() => setMediaFailed(true)}
                    />
                )}
                <figcaption className="media__cap">
                    <span className="media__name">{data.name}</span>
                    {onOpenTab && (
                        <button className="media__open" onClick={() => onOpenTab(data.path)}>
                            Open
                        </button>
                    )}
                </figcaption>
            </figure>
        );
    }

    if (data.kind === "pdf") {
        return (
            <figure className={shell}>
                {head}
                <PdfPreview podId={podId} path={path} name={data.name} size={data.size} rawUrl={data.rawUrl} full={full}/>
            </figure>
        );
    }

    /* Nothing this app will draw. On the stage that is the whole view, so the
       platform link is the only way onward and earns its place there. */
    const body = (
        <>
            <span className="resource__glyph"><FileIcon size={22} /></span>
            <span className="resource__body">
                <span className="resource__name">{data.name}</span>
                {/* What actually happened, when this app knows. "Preview
                    unavailable for this format" over a .html file was a
                    sentence about the wrong thing: the format is drawn, the
                    bytes were the problem. */}
                <span className="resource__type">
                    {data.note ?? (full ? "Preview unavailable for this format · use Download to save it" : meta || data.path)}
                </span>
            </span>
            <span className="resource__go">Open{full && <ExternalIcon size={14} />}</span>
        </>
    );

    if (!full && onOpenTab) {
        return (
            <button className="resource resource--link" onClick={() => onOpenTab(data.path)}>
                {body}
            </button>
        );
    }

    const href = data.appUrl ?? data.rawUrl;
    return href ? (
        <a className="resource resource--link" href={href} target="_blank" rel="noreferrer">
            {body}
        </a>
    ) : (
        <div className="resource">{body}</div>
    );
}
