'use client';

import { useEffect, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { File as FileIcon } from '@/components/ui/icons';

import { DocumentFrontmatter } from '@/components/documents/document-frontmatter';
import { MarkdownEditor } from '@/components/documents/markdown-editor';
import {
    getDocumentPreviewType,
    getOfficePreviewKind,
    isTextPreviewType,
    renderDocxPreview,
    renderPdfPreview,
    type DocumentPreviewType,
    type OfficePreviewKind,
    type PdfPreviewData,
} from '@/components/documents/preview-renderers';
import {
    buildDocxPreviewSrcDoc,
    buildHtmlPreviewDocument,
    buildHtmlPreviewSrcDoc,
} from '@/lib/files/html-preview';
import { joinFrontmatter, splitFrontmatter } from '@/lib/files/frontmatter';
import { getLemmaClient } from '@/lib/sdk/lemma-client';

/**
 * How a file looks, wherever it is being read.
 *
 * A document has one appearance in this product, and it is not a property of
 * the route that happens to be showing it. Split out of the viewer because the
 * share route had grown a second, poorer answer to the same question — every
 * text-ish file dumped into a `<pre>`, so a shared `.html` showed its own source
 * and a shared `.md` showed its asterisks — and the only durable fix is for both
 * readers to render through the same code.
 *
 * Read-only presentation lives here. Editing does not: the workspace viewer
 * passes `editable` and an `onContentChange`, and everything else about the two
 * is identical by construction.
 */

const noop = () => undefined;

export interface DocumentPreviewBodyProps {
    /** File name, used for alt text and iframe titles. */
    name: string;
    /** Full path — decides whether frontmatter reads as a skill manifest. */
    path: string;
    previewType: DocumentPreviewType;
    officeKind: OfficePreviewKind;
    /** The whole text file, frontmatter included. Empty for binary types. */
    content: string;
    /** Self-contained HTML, assets already inlined. */
    htmlSrcDoc: string | null;
    imageUrl: string | null;
    pdf: PdfPreviewData | null;
    docxSrcDoc: string;
    /** HTML only: show the markup rather than the rendered page. */
    showHtmlSource?: boolean;
    editable?: boolean;
    /** Receives the whole file — frontmatter edits and prose edits alike. */
    onContentChange?: (next: string) => void;
}

export function DocumentPreviewBody({
    name,
    path,
    previewType,
    officeKind,
    content,
    htmlSrcDoc,
    imageUrl,
    pdf,
    docxSrcDoc,
    showHtmlSource = false,
    editable = false,
    onContentChange,
}: DocumentPreviewBodyProps) {
    /**
     * Frontmatter never reaches the markdown editor. A `---` fence renders as a
     * rule and a setext heading, and the editor would then write that mangled
     * shape back — quietly stripping the contract a SKILL.md depends on. The
     * block is held aside here and re-attached to whatever the editor emits.
     */
    const frontmatter = useMemo(
        () => (previewType === 'markdown' ? splitFrontmatter(content) : null),
        [content, previewType],
    );
    const markdownBody = frontmatter ? frontmatter.body : content;
    const frontmatterRaw = frontmatter?.raw ?? null;

    if (previewType === 'markdown') {
        return (
            // One column, one left edge. The frontmatter and the prose used to
            // sit in boxes of different widths, which is what made a plain
            // document look crooked.
            <div className="document-page px-2 py-5 sm:px-4">
                <DocumentFrontmatter
                    content={content}
                    path={path}
                    editable={editable}
                    onChange={onContentChange ?? noop}
                />
                <MarkdownEditor
                    content={markdownBody}
                    onChange={editable && onContentChange
                        ? (body) => onContentChange(joinFrontmatter(frontmatterRaw, body))
                        : noop}
                    editable={editable}
                    className="min-h-[70vh]"
                    editorClassName="min-h-[70vh]"
                    readableProse
                />
            </div>
        );
    }

    // An empty body is a real outcome — a zero-byte file, or a read that came
    // back with nothing — and rendering it as a blank frame says nothing at all.
    // The blank was indistinguishable from a broken preview, which is exactly
    // how long it took to find one.
    if (isTextPreviewType(previewType) && content.length === 0) {
        return <PreviewNotice>This file is empty.</PreviewNotice>;
    }

    if (previewType === 'html' && !showHtmlSource) {
        const htmlDocument = htmlSrcDoc || buildHtmlPreviewSrcDoc(content);
        return (
            // Keyed, so a document that arrives in two passes — the raw file,
            // then the same file with its assets inlined a moment later — gets a
            // fresh frame for the second pass instead of having `srcdoc`
            // rewritten underneath it.
            //
            // Rewriting the attribute re-navigates a frame that is usually still
            // loading the first document, and a frame that loses that race stays
            // blank for good: the attribute already holds the right markup, so
            // no later render touches it again. That is the preview that shows
            // up only once something forces a whole-document style recalculation
            // — switching theme, which `disableTransitionOnChange` does by
            // design.
            //
            // Length is the discriminator because the alternative is hashing a
            // document that runs to megabytes on every render. Inlining an asset
            // only ever grows the file, so the two passes differ in length
            // whenever they differ at all.
            <iframe
                key={`html-${htmlDocument.length}`}
                title={name}
                srcDoc={htmlDocument}
                sandbox="allow-scripts allow-forms allow-popups allow-modals allow-downloads allow-top-navigation-by-user-activation"
                className="embedded-canvas block h-full min-h-[78vh] w-full border-0"
                referrerPolicy="strict-origin-when-cross-origin"
            />
        );
    }

    if (previewType === 'json' || previewType === 'html' || previewType === 'code') {
        return (
            <textarea
                className="document-viewer-source-field h-[78vh] w-full resize-none rounded-md border border-[color:var(--field-border)] bg-[var(--field-bg)] p-3 font-mono text-xs leading-5 text-[var(--text-secondary)] focus:outline-none"
                value={content}
                onChange={(event) => {
                    if (editable) onContentChange?.(event.target.value);
                }}
                readOnly={!editable}
                spellCheck={false}
            />
        );
    }

    if (previewType === 'image') {
        return imageUrl ? (
            <div className="flex min-h-[78vh] items-start justify-center overflow-auto rounded-md bg-[var(--row-bg)] p-4">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                    src={imageUrl}
                    alt={name}
                    className="max-h-[78vh] max-w-full object-contain"
                />
            </div>
        ) : (
            <PreviewNotice>Could not render image preview. Use Download.</PreviewNotice>
        );
    }

    if (previewType === 'pdf') {
        return pdf && pdf.pages.length > 0 ? (
            // A page is a sheet, not a card. It arrives as a white rectangle
            // with its own edges already in it, so the border, the radius and
            // the inset that used to frame each one were chrome drawn around
            // chrome — and a long PDF repeated all three down the whole scroll.
            // A soft drop shadow is the whole job: it keeps two consecutive
            // sheets from reading as one continuous page, and says nothing else.
            <div className="flex flex-col items-center gap-4">
                {pdf.pages.map((page, index) => (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                        key={`pdf-page-${index + 1}`}
                        src={page.src}
                        alt={`PDF page ${index + 1}`}
                        width={page.displayWidth}
                        height={page.displayHeight}
                        className="h-auto max-w-full shadow-[var(--shadow-md)]"
                    />
                ))}

                {pdf.truncated && (
                    <PreviewNotice>
                        Showing first {pdf.pages.length} of {pdf.totalPages} pages.
                    </PreviewNotice>
                )}
            </div>
        ) : (
            <PreviewNotice>Could not render PDF preview. Use Download.</PreviewNotice>
        );
    }

    if (previewType === 'office') {
        return officeKind === 'docx' && docxSrcDoc ? (
            <iframe
                title={name}
                srcDoc={docxSrcDoc}
                sandbox=""
                className="embedded-canvas block h-full min-h-[78vh] w-full rounded-md border border-[color:var(--card-border)]"
            />
        ) : (
            <PreviewNotice>
                This office file type is not previewable here yet. Use Download.
            </PreviewNotice>
        );
    }

    return (
        <div className="surface-panel-muted px-3 py-4 text-xs text-[var(--text-tertiary)]">
            <div className="flex items-center gap-2">
                <FileIcon className="h-4 w-4" />
                Preview is not available for this file type. Use Download.
            </div>
        </div>
    );
}

function PreviewNotice({ children }: { children: React.ReactNode }) {
    return (
        <div className="surface-panel-muted px-3 py-2 text-xs text-[var(--text-tertiary)]">
            {children}
        </div>
    );
}

export interface DocumentPreviewData {
    previewType: DocumentPreviewType;
    officeKind: OfficePreviewKind;
    content: string;
    htmlSrcDoc: string | null;
    imageUrl: string | null;
    pdf: PdfPreviewData | null;
    docxSrcDoc: string;
    blob: Blob | null;
    isLoading: boolean;
    isError: boolean;
}

/**
 * Everything `DocumentPreviewBody` needs, fetched and derived.
 *
 * For readers who hold nothing but a path — the share route. The workspace
 * viewer keeps its own loading, because there the same bytes are also the
 * edit buffer and the autosave baseline.
 */
export function useDocumentPreview({
    podId,
    path,
    name,
    enabled = true,
}: {
    podId: string;
    path: string;
    name?: string | null;
    enabled?: boolean;
}): DocumentPreviewData {
    const previewType = getDocumentPreviewType(name || path);
    const officeKind = getOfficePreviewKind(name || path);
    const isText = isTextPreviewType(previewType);

    const { data, isPending, isError } = useQuery({
        queryKey: ['document-preview', podId, path],
        queryFn: async () => {
            const blob = await getLemmaClient(podId).files.download(path);
            return { blob, text: isText ? await blob.text() : '' };
        },
        enabled: enabled && Boolean(path),
        retry: false,
    });

    const content = data?.text ?? '';
    const blob = data?.blob ?? null;

    // Assets are followed against the reader's own permissions, so one this
    // reader may not open resolves to nothing and the page renders without it.
    const htmlQuery = useQuery({
        queryKey: ['document-preview-html', podId, path, content.length],
        queryFn: () => buildHtmlPreviewDocument({
            contentHtml: content,
            documentPath: path,
            loadAsset: (assetPath) => getLemmaClient(podId).files.download(assetPath),
        }),
        enabled: enabled && previewType === 'html' && content.length > 0,
        retry: false,
    });

    const pdfQuery = useQuery({
        queryKey: ['document-preview-pdf', podId, path, blob?.size ?? 0],
        queryFn: () => renderPdfPreview(blob as Blob),
        enabled: enabled && previewType === 'pdf' && Boolean(blob),
        retry: false,
    });

    const docxQuery = useQuery({
        queryKey: ['document-preview-docx', podId, path, blob?.size ?? 0],
        queryFn: () => renderDocxPreview(blob as Blob),
        enabled: enabled && previewType === 'office' && officeKind === 'docx' && Boolean(blob),
        retry: false,
    });

    // Derived rather than stored, so the effect below is only responsible for
    // revoking it — object URLs leak for the lifetime of the document otherwise.
    const imageUrl = useMemo(
        () => (previewType === 'image' && blob ? URL.createObjectURL(blob) : null),
        [blob, previewType],
    );

    useEffect(() => {
        if (!imageUrl) return;
        return () => URL.revokeObjectURL(imageUrl);
    }, [imageUrl]);

    // A file is not "loaded" until the thing the reader will actually look at
    // has been rendered, or the page flashes an empty frame first. Keyed on the
    // render actually being in flight rather than on its result being absent —
    // a PDF that fails to rasterise has no result either, and waiting for one
    // that is never coming would hold the skeleton up forever instead of
    // showing the fallback that offers Download.
    //
    // HTML counts for the same reason the other two do, and was missing: the
    // frame used to mount on the raw file and then swap to the inlined one, so
    // every shared page flashed once with its stylesheet and images unresolved,
    // and the swap itself is the navigation a frame can lose. Waiting means the
    // iframe mounts once, on the document the reader is meant to see.
    const isRenderPending = pdfQuery.isLoading || docxQuery.isLoading || htmlQuery.isLoading;

    return {
        previewType,
        officeKind,
        content,
        htmlSrcDoc: htmlQuery.data?.srcDoc ?? null,
        imageUrl,
        pdf: pdfQuery.data ?? null,
        docxSrcDoc: docxQuery.data?.html ? buildDocxPreviewSrcDoc(docxQuery.data.html) : '',
        blob,
        isLoading: (isPending && enabled && Boolean(path)) || isRenderPending,
        isError,
    };
}
