'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLayoutEffect } from 'react';
import type { ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
    ArrowLeft,
    Code2,
    CopyCheck,
    Download,
    Eye,
    File as FileIcon,
    FileText,
    Maximize2,
    Minimize2,
    Printer,
    Save,
    Share2,
} from '@/components/ui/icons';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { DropdownMenuItem, DropdownMenuSeparator } from '@/components/ui/dropdown-menu';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { DestructiveConfirmationDialog } from '@/components/shared/destructive-confirmation-dialog';
import { DestructiveResourceActionItem, ResourceActionsMenu } from '@/components/shared/resource-actions-menu';
import { ResourceShareButton, ResourceVisibilityBadge, type ResourceVisibilityValue } from '@/components/shared/resource-visibility';
import { usePodTopbar } from '@/components/pod/pod-topbar-context';
import { resourceAllows } from '@/lib/authz/resource-actions';
import { isPersonalPath } from '@/lib/files/doc-sections';
import { FileIndexStatusBadge } from '@/components/documents/file-index-status-badge';
import {
    MarkdownAttachmentControl,
    canAttachDocumentMarkdown,
    usesUserMarkdown,
} from '@/components/documents/markdown-attachment-control';
import {
    AUTOSAVE_DELAY_MS,
    describeAutosaveStatus,
    isAutosavedPreviewType,
    shouldShowSaveButton,
    type DocumentSaveState,
} from '@/components/documents/document-save-state';
import { useDatastoreFile, useDeleteDatastoreFile } from '@/lib/hooks/use-datastores';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import {
    canPrintDocument,
    getDocumentPreviewType,
    getOfficePreviewKind,
    printFileName,
    renderDocxPreview,
    renderPdfPreview,
} from '@/components/documents/preview-renderers';
import { DocumentFrontmatter } from '@/components/documents/document-frontmatter';
import { joinFrontmatter, splitFrontmatter } from '@/lib/files/frontmatter';
import { MarkdownEditor } from './markdown-editor';
import { DocumentBodySkeleton, DocumentSkeleton } from '@/components/documents/document-skeleton';
import { cn } from '@/lib/utils';

interface DocumentViewerProps {
    podId: string;
    datastoreName: string;
    fileId: string;
    onClose?: () => void;
    onDeleted?: () => void;
    backLabel?: string;
    contextLabel?: ReactNode;
    extraActions?: ReactNode;
    headerMode?: 'inline' | 'topbar';
    topbarBackHref?: string;
    topbarBackLabel?: string;
    canWrite?: boolean;
    canDelete?: boolean;
}

type TextViewMode = 'preview' | 'source';

type HtmlPreviewDocument = {
    srcDoc: string;
};

function inferTextMimeType(filename: string): string {
    const lower = filename.toLowerCase();
    if (lower.endsWith('.md') || lower.endsWith('.markdown')) return 'text/markdown';
    if (lower.endsWith('.json')) return 'application/json';
    if (lower.endsWith('.html') || lower.endsWith('.htm')) return 'text/html';
    if (lower.endsWith('.css')) return 'text/css';
    if (lower.endsWith('.csv')) return 'text/csv';
    if (lower.endsWith('.xml')) return 'application/xml';
    return 'text/plain';
}

function buildDocxPreviewSrcDoc(contentHtml: string): string {
    return `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <style>
    :root { color-scheme: light; }
    * { box-sizing: border-box; }
    html, body { margin: 0; padding: 0; background: rgb(255 255 255); color: rgb(15 23 42); }
    body {
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
      line-height: 1.55;
      overflow-x: hidden;
    }
    body :where(*) { max-width: 100%; }
    body :where(p, li, td, th, span) { overflow-wrap: anywhere; word-break: break-word; }
    img { max-width: 100%; height: auto; }
    table { border-collapse: collapse; width: 100%; margin: 12px 0; }
    td, th { border: 1px solid rgb(229 231 235); padding: 6px 8px; vertical-align: top; }
    p { margin: 0 0 10px; }
    h1, h2, h3, h4, h5, h6 { margin: 14px 0 10px; }
  </style>
</head>
<body>${contentHtml}</body>
</html>`;
}

function looksLikeHtmlDocument(contentHtml: string): boolean {
    return /^\s*<!doctype\s+html/i.test(contentHtml) || /^\s*<html[\s>]/i.test(contentHtml);
}

function buildHtmlPreviewSrcDoc(contentHtml: string): string {
    if (looksLikeHtmlDocument(contentHtml)) {
        return contentHtml;
    }

    return `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
</head>
<body>${contentHtml}</body>
</html>`;
}

function getDirectoryPath(filePath: string): string {
    const normalized = filePath.replace(/\\/g, '/');
    const lastSlashIndex = normalized.lastIndexOf('/');
    if (lastSlashIndex <= 0) return '/';
    return normalized.slice(0, lastSlashIndex);
}

function normalizePathSegments(path: string): string {
    const startsWithSlash = path.startsWith('/');
    const segments = path.split('/').filter((segment) => segment.length > 0);
    const normalizedSegments: string[] = [];

    segments.forEach((segment) => {
        if (segment === '.') return;
        if (segment === '..') {
            normalizedSegments.pop();
            return;
        }
        normalizedSegments.push(segment);
    });

    return `${startsWithSlash ? '/' : ''}${normalizedSegments.join('/')}` || '/';
}

function resolveHtmlAssetPath(baseFilePath: string, rawUrl: string): string | null {
    const value = rawUrl.trim();
    if (!value || value.startsWith('#')) return null;
    if (/^(?:[a-z][a-z0-9+.-]*:|\/\/)/i.test(value)) return null;

    const [withoutHash] = value.split('#');
    const [pathname] = withoutHash.split('?');
    if (!pathname) return null;
    const decodedPathname = safeDecodeUrlPath(pathname);

    if (decodedPathname.startsWith('/')) {
        return normalizePathSegments(decodedPathname);
    }

    return normalizePathSegments(`${getDirectoryPath(baseFilePath)}/${decodedPathname}`);
}

function safeDecodeUrlPath(pathname: string): string {
    try {
        return decodeURIComponent(pathname);
    } catch {
        return pathname;
    }
}

function blobToDataUrl(blob: Blob): Promise<string> {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
            if (typeof reader.result === 'string') resolve(reader.result);
            else reject(new Error('Failed to read asset'));
        };
        reader.onerror = () => reject(reader.error || new Error('Failed to read asset'));
        reader.readAsDataURL(blob);
    });
}

async function buildHtmlPreviewDocument({
    contentHtml,
    documentPath,
    loadAsset,
}: {
    contentHtml: string;
    documentPath: string;
    loadAsset: (path: string) => Promise<Blob>;
}): Promise<HtmlPreviewDocument> {
    const html = buildHtmlPreviewSrcDoc(contentHtml);
    const parser = new DOMParser();
    const parsed = parser.parseFromString(html, 'text/html');
    const assetCache = new Map<string, Promise<string | null>>();

    const resolveAssetUrl = (rawUrl: string, basePath = documentPath): Promise<string | null> => {
        const assetPath = resolveHtmlAssetPath(basePath, rawUrl);
        if (!assetPath) return Promise.resolve(null);

        const cached = assetCache.get(assetPath);
        if (cached) return cached;

        const next = loadAsset(assetPath)
            .then((blob) => blobToDataUrl(blob))
            .catch(() => null);
        assetCache.set(assetPath, next);
        return next;
    };

    const resolveStylesheetUrl = (rawUrl: string): Promise<string | null> => {
        const stylesheetPath = resolveHtmlAssetPath(documentPath, rawUrl);
        if (!stylesheetPath) return Promise.resolve(null);

        const cacheKey = `css:${stylesheetPath}`;
        const cached = assetCache.get(cacheKey);
        if (cached) return cached;

        const next = loadAsset(stylesheetPath)
            .then(async (blob) => {
                const cssText = await blob.text();
                const rewrittenCss = await rewriteCssUrls(cssText, stylesheetPath, resolveAssetUrl);
                return blobToDataUrl(new Blob([rewrittenCss], { type: blob.type || 'text/css' }));
            })
            .catch(() => resolveAssetUrl(rawUrl));
        assetCache.set(cacheKey, next);
        return next;
    };

    const rewriteAttribute = async (
        selector: string,
        attribute: string,
        resolveUrl: (rawUrl: string) => Promise<string | null> = resolveAssetUrl
    ) => {
        const elements = Array.from(parsed.querySelectorAll<HTMLElement>(selector));
        await Promise.all(elements.map(async (element) => {
            const rawValue = element.getAttribute(attribute);
            if (!rawValue) return;
            const nextValue = await resolveUrl(rawValue);
            if (nextValue) element.setAttribute(attribute, nextValue);
        }));
    };

    const rewriteSrcset = async () => {
        const elements = Array.from(parsed.querySelectorAll<HTMLElement>('[srcset]'));
        await Promise.all(elements.map(async (element) => {
            const rawValue = element.getAttribute('srcset');
            if (!rawValue) return;
            const nextValue = await rewriteSrcsetValue(rawValue, resolveAssetUrl);
            if (nextValue) element.setAttribute('srcset', nextValue);
        }));
    };

    await Promise.all([
        rewriteAttribute('img[src], script[src], iframe[src], source[src], video[src], audio[src], embed[src]', 'src'),
        rewriteAttribute('object[data]', 'data'),
        rewriteAttribute('link[rel~="stylesheet"][href]', 'href', resolveStylesheetUrl),
        rewriteAttribute('link[rel~="icon"][href], link[rel~="preload"][href], link[rel~="modulepreload"][href]', 'href'),
        rewriteAttribute('[poster]', 'poster'),
        rewriteSrcset(),
    ]);

    return {
        srcDoc: `<!doctype html>\n${parsed.documentElement.outerHTML}`,
    };
}

async function rewriteCssUrls(
    cssText: string,
    stylesheetPath: string,
    resolveAssetUrl: (rawUrl: string, basePath?: string) => Promise<string | null>
): Promise<string> {
    const urlPattern = /url\(\s*(["']?)([^"')]+)\1\s*\)/gi;
    const matches = Array.from(cssText.matchAll(urlPattern));
    if (matches.length === 0) return cssText;

    const replacements = await Promise.all(matches.map(async (match) => {
        const rawUrl = match[2]?.trim();
        if (!rawUrl) return null;
        const objectUrl = await resolveAssetUrl(rawUrl, stylesheetPath);
        return objectUrl ? { from: match[0], to: `url("${objectUrl}")` } : null;
    }));

    return replacements.reduce((nextCss, replacement) => (
        replacement ? nextCss.replace(replacement.from, replacement.to) : nextCss
    ), cssText);
}

async function rewriteSrcsetValue(
    srcset: string,
    resolveAssetUrl: (rawUrl: string) => Promise<string | null>
): Promise<string | null> {
    const candidates = srcset.split(',').map((candidate) => candidate.trim()).filter(Boolean);
    if (candidates.length === 0) return null;

    const rewrittenCandidates = await Promise.all(candidates.map(async (candidate) => {
        const [rawUrl, ...descriptorParts] = candidate.split(/\s+/);
        if (!rawUrl) return candidate;
        const objectUrl = await resolveAssetUrl(rawUrl);
        return [objectUrl || rawUrl, ...descriptorParts].join(' ');
    }));

    return rewrittenCandidates.join(', ');
}

export function DocumentViewer({
    podId,
    datastoreName,
    fileId,
    onClose,
    onDeleted,
    backLabel = 'Back',
    contextLabel,
    extraActions,
    headerMode = 'inline',
    topbarBackHref,
    topbarBackLabel,
    canWrite = true,
    canDelete = true,
}: DocumentViewerProps) {
    const topbar = usePodTopbar();
    const queryClient = useQueryClient();
    const { data: doc, isLoading: isLoadingDoc } = useDatastoreFile(podId, datastoreName, fileId);
    const { mutate: deleteDocument, isPending: isDeleting } = useDeleteDatastoreFile();

    const [docContent, setDocContent] = useState<string>('');
    const [originalContent, setOriginalContent] = useState<string>('');
    const [fileBlob, setFileBlob] = useState<Blob | null>(null);
    const [imagePreviewUrl, setImagePreviewUrl] = useState<string | null>(null);
    const [htmlPreviewDocument, setHtmlPreviewDocument] = useState<HtmlPreviewDocument | null>(null);
    const [showDeleteDialog, setShowDeleteDialog] = useState(false);
    const [isLoadingContent, setIsLoadingContent] = useState(false);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [textViewMode, setTextViewMode] = useState<TextViewMode>('preview');
    const [isReading, setIsReading] = useState(false);
    const [isAttachmentOpen, setIsAttachmentOpen] = useState(false);
    const [saveState, setSaveState] = useState<DocumentSaveState>('idle');
    const viewerShellRef = useRef<HTMLDivElement | null>(null);

    const documentPath = doc?.path || fileId;
    const previewType = getDocumentPreviewType(doc?.name || documentPath);
    const officePreviewKind = getOfficePreviewKind(doc?.name || documentPath);
    const canWriteDocument = resourceAllows(doc, 'folder.write', canWrite);
    const canDeleteDocument = resourceAllows(doc, 'folder.delete', canDelete);

    const isTextEditable = previewType === 'markdown'
        || previewType === 'json'
        || previewType === 'html'
        || previewType === 'code';

    /**
     * Frontmatter never reaches the markdown editor. A `---` fence renders as a
     * rule and a setext heading, and the editor would then write that mangled
     * shape back — quietly stripping the contract a SKILL.md depends on. The
     * block is held aside here and re-attached to whatever the editor emits.
     */
    const markdownFrontmatter = useMemo(
        () => (previewType === 'markdown' ? splitFrontmatter(docContent) : null),
        [docContent, previewType]
    );
    const markdownBody = markdownFrontmatter ? markdownFrontmatter.body : docContent;
    const markdownFrontmatterRaw = markdownFrontmatter?.raw ?? null;
    const handleMarkdownBodyChange = useCallback(
        (body: string) => {
            setDocContent(joinFrontmatter(markdownFrontmatterRaw, body));
        },
        [markdownFrontmatterRaw]
    );

    useEffect(() => {
        if (!doc) return;

        let cancelled = false;
        setIsLoadingContent(true);
        setLoadError(null);
        setDocContent('');
        setOriginalContent('');
        setFileBlob(null);
        setImagePreviewUrl(null);
        setHtmlPreviewDocument(null);
        setTextViewMode(previewType === 'html' ? 'preview' : 'source');
        // "Saved" belongs to the document you saved, not to the next one.
        setSaveState('idle');

        const load = async () => {
            try {
                const blob = await getLemmaClient(podId).files.download(documentPath);
                if (cancelled) return;

                if (isTextEditable) {
                    const text = await blob.text();
                    if (cancelled) return;
                    setDocContent(text);
                    setOriginalContent(text);
                } else {
                    setFileBlob(blob);
                }
            } catch (error) {
                if (cancelled) return;
                const message = error instanceof Error ? error.message : 'Failed to load file';
                setLoadError(message);
            } finally {
                if (!cancelled) setIsLoadingContent(false);
            }
        };

        void load();

        return () => {
            cancelled = true;
        };
    }, [doc, documentPath, isTextEditable, podId, previewType]);

    useEffect(() => {
        if (previewType !== 'image' || !fileBlob) {
            setImagePreviewUrl(null);
            return;
        }

        const nextUrl = URL.createObjectURL(fileBlob);
        setImagePreviewUrl(nextUrl);

        return () => {
            URL.revokeObjectURL(nextUrl);
        };
    }, [fileBlob, previewType]);

    useEffect(() => {
        if (previewType !== 'html' || textViewMode !== 'preview' || !docContent) {
            setHtmlPreviewDocument(null);
            return;
        }

        let cancelled = false;
        setHtmlPreviewDocument({ srcDoc: buildHtmlPreviewSrcDoc(docContent) });

        buildHtmlPreviewDocument({
            contentHtml: docContent,
            documentPath,
            loadAsset: (path) => getLemmaClient(podId).files.download(path),
        })
            .then((nextPreview) => {
                if (cancelled) return;
                setHtmlPreviewDocument(nextPreview);
            })
            .catch(() => {
                if (cancelled) return;
                setHtmlPreviewDocument({ srcDoc: buildHtmlPreviewSrcDoc(docContent) });
            });

        return () => {
            cancelled = true;
        };
    }, [docContent, documentPath, podId, previewType, textViewMode]);

    const {
        data: pdfPreview,
        isLoading: isLoadingPdfPreview,
        error: pdfPreviewError,
    } = useQuery({
        queryKey: ['datastore-file-pdf-preview', podId, documentPath, fileBlob?.size ?? 0],
        queryFn: () => renderPdfPreview(fileBlob as Blob),
        enabled: previewType === 'pdf' && !!fileBlob,
        staleTime: 0,
    });

    const {
        data: docxPreview,
        isLoading: isLoadingDocxPreview,
        error: docxPreviewError,
    } = useQuery({
        queryKey: ['datastore-file-docx-preview', podId, documentPath, fileBlob?.size ?? 0],
        queryFn: () => renderDocxPreview(fileBlob as Blob),
        enabled: previewType === 'office' && officePreviewKind === 'docx' && !!fileBlob,
        staleTime: 0,
    });

    const docxPreviewSrcDoc = useMemo(() => (
        docxPreview?.html ? buildDocxPreviewSrcDoc(docxPreview.html) : ''
    ), [docxPreview]);


    /**
     * Reading mode is in-app state; browser full screen is a bonus on top.
     *
     * It used to be the other way round, and that is why the docs section
     * shipped a full screen with no chrome in it: those controls render into the
     * pod topbar, which lives outside the element being promoted, so the reader
     * got a document with no header, no save, and no exit but Esc. An overlay
     * this component owns behaves the same whether or not the surrounding frame
     * was ever granted full screen.
     */
    const exitReading = useCallback(() => {
        setIsReading(false);
        if (typeof document !== 'undefined' && document.fullscreenElement) {
            void document.exitFullscreen().catch(() => undefined);
        }
    }, []);

    const handleToggleReading = useCallback(() => {
        if (isReading) {
            exitReading();
            return;
        }

        setIsReading(true);
        // Best effort: the overlay is already covering the app, so a frame that
        // refuses full screen costs the browser chrome and nothing else.
        if (document.fullscreenEnabled) {
            void viewerShellRef.current?.requestFullscreen().catch(() => undefined);
        }
    }, [exitReading, isReading]);

    useEffect(() => {
        if (!isReading) return;

        // Esc is the reflex, and inside an in-app overlay it is not the
        // browser's to handle. When full screen *is* active the browser exits it
        // without ever dispatching the key, which the change listener catches.
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === 'Escape') exitReading();
        };
        const handleFullscreenChange = () => {
            if (!document.fullscreenElement) setIsReading(false);
        };

        document.addEventListener('keydown', handleKeyDown);
        document.addEventListener('fullscreenchange', handleFullscreenChange);
        return () => {
            document.removeEventListener('keydown', handleKeyDown);
            document.removeEventListener('fullscreenchange', handleFullscreenChange);
        };
    }, [exitReading, isReading]);

    /**
     * Print, which is also how you get a PDF: every desktop browser and iOS
     * offers "Save as PDF" from its own print dialog, so the page does the work
     * and the platform supplies the file. Nothing to render twice, nothing to
     * keep in sync with what the screen shows.
     */
    const handlePrint = useCallback(() => {
        // Said now, not after: the browser reports when its dialog closes but
        // never whether a file was written, so "Saved" would be a guess that is
        // wrong every time someone cancels. This acknowledges the click and
        // names the one choice in the dialog that produces a file.
        toast('Opening print — choose "Save as PDF" for a file');
        // A frame's delay so the toast paints before the modal dialog blocks.
        requestAnimationFrame(() => window.print());
    }, []);

    /**
     * Paper is white whatever the app is wearing.
     *
     * Browsers drop background colours when printing but keep text colours, so
     * printing a dark-themed document lays near-white ink on a white sheet and
     * hands you a blank-looking page. The theme class comes off for the length
     * of the print and goes back after — the light palette is already the one
     * that reads as ink.
     */
    const printedFileName = doc ? printFileName(doc.name) : '';

    useEffect(() => {
        const root = document.documentElement;
        let restoreDark = false;
        let previousTitle = '';

        const handleBeforePrint = () => {
            restoreDark = root.classList.contains('dark');
            if (restoreDark) root.classList.remove('dark');

            // The page title is where browsers get the default "Save as PDF"
            // filename, so while the dialog is open the tab is named after the
            // document rather than after the route it happens to be on.
            if (printedFileName) {
                previousTitle = document.title;
                document.title = printedFileName;
            }
        };
        const handleAfterPrint = () => {
            if (restoreDark) root.classList.add('dark');
            restoreDark = false;
            if (previousTitle) {
                document.title = previousTitle;
                previousTitle = '';
            }
        };

        window.addEventListener('beforeprint', handleBeforePrint);
        window.addEventListener('afterprint', handleAfterPrint);
        return () => {
            window.removeEventListener('beforeprint', handleBeforePrint);
            window.removeEventListener('afterprint', handleAfterPrint);
            // A print started and never finished must not leave the app light,
            // or the tab wearing a document's name after you have left it.
            handleAfterPrint();
        };
    }, [printedFileName]);

    const handleDownload = useCallback(async () => {
        if (!doc) return;
        try {
            const blob = await getLemmaClient(podId).files.download(documentPath);
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = doc.name;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(url);
        } catch {
            toast.error('Failed to download file');
        }
    }, [doc, documentPath, podId]);

    const handleDelete = useCallback(() => {
        if (!doc || !canDeleteDocument) return;

        deleteDocument(
            { podId, datastoreName, file_path: documentPath },
            {
                onSuccess: () => {
                    toast.success('File deleted');
                    setShowDeleteDialog(false);
                    onDeleted?.();
                    onClose?.();
                },
                onError: () => {
                    toast.error('Failed to delete file');
                },
            }
        );
    }, [canDeleteDocument, datastoreName, deleteDocument, doc, documentPath, onClose, onDeleted, podId]);

    /**
     * One write, however it was asked for.
     *
     * The run counter is what keeps a slow save from undoing a fast one: two
     * writes can be in flight when you keep typing through an autosave, and only
     * the last one may report what is now on disk.
     */
    const saveRunRef = useRef(0);
    const persist = useCallback(async (content: string) => {
        if (!doc || !isTextEditable || !canWriteDocument) return;

        const run = ++saveRunRef.current;
        setSaveState('saving');
        try {
            const blob = new Blob([content], { type: inferTextMimeType(doc.name) });
            await getLemmaClient(podId).files.update(documentPath, {
                file: blob,
                name: doc.name,
            });
            if (saveRunRef.current !== run) return;
            setOriginalContent(content);
            setSaveState('saved');
        } catch {
            if (saveRunRef.current !== run) return;
            setSaveState('error');
            throw new Error('save failed');
        }
    }, [canWriteDocument, doc, documentPath, isTextEditable, podId]);

    const handleSave = useCallback(async () => {
        try {
            await persist(docContent);
            toast.success('File saved');
        } catch {
            toast.error('Failed to save file');
        }
    }, [docContent, persist]);

    const hasUnsavedChanges = Boolean(doc && isTextEditable && docContent !== originalContent);
    const isAutosaving = canWriteDocument && isAutosavedPreviewType(previewType);

    /**
     * Prose saves itself once typing stops, the way an agent's prompt does.
     *
     * Only ever fires behind a real edit: the editor emits changes when it has
     * focus, so a document you opened and read is never rewritten — which
     * matters because the round-trip through the editor normalises the markdown
     * to its own dialect.
     */
    useEffect(() => {
        if (!isAutosaving || !hasUnsavedChanges) return;

        const timer = setTimeout(() => {
            void persist(docContent).catch(() => undefined);
        }, AUTOSAVE_DELAY_MS);
        return () => clearTimeout(timer);
    }, [docContent, hasUnsavedChanges, isAutosaving, persist]);

    /**
     * Closing the tab mid-pause must not cost the last sentence. Held in a ref
     * so the unmount effect below never re-runs on a keystroke.
     */
    const pendingSaveRef = useRef<(() => void) | null>(null);
    useEffect(() => {
        pendingSaveRef.current = isAutosaving && hasUnsavedChanges
            ? () => { void persist(docContent).catch(() => undefined); }
            : null;
    }, [docContent, hasUnsavedChanges, isAutosaving, persist]);

    useEffect(() => () => pendingSaveRef.current?.(), []);

    const autosaveStatus = isAutosaving
        ? describeAutosaveStatus({ state: saveState, hasUnsavedChanges })
        : null;
    const canToggleTextView = previewType === 'html';
    const isTextSourceMode = !canToggleTextView || textViewMode === 'source';
    const textModeToggleLabel = textViewMode === 'preview' ? 'Source' : 'Preview';
    const documentVisibility = doc?.visibility || 'POD';
    /**
     * Address the document by id rather than by the path in the address bar.
     * `/me/…` is an alias for whoever is reading, so a path-shaped link resolves
     * to the *recipient's* own file — a 404, or silently the wrong document when
     * the names happen to match.
     */
    const documentShareUrl = typeof window === 'undefined' || !doc?.id
        ? undefined
        : `${window.location.origin}${window.location.pathname}?fileId=${encodeURIComponent(doc.id)}`;

    const handleShareVisibilityChange = useCallback(async (visibility: ResourceVisibilityValue) => {
        if (!doc) return;
        await getLemmaClient(podId).files.update(documentPath, {
            visibility,
        });
        queryClient.invalidateQueries({ queryKey: ['datastore-files', podId, datastoreName] });
        queryClient.invalidateQueries({ queryKey: ['datastore-files', podId, datastoreName, documentPath] });
        toast.success('Sharing updated');
    }, [datastoreName, doc, documentPath, podId, queryClient]);

    const handleCopyContent = useCallback(async () => {
        if (!doc) return;
        try {
            if (isTextEditable) {
                await navigator.clipboard.writeText(docContent);
                toast.success('Content copied');
                return;
            }

            if (!fileBlob) {
                toast.error('Content is still loading');
                return;
            }

            if (typeof ClipboardItem === 'undefined' || !navigator.clipboard?.write) {
                toast.error('Copy content is not available for this file type');
                return;
            }

            await navigator.clipboard.write([
                new ClipboardItem({ [fileBlob.type || 'application/octet-stream']: fileBlob }),
            ]);
            toast.success('Content copied');
        } catch {
            toast.error('Could not copy content');
        }
    }, [doc, docContent, fileBlob, isTextEditable]);

    /**
     * Reading a document is the common case, so the header is not a toolbar.
     *
     * Inline: what changes the document's state (save, or the note that it saved
     * itself), what changes the view (reading mode, source), and who can see it.
     * Everything else — copy, download, the markdown override, delete — is a
     * verb you reach for occasionally and can afford to open a menu for.
     */
    const headerActions = useMemo(() => (
        <TooltipProvider>
            <div className="flex shrink-0 items-center gap-1">
            {extraActions}
            {autosaveStatus ? (
                <span
                    className={cn(
                        'px-1 text-xs',
                        autosaveStatus.tone === 'error'
                            ? 'text-[var(--state-error)]'
                            : 'text-[var(--text-tertiary)]'
                    )}
                    // Announced politely: it is a reassurance, not an
                    // interruption in the middle of a sentence.
                    role="status"
                    aria-live="polite"
                >
                    {autosaveStatus.label}
                </span>
            ) : null}
            {shouldShowSaveButton({ previewType, canWrite: canWriteDocument, hasUnsavedChanges }) && (
                <Button variant="primary"
                    size="sm"
                    className="h-8 gap-1.5 px-3 text-xs font-medium"
                    onClick={() => void handleSave()}
                >
                    <Save className="h-3.5 w-3.5" />
                    Save changes
                </Button>
            )}
            <Tooltip>
                <TooltipTrigger asChild>
                    <Button
                        type="button"
                        variant="quiet"
                        size="icon"
                        className="h-8 w-8 rounded"
                        onClick={handleToggleReading}
                        disabled={!doc}
                        aria-label="Read full screen"
                    >
                        <Maximize2 className="h-4 w-4" />
                    </Button>
                </TooltipTrigger>
                <TooltipContent>Read full screen</TooltipContent>
            </Tooltip>
            {/* Personal files promote instead of sharing: `/me` is an alias for
                whoever is reading, so they have no address that means the same
                thing to anyone else. The promote action arrives via
                `extraActions` from the space around this viewer. */}
            {canWriteDocument && !isPersonalPath(documentPath) ? (
                <ResourceShareButton
                    value={documentVisibility}
                    podId={podId}
                    resourceType="document"
                    resourceId={documentPath}
                    resourceLabel="files"
                    resourceName={doc?.name || documentPath}
                    shareUrl={documentShareUrl}
                    onChange={handleShareVisibilityChange}
                    disabled={!doc}
                    trigger={({ openShare, disabled }) => (
                        <Tooltip>
                            <TooltipTrigger asChild>
                                <Button
                                    type="button"
                                    variant="quiet"
                                    size="icon"
                                    className="h-8 w-8 rounded"
                                    onClick={openShare}
                                    disabled={disabled}
                                    aria-label="Share"
                                >
                                    <Share2 className="h-4 w-4" />
                                </Button>
                            </TooltipTrigger>
                            <TooltipContent>Share</TooltipContent>
                        </Tooltip>
                    )}
                />
            ) : null}
            {canToggleTextView ? (
                <Tooltip>
                    <TooltipTrigger asChild>
                        <Button
                            variant="quiet"
                            size="icon"
                            className="h-8 w-8 rounded"
                            onClick={() => setTextViewMode((current) => current === 'preview' ? 'source' : 'preview')}
                            aria-label={textModeToggleLabel}
                        >
                            {textViewMode === 'preview' ? <Code2 className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                        </Button>
                    </TooltipTrigger>
                    <TooltipContent>{textModeToggleLabel}</TooltipContent>
                </Tooltip>
            ) : null}
            <ResourceActionsMenu ariaLabel="More document actions">
                <DropdownMenuItem
                    disabled={!doc || isLoadingContent}
                    onSelect={() => void handleCopyContent()}
                >
                    <CopyCheck className="mr-2 h-4 w-4" />
                    Copy content
                </DropdownMenuItem>
                <DropdownMenuItem disabled={!doc} onSelect={() => void handleDownload()}>
                    <Download className="mr-2 h-4 w-4" />
                    Download
                </DropdownMenuItem>
                {/* Named for what it does, not for the file it can produce. The
                    dialog this opens is the browser's, and "Save as PDF" is one
                    of the destinations in it — so calling this "Export as PDF"
                    would promise a file landing in the pod. It does not. */}
                {canPrintDocument(previewType) ? (
                    <DropdownMenuItem
                        disabled={!doc || isLoadingContent}
                        onSelect={handlePrint}
                    >
                        <Printer className="mr-2 h-4 w-4" />
                        Print or save as PDF
                    </DropdownMenuItem>
                ) : null}
                {canWriteDocument && canAttachDocumentMarkdown(previewType) ? (
                    <DropdownMenuItem disabled={!doc} onSelect={() => setIsAttachmentOpen(true)}>
                        <FileText className="mr-2 h-4 w-4" />
                        {usesUserMarkdown(doc?.metadata) ? 'Using your markdown' : 'Attach your markdown'}
                    </DropdownMenuItem>
                ) : null}
                {canDeleteDocument ? (
                    <>
                        <DropdownMenuSeparator />
                        <DestructiveResourceActionItem
                            disabled={!doc || isDeleting}
                            onSelect={() => setShowDeleteDialog(true)}
                        >
                            Delete file
                        </DestructiveResourceActionItem>
                    </>
                ) : null}
            </ResourceActionsMenu>
            </div>
        </TooltipProvider>
    ), [
        autosaveStatus,
        canDeleteDocument,
        canToggleTextView,
        canWriteDocument,
        doc,
        documentPath,
        documentShareUrl,
        documentVisibility,
        extraActions,
        previewType,
        handleCopyContent,
        handleDownload,
        handlePrint,
        handleSave,
        handleShareVisibilityChange,
        handleToggleReading,
        hasUnsavedChanges,
        isDeleting,
        isLoadingContent,
        podId,
        textModeToggleLabel,
        textViewMode,
    ]);

    useLayoutEffect(() => {
        if (headerMode !== 'topbar') return;

        topbar?.setTopbar({
            title: doc?.name || documentPath,
            icon: <FileIcon className="h-4 w-4" />,
            meta: doc ? <FileIndexStatusBadge file={doc} /> : undefined,
            backHref: topbarBackHref,
            backLabel: topbarBackLabel || backLabel,
            actions: headerActions,
        });

        return () => topbar?.setTopbar({});
    }, [backLabel, doc, doc?.name, documentPath, headerActions, headerMode, topbar, topbarBackHref, topbarBackLabel]);

    // Same shell the dynamic-import fallback drew a moment ago, and the same one
    // the settled viewer draws next — so this is a continuation of one load, not
    // a second screen replacing the first.
    if (isLoadingDoc) {
        return <DocumentSkeleton headerMode={headerMode} />;
    }

    if (!doc) {
        return (
            <div className="h-full flex items-center justify-center text-[var(--text-tertiary)]">
                File not found
            </div>
        );
    }

    const isLoadingPreview = isLoadingContent
        || (previewType === 'pdf' && isLoadingPdfPreview)
        || (previewType === 'office' && officePreviewKind === 'docx' && isLoadingDocxPreview);

    const previewError = loadError
        || (pdfPreviewError instanceof Error ? pdfPreviewError.message : null)
        || (docxPreviewError instanceof Error ? docxPreviewError.message : null);

    /**
     * Reading mode is read-only, and that is the point rather than a shortcut.
     * The chrome that saves, shares and deletes is gone from the screen, so the
     * mode should not also accept edits it has no way to acknowledge.
     */
    const isEditable = canWriteDocument && !isReading;

    return (
        <div
            ref={viewerShellRef}
            className={cn(
                'document-viewer-shell relative flex h-full min-h-0 flex-col',
                isReading ? 'bg-[var(--bg-canvas)]' : 'bg-[var(--card-bg)]'
            )}
            data-reading={isReading ? 'true' : undefined}
        >
            {isReading ? (
                <TooltipProvider>
                    <div className="document-reading-exit">
                        <Tooltip>
                            <TooltipTrigger asChild>
                                <Button
                                    type="button"
                                    variant="quiet"
                                    size="icon"
                                    className="h-8 w-8 rounded"
                                    onClick={exitReading}
                                    aria-label="Exit reading mode"
                                >
                                    <Minimize2 className="h-4 w-4" />
                                </Button>
                            </TooltipTrigger>
                            <TooltipContent side="left">Exit reading mode — Esc</TooltipContent>
                        </Tooltip>
                    </div>
                </TooltipProvider>
            ) : null}

            {headerMode === 'inline' && !isReading ? (
                <div className="context-row flex-wrap items-center justify-between gap-2 px-3 py-2">
                <div className="min-w-0 flex items-center gap-2">
                    {onClose ? (
                        <Button variant="quiet" size="sm" className="h-8 gap-1.5" onClick={onClose}>
                            <ArrowLeft className="h-3.5 w-3.5" />
                            {backLabel}
                        </Button>
                    ) : null}
                    <div className="min-w-0">
                        {contextLabel ? (
                            <p className="text-xs font-medium uppercase tracking-normal text-[var(--text-tertiary)]">
                                {contextLabel}
                            </p>
                        ) : null}
                        <p className="truncate text-sm font-medium text-[var(--text-primary)]">{doc.name}</p>
                        <div className="mt-1 flex flex-wrap items-center gap-1.5">
                            <ResourceVisibilityBadge visibility={documentVisibility} resourceLabel="files" />
                            <FileIndexStatusBadge file={doc} />
                        </div>
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    {headerActions}
                </div>
                </div>
            ) : null}

            <div className="min-h-0 flex-1 overflow-auto p-3">
                {/* By now the header is real and only the body is still coming,
                    so the body keeps the exact fill it already had. */}
                {isLoadingPreview && <DocumentBodySkeleton />}

                {!isLoadingPreview && previewError && (
                    <div className="rounded-md border px-3 py-2 text-xs state-surface-error">
                        {previewError}
                    </div>
                )}

                {!isLoadingPreview && !previewError && (
                    previewType === 'markdown' ? (
                        // One column, one left edge. The frontmatter and the
                        // prose used to sit in boxes of different widths, which
                        // is what made a plain document look crooked.
                        <div className="document-page px-2 py-5 sm:px-4">
                            <DocumentFrontmatter
                                content={docContent}
                                path={documentPath}
                                editable={isEditable}
                                onChange={setDocContent}
                            />
                            <MarkdownEditor
                                content={markdownBody}
                                onChange={isEditable ? handleMarkdownBodyChange : () => undefined}
                                editable={isEditable}
                                className="min-h-[70vh]"
                                editorClassName="min-h-[70vh]"
                                readableProse
                            />
                        </div>
                    ) : previewType === 'html' && !isTextSourceMode ? (
                        <iframe
                            title={doc.name}
                            srcDoc={htmlPreviewDocument?.srcDoc || buildHtmlPreviewSrcDoc(docContent)}
                            sandbox="allow-scripts allow-forms allow-popups allow-modals allow-downloads allow-top-navigation-by-user-activation"
                            className="embedded-canvas block h-full min-h-[78vh] w-full border-0"
                            referrerPolicy="strict-origin-when-cross-origin"
                        />
                    ) : previewType === 'json' || previewType === 'html' || previewType === 'code' ? (
                        <textarea
                            className="document-viewer-source-field h-[78vh] w-full resize-none rounded-md border border-[color:var(--field-border)] bg-[var(--field-bg)] p-3 font-mono text-xs leading-5 text-[var(--text-secondary)] focus:outline-none"
                            value={docContent}
                            onChange={(event) => {
                                if (isEditable) setDocContent(event.target.value);
                            }}
                            readOnly={!isEditable}
                            spellCheck={false}
                        />
                    ) : previewType === 'image' ? (
                        imagePreviewUrl ? (
                            <div className="flex min-h-[78vh] items-start justify-center overflow-auto rounded-md bg-[var(--row-bg)] p-4">
                                {/* eslint-disable-next-line @next/next/no-img-element */}
                                <img
                                    src={imagePreviewUrl}
                                    alt={doc.name}
                                    className="max-h-[78vh] max-w-full object-contain"
                                />
                            </div>
                        ) : (
                            <div className="surface-panel-muted px-3 py-2 text-xs text-[var(--text-tertiary)]">
                                Could not render image preview. Use Download.
                            </div>
                        )
                    ) : previewType === 'pdf' ? (
                        pdfPreview && pdfPreview.pages.length > 0 ? (
                            <div className="space-y-3">
                                {pdfPreview.pages.map((page, index) => (
                                    <div
                                        key={`pdf-page-${index + 1}`}
                                        className="embedded-canvas flex justify-center rounded-md border border-[color:var(--card-border)] p-1.5"
                                    >
                                        {/* eslint-disable-next-line @next/next/no-img-element */}
                                        <img
                                            src={page.src}
                                            alt={`PDF page ${index + 1}`}
                                            width={page.displayWidth}
                                            height={page.displayHeight}
                                            className="h-auto max-w-full rounded-sm"
                                        />
                                    </div>
                                ))}

                                {pdfPreview.truncated && (
                                    <div className="surface-panel-muted px-3 py-2 text-xs text-[var(--text-tertiary)]">
                                        Showing first {pdfPreview.pages.length} of {pdfPreview.totalPages} pages.
                                    </div>
                                )}
                            </div>
                        ) : (
                            <div className="surface-panel-muted px-3 py-2 text-xs text-[var(--text-tertiary)]">
                                Could not render PDF preview. Use Download.
                            </div>
                        )
                    ) : previewType === 'office' ? (
                        officePreviewKind === 'docx' && docxPreviewSrcDoc ? (
                            <iframe
                                title={doc.name}
                                srcDoc={docxPreviewSrcDoc}
                                sandbox=""
                                className="embedded-canvas block w-full h-full min-h-[78vh] rounded-md border border-[color:var(--card-border)]"
                            />
                        ) : (
                            <div className="surface-panel-muted px-3 py-2 text-xs text-[var(--text-tertiary)]">
                                This office file type is not previewable here yet. Use Download.
                            </div>
                        )
                    ) : (
                        <div className="surface-panel-muted px-3 py-4 text-xs text-[var(--text-tertiary)]">
                            <div className="flex items-center gap-2">
                                <FileIcon className="h-4 w-4" />
                                Preview is not available for this file type. Use Download.
                            </div>
                        </div>
                    )
                )}
            </div>
            {canDeleteDocument ? <DestructiveConfirmationDialog
                open={showDeleteDialog}
                onOpenChange={setShowDeleteDialog}
                title="Delete file"
                description={`Delete "${doc.name}"?`}
                resourceName={doc.name}
                confirmationText=""
                consequences={[
                    'The file will be removed from this datastore.',
                    'This action cannot be undone.',
                ]}
                confirmLabel="Delete file"
                pendingLabel="Deleting file..."
                isPending={isDeleting}
                onConfirm={handleDelete}
            /> : null}

            {/* Rendered here rather than inside the menu that opens it: a menu
                unmounts on select, and would take a dialog nested in it along. */}
            {canWriteDocument && canAttachDocumentMarkdown(previewType) ? (
                <MarkdownAttachmentControl
                    podId={podId}
                    datastoreName={datastoreName}
                    filePath={documentPath}
                    metadata={doc.metadata}
                    open={isAttachmentOpen}
                    onOpenChange={setIsAttachmentOpen}
                    showTrigger={false}
                />
            ) : null}
        </div>
    );
}
