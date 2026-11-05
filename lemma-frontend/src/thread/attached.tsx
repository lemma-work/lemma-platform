"use client";

import { useEffect, useRef, useState } from "react";
import { CloseIcon, FileIcon } from "@/ui/icons";
import { describeSize, type Attachment } from "./attachments";

/** Files waiting to go with the next message, above the box that sends them —
 *  the composer's and the ask box's, which hold the same files the same way. */
export function AttachedFiles({ files, onRemove, className = "attached" }: {
    files: readonly Attachment[];
    onRemove?: (key: string) => void;
    className?: string;
}) {
    if (files.length === 0) return null;
    return (
        <div className={className} aria-label="Attached files">
            {files.map((one) => <AttachedFile key={one.key} one={one} onRemove={onRemove} />)}
        </div>
    );
}

/** A picture is drawn as itself. A pasted screenshot is renamed on the way in
 *  (`pasted-20261022-101500.png`), so its name says nothing about which one it
 *  is; the picture does. Anything a browser cannot decode — a PDF, a HEIC from
 *  a phone, an SVG — stays a named chip. */
function AttachedFile({ one, onRemove }: { one: Attachment; onRemove?: (key: string) => void }) {
    const [unreadable, setUnreadable] = useState(false);
    const state = one.status === "uploading" ? "sending…"
        : one.status === "failed" ? "failed"
        : null;
    const drop = (
        <button
            type="button"
            className="attached__drop"
            aria-label={"Remove " + one.file.name}
            title={"Remove " + one.file.name}
            onClick={() => onRemove?.(one.key)}
        >
            <CloseIcon size={12} />
        </button>
    );

    if (one.file.type.startsWith("image/") && !unreadable) {
        return (
            <span className="attached__thumb" data-status={one.status} title={one.error ?? one.file.name}>
                <Thumbnail file={one.file} onUnreadable={() => setUnreadable(true)} />
                {state && <span className="attached__state">{state}</span>}
                {drop}
            </span>
        );
    }
    return (
        <span className="attached__chip" data-status={one.status}>
            <FileIcon size={14} />
            <span className="attached__name" title={one.error ?? one.file.name}>{one.file.name}</span>
            <span className="attached__size">{state ?? describeSize(one.file.size)}</span>
            {drop}
        </span>
    );
}

/** The picture, decoded and drawn onto a canvas the size of the tile.
 *
 *  Not an `<img>` pointed at an object URL: that hands the page a URL made
 *  from a file the person chose, which code scanning reads as their input
 *  reaching a DOM attribute, and it keeps the whole screenshot decoded at full
 *  size to show 64 pixels of it. A canvas holds the tile and nothing else. */
function Thumbnail({ file, onUnreadable }: { file: File; onUnreadable: () => void }) {
    const canvas = useRef<HTMLCanvasElement | null>(null);
    const unreadable = useRef(onUnreadable);
    unreadable.current = onUnreadable;
    useEffect(() => {
        if (typeof createImageBitmap !== "function") {
            unreadable.current();
            return;
        }
        let live = true;
        createImageBitmap(file).then(
            (bitmap) => {
                if (live && canvas.current) drawCovering(canvas.current, bitmap);
                bitmap.close();
            },
            () => { if (live) unreadable.current(); },
        );
        return () => { live = false; };
    }, [file]);
    return <canvas ref={canvas} role="img" aria-label={file.name} />;
}

/** The middle square of the picture, at the tile's size in device pixels —
 *  what `object-fit: cover` would show in a square. */
function drawCovering(canvas: HTMLCanvasElement, bitmap: ImageBitmap) {
    const side = Math.round((canvas.clientWidth || 64) * (window.devicePixelRatio || 1));
    canvas.width = side;
    canvas.height = side;
    const crop = Math.min(bitmap.width, bitmap.height);
    canvas.getContext("2d")?.drawImage(
        bitmap,
        (bitmap.width - crop) / 2, (bitmap.height - crop) / 2, crop, crop,
        0, 0, side, side,
    );
}
