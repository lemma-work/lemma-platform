"use client";

import { Component, useEffect, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileIcon, LinkIcon } from "@/ui/icons";
import { channelApi } from "./api";
import { deliveryNotes } from "./deliveries";
import { problemOf } from "./problem";
import type { ChannelDetail, Line, Part } from "./model";
import { extraLinks, isInlineImage, pieces, sizeShown } from "./parts-model";
import { linesKey, threadKey } from "./queries";

/** A person's line as typed, with who it names as chips and its addresses
 *  as links. */
export function LineText({ text, parts, me }: { text: string; parts: readonly Part[]; me: string | null }) {
    return (
        <p className="cline__text">
            {pieces(text, parts).map((piece, index) => piece.kind === "mention" ? (
                <span key={index} className="chmention" data-you={(piece.who.kind === "MEMBER" && piece.who.id === me) || undefined} data-agent={piece.who.kind === "AGENT" || undefined}>
                    {piece.text}
                </span>
            ) : piece.kind === "link" ? (
                <a key={index} href={piece.url} target="_blank" rel="noopener noreferrer nofollow">{piece.text}</a>
            ) : piece.text)}
        </p>
    );
}

/** The files a line shares, each read through the line -- an image shown,
 *  anything else saved -- and the links it carries that its text does not
 *  show. A file is never opened by its path: that is the writer's own, and
 *  the reader may not read it there. */
export function LineParts(props: { podId: string; channelId: string; line: Line }) {
    return <PartsBoundary><LinePartsList {...props} /></PartsBoundary>;
}

/** What a line shares that could not be drawn is said under it; the
 *  timeline around it -- every other line -- stays. */
class PartsBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
    state = { failed: false };
    static getDerivedStateFromError() {
        return { failed: true };
    }
    componentDidCatch(error: unknown) {
        console.error("A line's files could not be shown", error);
    }
    render() {
        if (this.state.failed) return <p className="cline__problem" role="alert">Couldn’t show what this line shares.</p>;
        return this.props.children;
    }
}

function LinePartsList({ podId, channelId, line }: { podId: string; channelId: string; line: Line }) {
    const files = line.parts.filter((part): part is Extract<Part, { type: "file" }> => part.type === "file");
    const links = extraLinks(line.text ?? "", line.parts);
    if (!files.length && !links.length) return null;
    return (
        <ul className="cline__parts" aria-label="Shared with this line">
            {files.map((file) => (
                <li key={"f" + (file.fileId ?? file.path)}>
                    <FileChip podId={podId} channelId={channelId} lineId={line.id} file={file} />
                </li>
            ))}
            {links.map((link) => (
                <li key={"l" + link.url}>
                    <a className="chfile" href={link.url} target="_blank" rel="noopener noreferrer nofollow">
                        <LinkIcon size={15} aria-hidden="true" />
                        <span className="chfile__name">{link.label ?? link.url}</span>
                    </a>
                </li>
            ))}
        </ul>
    );
}

/** Whether `value` is bytes a URL can be made for: a server answering
 *  with something else (text, JSON) is a file that cannot be shown, not a
 *  timeline that cannot be drawn. */
export function isBlob(value: unknown): value is Blob {
    return typeof Blob !== "undefined" && value instanceof Blob;
}

/** An object URL for `blob` for as long as this component shows it. */
export function useObjectUrl(blob: Blob | null): string | null {
    const [url, setUrl] = useState<string | null>(null);
    useEffect(() => {
        if (!isBlob(blob)) { setUrl(null); return; }
        const made = URL.createObjectURL(blob);
        setUrl(made);
        return () => URL.revokeObjectURL(made);
    }, [blob]);
    return url;
}

export function FileChip({ podId, channelId, lineId, file }: { podId: string; channelId: string; lineId: string; file: Extract<Part, { type: "file" }> }) {
    const image = isInlineImage(file.mimeType) && file.fileId !== null;
    /* The bytes are cached, not a URL for them: a URL is this mount's, made
       here and let go when it goes, so a chip drawn again -- scrolled back
       to, a thread reopened -- never shows one already revoked. */
    const preview = useQuery({
        queryKey: ["channel", podId, "file", channelId, lineId, file.fileId] as const,
        enabled: image,
        staleTime: Infinity,
        gcTime: 5 * 60_000,
        retry: false,
        queryFn: () => channelApi(podId).file(channelId, lineId, file.fileId as string),
    });
    const bytes = isBlob(preview.data) ? preview.data : null;
    const shown = useObjectUrl(bytes);
    const unshown = image && (preview.isError || (preview.isSuccess && !bytes));
    const save = useMutation({
        mutationFn: async () => {
            const blob: unknown = await channelApi(podId).file(channelId, lineId, file.fileId as string);
            if (!isBlob(blob)) throw new Error("The file came back unreadable.");
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = url;
            link.download = file.name;
            link.click();
            setTimeout(() => URL.revokeObjectURL(url), 1_000);
        },
    });
    if (!file.fileId) {
        /* Not stored yet -- a line still being sent -- or from a server that
           does not serve it: said, not offered. */
        return <span className="chfile"><FileIcon size={15} aria-hidden="true" /><span className="chfile__name">{file.name}</span></span>;
    }
    return (
        <span className="chfile__wrap">
            {image && shown && <img className="chfile__preview" src={shown} alt={file.name} loading="lazy" />}
            <button type="button" className="chfile" disabled={save.isPending} onClick={() => save.mutate()} title={"Save " + file.name}>
                <FileIcon size={15} aria-hidden="true" />
                <span className="chfile__name">{file.name}</span>
                {sizeShown(file.size) && <small>{sizeShown(file.size)}</small>}
                {save.isPending && <small>Saving…</small>}
            </button>
            {unshown && !save.isError && <small className="cline__problem" role="alert">Couldn’t show this image.</small>}
            {save.isError && <small className="cline__problem" role="alert">{problemOf(save.error, "Couldn’t get the file.")}</small>}
        </span>
    );
}

/** Where the reader's own line went outside, under it: sent, failed and
 *  why, skipped and why -- with Retry on a send that can be tried again.
 *  The line carries it, and comes again when a send moves on. */
export function DeliveryStatus({ podId, channel, line, canRetry }: { podId: string; channel: ChannelDetail; line: Line; canRetry: boolean }) {
    const cache = useQueryClient();
    const retry = useMutation({
        mutationFn: (deliveryId: string) => channelApi(podId).retryDelivery(channel.id, line.id, deliveryId),
        /* The socket sends the line again as its send moves; read it again
           too, for when the socket is down. */
        onSettled: () => {
            void cache.invalidateQueries({ queryKey: linesKey(podId, channel.id) });
            if (line.threadRootId) void cache.invalidateQueries({ queryKey: threadKey(podId, channel.id, line.threadRootId) });
        },
    });
    const notes = deliveryNotes(line.delivered, channel.places);
    if (!notes.length) return null;
    return (
        <div className="cline__sent" aria-label="Where it went">
            {notes.map((note) => (
                <span key={note.id} className="cline__delivery" data-tone={note.tone}>
                    {note.said}
                    {note.retry && canRetry && (
                        <> · <button type="button" className="cline__act" disabled={retry.isPending} onClick={() => retry.mutate(note.id)}>{retry.isPending && retry.variables === note.id ? "Retrying…" : "Retry"}</button></>
                    )}
                </span>
            ))}
            {retry.isError && <span className="cline__problem" role="alert">{problemOf(retry.error, "Couldn’t retry it.")}</span>}
        </div>
    );
}
