'use client';

import { useCallback, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { toast } from 'sonner';

import { ArrowUpRight, Download } from '@/components/ui/icons';
import { ThemeToggle } from '@/components/theme/theme-toggle';
import { Button } from '@/components/ui/button';
import { DocumentBodySkeleton } from '@/components/documents/document-skeleton';
import { DocumentPreviewBody, useDocumentPreview } from '@/components/documents/document-preview';
import { getDocumentPreviewType } from '@/components/documents/preview-renderers';
import { cn } from '@/lib/utils';
import { StepLoader } from '@/components/brand/loader';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import type { ShareTarget } from '@/lib/share/share-link';
import { getShareKindCopy, humanizeResourceName, type ShareKind } from '@/lib/share/share-link';

interface ResourcePreview {
    resource_type: string;
    resource_name?: string | null;
    resource_id?: string | null;
    pod_id: string;
    visibility?: string | null;
    allowed_actions?: string[] | null;
}

/**
 * What someone sees when a link they were sent is genuinely theirs to read, but
 * the pod around it is not.
 *
 * Chromeless on purpose. The workspace shell would leak the shape of a pod this
 * reader has no standing in — agent names, folder structure, how much is in
 * there — none of which the sharer offered. One resource, nothing else.
 *
 * "Chromeless" was once taken to mean "and therefore built separately", which is
 * how this page ended up with its own idea of what a document looks like. The
 * frame around the resource is this page's own; the resource inside it renders
 * through exactly what the workspace uses.
 */
export function SharedResourceView({
    target,
    kind,
    preview,
    fallbackName,
    openInPodHref,
}: {
    target: ShareTarget;
    kind: ShareKind;
    preview: ResourcePreview;
    fallbackName: string | null;
    /** Set only for a reader who is in the pod; null for everyone else. */
    openInPodHref?: string | null;
}) {
    const name = preview.resource_name || fallbackName || getShareKindCopy(kind).noun;
    const displayName = humanizeResourceName(name);
    const documentPath = (kind === 'document' && (preview.resource_name || target.resourceName)) || null;

    const handleDownload = useCallback(async () => {
        if (!documentPath) return;
        try {
            const blob = await getLemmaClient(target.podId).files.download(documentPath);
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = lastSegment(documentPath);
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(url);
        } catch {
            toast.error('Failed to download file');
        }
    }, [documentPath, target.podId]);

    // A shared document is not a page *about* a document. Every row of chrome
    // above it is a row the thing you were sent does not get, and the frame
    // used to take three of them plus a footer — which left an 78vh preview
    // opening below the fold, on a page that scrolled as a whole while the
    // document scrolled inside it. One bar, and the rest is the document.
    const previewType = documentPath ? getDocumentPreviewType(documentPath) : null;

    return (
        <div className="flex h-dvh flex-col bg-[var(--card-bg)]">
            <header className="flex shrink-0 items-center justify-between gap-3 border-b border-[color:var(--border-subtle)] px-3 py-1.5">
                <div className="flex min-w-0 items-baseline gap-2">
                    <p className="truncate text-sm font-medium text-[var(--text-primary)]">
                        {displayName}
                    </p>
                    {/* The one thing a guest cannot work out from the page: why
                        it has no workspace around it. A member can — they have
                        the pod, and the button on the right proves it. */}
                    {openInPodHref ? null : (
                        <span className="shrink-0 text-xs text-[var(--text-tertiary)]">
                            Shared with you
                        </span>
                    )}
                </div>
                <div className="flex shrink-0 items-center gap-1">
                    {documentPath ? (
                        <Button
                            variant="quiet"
                            size="sm"
                            className="h-7 gap-1.5"
                            onClick={handleDownload}
                        >
                            <Download className="h-3.5 w-3.5" />
                            Download
                        </Button>
                    ) : null}
                    {openInPodHref ? (
                        <Button variant="secondary" size="sm" asChild className="h-7 gap-1.5">
                            <Link href={openInPodHref} prefetch={false}>
                                Open in pod
                                <ArrowUpRight className="h-3.5 w-3.5" />
                            </Link>
                        </Button>
                    ) : null}
                    {/* A shared document is often the only Lemma page its
                        reader ever opens, and it inherits a system appearance
                        they had no say in. */}
                    <ThemeToggle variant="icon" className="h-7 w-7" />
                    <Link
                        href="/"
                        className="px-1.5 text-xs text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]"
                    >
                        Lemma
                    </Link>
                </div>
            </header>

            {/* An HTML file is a whole page and gets the frame edge to edge;
                everything else reads better with the viewer's own margin. */}
            <div
                className={cn(
                    'min-h-0 flex-1 overflow-auto',
                    previewType === 'html' ? null : 'p-3',
                )}
            >
                <SharedResourceBody target={target} kind={kind} documentPath={documentPath} />
            </div>
        </div>
    );
}

function lastSegment(value: string): string {
    return value.split('/').filter(Boolean).at(-1) || value;
}

function SharedResourceBody({
    target,
    kind,
    documentPath,
}: {
    target: ShareTarget;
    kind: ShareKind;
    documentPath: string | null;
}) {
    if (kind === 'document' && documentPath) {
        return <SharedDocument podId={target.podId} path={documentPath} />;
    }
    if (kind === 'table') return <SharedTable target={target} />;
    return <SharedIdentityOnly kind={kind} />;
}

/**
 * Agents, apps, workflows and functions: what it is, not what it does.
 *
 * Running one is a different question from reading one — it spends money, hits
 * connectors and writes to the pod — so a guest never gets controls, only the
 * description the sharer wanted them to see.
 */
function SharedIdentityOnly({ kind }: { kind: ShareKind }) {
    return (
        <section className="surface-panel-muted px-3 py-4 text-xs text-[var(--text-tertiary)]">
            You can see that this {getShareKindCopy(kind).noun.toLowerCase()} exists and who shared
            it. Running it, and seeing what it has done, needs access to the pod it belongs to.
        </section>
    );
}

/**
 * A shared file, rendered the way the workspace renders it.
 *
 * Markdown is typeset, an `.html` file is the page rather than its source, a PDF
 * comes back as pages and a `.docx` as its own document — all of it the same
 * code path a member gets, so the two readings of one file cannot diverge.
 */
function SharedDocument({ podId, path }: { podId: string; path: string }) {
    const preview = useDocumentPreview({ podId, path, name: path });

    if (preview.isLoading) return <DocumentBodySkeleton />;

    if (preview.isError) {
        return (
            <div className="rounded-md border px-3 py-2 text-xs state-surface-error">
                This document could not be loaded.
            </div>
        );
    }

    return (
        <DocumentPreviewBody
            name={lastSegment(path)}
            path={path}
            previewType={preview.previewType}
            officeKind={preview.officeKind}
            content={preview.content}
            htmlSrcDoc={preview.htmlSrcDoc}
            imageUrl={preview.imageUrl}
            pdf={preview.pdf}
            docxSrcDoc={preview.docxSrcDoc}
        />
    );
}

const RECORD_PREVIEW_LIMIT = 50;

function SharedTable({ target }: { target: ShareTarget }) {
    const client = useMemo(() => getLemmaClient(target.podId), [target.podId]);
    const tableName = target.resourceName!;

    const { data, isLoading, error } = useQuery({
        queryKey: ['shared-table', target.podId, tableName],
        queryFn: async () => {
            const [table, records] = await Promise.all([
                client.tables.get(tableName),
                client.records.list(tableName, { limit: RECORD_PREVIEW_LIMIT }),
            ]);
            return { table, records };
        },
        retry: false,
    });

    if (isLoading) {
        return (
            <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                <StepLoader size="sm" /> Loading table…
            </div>
        );
    }

    if (error || !data) {
        return (
            <div className="rounded-md border px-3 py-2 text-xs state-surface-error">
                This table could not be loaded.
            </div>
        );
    }

    const columns = (data.table?.columns || []).map((column: { name: string }) => column.name);
    const rows = (data.records?.items || []) as Record<string, unknown>[];

    if (!columns.length) {
        return (
            <p className="surface-panel-muted px-3 py-2 text-xs text-[var(--text-tertiary)]">
                This table has no columns yet.
            </p>
        );
    }

    return (
        <section className="min-w-0">
            {/* The table scrolls inside its own box; the page never scrolls sideways. */}
            <div className="overflow-x-auto rounded-lg border border-[color:var(--border-subtle)]">
                <table className="w-full border-collapse text-sm">
                    <thead>
                        <tr className="bg-[var(--surface-2)]">
                            {columns.map((column) => (
                                <th
                                    key={column}
                                    className="whitespace-nowrap px-3 py-2 text-left font-medium text-[var(--text-secondary)]"
                                >
                                    {column}
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((row, index) => (
                            <tr
                                key={String(row.id ?? index)}
                                className="border-t border-[color:var(--border-subtle)]"
                            >
                                {columns.map((column) => (
                                    <td
                                        key={column}
                                        className="max-w-xs truncate px-3 py-2 text-[var(--text-primary)]"
                                    >
                                        {formatCell(row[column])}
                                    </td>
                                ))}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <p className="mt-2 text-xs text-[var(--text-tertiary)]">
                {rows.length === 0
                    ? 'No rows are visible to you.'
                    : `Showing up to ${RECORD_PREVIEW_LIMIT} rows.`}
            </p>
        </section>
    );
}

function formatCell(value: unknown): string {
    if (value === null || value === undefined) return '';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
}
