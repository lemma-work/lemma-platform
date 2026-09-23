'use client';

/**
 * Showing one sandbox file, wherever it is being shown.
 *
 * Extracted because there are two places now: the compact pane beside a
 * conversation and the full-screen explorer. A second copy of "what can this
 * file be rendered as" is how the two come to disagree about an `.mp4` -- and
 * the last time this logic was wrong, it printed the container's bytes as
 * text.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { FileTypeIcon } from '@/components/documents/file-type-icon';
import { getDocumentPreviewType } from '@/components/documents/preview-renderers';
import { Button } from '@/components/ui/button';
import { Download } from '@/components/ui/icons';
import { useWorkspaceFile } from '@/lib/hooks/use-workspace-files';

export const formatSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

/**
 * Folders first, then files, each A-Z.
 *
 * The server returns one flat alphabetical run, so a directory sat wherever
 * its name fell -- `assets` between `PROJECT.md` and `audio` -- and the eye
 * had no way to separate the things you can open from the things you can go
 * into. Every file manager groups them, and this is why.
 *
 * `localeCompare` with `numeric` so `shot-2` precedes `shot-10`, and with
 * case folded so a capitalised name does not sort into its own block above
 * the lowercase ones.
 */
export function orderedEntries<T extends { name: string; kind: string }>(entries: readonly T[]): T[] {
    return [...entries].sort((a, b) => {
        const aDir = a.kind === 'directory';
        if (aDir !== (b.kind === 'directory')) return aDir ? -1 : 1;
        return a.name.localeCompare(b.name, undefined, {
            numeric: true,
            sensitivity: 'base',
        });
    });
}

/**
 * What the pane can actually put on the screen for this file.
 *
 * The pane used to ask one question -- "is it an image?" -- and send
 * everything else down the text branch. So opening an MP4 decoded the
 * container as UTF-8 and printed it: pages of replacement characters with
 * `ftypisom` legible at the top. A PDF and a .docx did the same thing. The
 * fix is to decide per file what can be *shown*, and to let everything else
 * be a file you download rather than a file rendered wrongly.
 */
type PreviewKind = 'image' | 'video' | 'audio' | 'pdf' | 'text' | 'opaque';

// Only the containers a browser will actually play. `mov` and `flac` are in
// because Safari plays both and the `onError` fallback catches the browsers
// that do not; `avi`, `mkv` and `wmv` are out because essentially nothing
// plays them, and offering a player that always fails is worse than offering
// the download straight away.
const VIDEO_EXTENSIONS = new Set(['mp4', 'm4v', 'mov', 'webm', 'ogv']);
const AUDIO_EXTENSIONS = new Set(['mp3', 'm4a', 'aac', 'wav', 'ogg', 'oga', 'flac', 'opus']);

export function previewKindOf(path: string): PreviewKind {
    const previewType = getDocumentPreviewType(path);
    if (previewType === 'image') return 'image';
    if (previewType === 'pdf') return 'pdf';
    const name = path.slice(path.lastIndexOf('/') + 1).toLowerCase();
    const dot = name.lastIndexOf('.');
    const extension = dot >= 0 ? name.slice(dot + 1) : '';
    if (VIDEO_EXTENSIONS.has(extension)) return 'video';
    if (AUDIO_EXTENSIONS.has(extension)) return 'audio';
    // `office` lands here with `unsupported`: a .docx is a zip, and this pane
    // has no renderer for one. The document viewer does; the sandbox pane is
    // deliberately the smaller surface.
    if (previewType === 'office' || previewType === 'unsupported') return 'opaque';
    return 'text';
}

type MediaNode = HTMLImageElement | HTMLVideoElement | HTMLAudioElement | HTMLIFrameElement;

/**
 * A file shown as itself: a picture, a player, or an embedded PDF.
 *
 * The `blob:` URL is attached to the node from an effect rather than passed
 * as a `src` prop, for two reasons. It keeps the URL's life tied to the
 * element's: StrictMode mounts as setup -> cleanup -> setup, so a URL made
 * during render is revoked by the first cleanup and never replaced, which is
 * the same trap `DownloadLink` documents. And it is what the effect rule
 * asks for -- the DOM is the external system here, so the effect writes to
 * it instead of round-tripping through React state.
 *
 * Mount this keyed on the path. `failed` is then reset by the remount rather
 * than by an effect, and a format this browser cannot decode falls back to
 * the same card an archive gets.
 */
export function MediaPreview({
    kind,
    blob,
    path,
    sizeBytes,
}: {
    kind: PreviewKind;
    blob: Blob;
    path: string;
    sizeBytes: number;
}) {
    const node = useRef<MediaNode | null>(null);
    const [failed, setFailed] = useState(false);
    const showable = kind !== 'opaque' && kind !== 'text' && !failed;

    useEffect(() => {
        const element = node.current;
        if (!element) return;
        const url = URL.createObjectURL(blob);
        element.setAttribute('src', url);
        return () => URL.revokeObjectURL(url);
    }, [blob, showable]);

    if (!showable) return <FileFacts path={path} sizeBytes={sizeBytes} />;

    const onError = () => setFailed(true);
    if (kind === 'image') {
        return (
            // eslint-disable-next-line @next/next/no-img-element
            <img
                ref={node as React.RefObject<HTMLImageElement>}
                alt={path}
                className="max-w-full rounded-md"
                onError={onError}
            />
        );
    }
    if (kind === 'video') {
        return (
            <video
                ref={node as React.RefObject<HTMLVideoElement>}
                controls
                className="max-w-full rounded-md"
                onError={onError}
            >
                <track kind="captions" />
            </video>
        );
    }
    if (kind === 'audio') {
        return (
            <audio
                ref={node as React.RefObject<HTMLAudioElement>}
                controls
                className="w-full"
                onError={onError}
            />
        );
    }
    return (
        <iframe
            ref={node as React.RefObject<HTMLIFrameElement>}
            title={path}
            className="h-[60vh] w-full rounded-md border-0"
        />
    );
}

/** Name, type and size, for a file the pane is not going to render. */
export function FileFacts({ path, sizeBytes }: { path: string; sizeBytes: number }) {
    const name = path.slice(path.lastIndexOf('/') + 1);
    return (
        <div className="flex items-center gap-3">
            <FileTypeIcon filename={name} size="lg" />
            <div className="min-w-0">
                <p className="truncate text-sm text-[var(--text-primary)]">{name}</p>
                <p className="text-xs text-[var(--text-tertiary)]">
                    {formatSize(sizeBytes)} · no preview for this kind of file
                </p>
            </div>
        </div>
    );
}

/**
 * Save a workspace file to the reader's own machine.
 *
 * The blob is already here — the hook fetched it to decide whether it could be
 * shown — so this costs nothing extra and is the only way out for the files the
 * pane refuses to render: an archive, a binary, anything past the text ceiling.
 * Without it those files could be listed and never opened.
 */
export function DownloadLink({ blob, path }: { blob: Blob; path: string }) {
    // The object URL is minted when the button is pressed, not when the
    // component renders.
    //
    // Rendering it was the bug, and it failed silently. The URL was made in a
    // `useMemo` and revoked in an effect cleanup; React's StrictMode runs a
    // mount as setup -> cleanup -> setup, so the cleanup revoked it while the
    // memo -- whose dependency had not changed -- never made another. The
    // anchor was left pointing at a revoked `blob:` URL, and a revoked one
    // does nothing when clicked: no download, no error, nothing in the
    // console. An `<img src>` built the same way survives, because it
    // resolves during commit rather than on a click minutes later, which is
    // why the same shape works elsewhere in the app.
    //
    // Minting on click sidesteps the lifecycle entirely, and is what the
    // document viewer already does.
    const save = useCallback(() => {
        const href = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = href;
        link.download = path.slice(path.lastIndexOf('/') + 1);
        document.body.append(link);
        link.click();
        link.remove();
        // Revoked on a later turn of the loop: Safari and Firefox abandon the
        // download if the URL dies in the same tick as the click.
        setTimeout(() => URL.revokeObjectURL(href), 0);
    }, [blob, path]);

    return (
        <Button variant="quiet" size="xs" onClick={save}>
            <Download className="size-3.5" />
            Download
        </Button>
    );
}

export function FileBody({ path, sizeBytes }: { path: string; sizeBytes: number }) {
    const kind = previewKindOf(path);
    // The size comes from the listing, so a file past the server's 8 MiB
    // single-read ceiling is fetched in pieces rather than silently truncated.
    const { data, isPending, error } = useWorkspaceFile(
        path,
        kind !== 'text',
        sizeBytes,
    );

    if (isPending) {
        return <p className="p-4 text-sm text-[var(--text-tertiary)]">Reading…</p>;
    }
    if (error) {
        return (
            <p className="p-4 text-sm text-[var(--text-tertiary)]">
                This file could not be read. It may have been removed since the list was taken.
            </p>
        );
    }
    if (data?.tooLarge) {
        return (
            <div className="flex flex-col items-start gap-2 p-4">
                <p className="text-sm text-[var(--text-tertiary)]">
                    {formatSize(data.sizeBytes)} is too large to show here. Download it, ask
                    the agent to summarise it, or open the part you need.
                </p>
                <DownloadLink blob={data.blob} path={path} />
            </div>
        );
    }
    if (kind !== 'text' && data) {
        return (
            <div className="flex flex-col items-start gap-3 p-4">
                <MediaPreview
                    key={path}
                    kind={kind}
                    blob={data.blob}
                    path={path}
                    sizeBytes={data.sizeBytes}
                />
                <DownloadLink blob={data.blob} path={path} />
            </div>
        );
    }
    if (!data?.text) return null;

    return (
        <div className="flex min-h-0 flex-col">
            <pre className="overflow-x-auto p-4 text-xs leading-5 text-[var(--text-secondary)]">
                {data.text}
            </pre>
            <div className="px-4 pb-4">
                <DownloadLink blob={data.blob} path={path} />
            </div>
        </div>
    );
}

