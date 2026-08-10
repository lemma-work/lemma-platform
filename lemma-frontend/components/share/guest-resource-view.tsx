'use client';

import { useEffect, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';

import { ResourceVisibilityBadge } from '@/components/shared/resource-visibility';
import { Button } from '@/components/ui/button';
import { StepLoader } from '@/components/brand/loader';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import type { ShareTarget } from '@/lib/share/share-link';
import { getShareKindCopy, type ShareKind } from '@/lib/share/share-link';

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
 */
export function GuestResourceView({
    target,
    kind,
    preview,
    fallbackName,
}: {
    target: ShareTarget;
    kind: ShareKind;
    preview: ResourcePreview;
    fallbackName: string | null;
}) {
    const name = preview.resource_name || fallbackName || getShareKindCopy(kind).noun;
    const displayName = kind === 'document' || kind === 'folder' ? lastSegment(name) : name;

    return (
        <main className="mx-auto flex min-h-dvh w-full max-w-4xl flex-col px-5 py-8">
            <header className="mb-6 border-b border-[color:var(--border-subtle)] pb-5">
                <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs font-medium uppercase tracking-wide text-[var(--text-tertiary)]">
                        {getShareKindCopy(kind).noun}
                    </span>
                    <ResourceVisibilityBadge
                        visibility={preview.visibility}
                        resourceType={kind === 'app' ? 'app' : undefined}
                        hideWhenDefault={false}
                    />
                </div>
                <h1 className="mt-1.5 truncate font-display text-2xl font-semibold text-[var(--text-primary)]">
                    {displayName}
                </h1>
                <p className="mt-1 text-sm text-[var(--text-secondary)]">
                    Shared with you. You can read this, but you are not a member of the pod it
                    lives in.
                </p>
            </header>

            <GuestResourceBody target={target} kind={kind} preview={preview} />

            <footer className="mt-10 border-t border-[color:var(--border-subtle)] pt-5 text-center">
                <Link
                    href="/"
                    className="text-xs text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]"
                >
                    Lemma — run your apps and agents, with your team
                </Link>
            </footer>
        </main>
    );
}

function lastSegment(value: string): string {
    return value.split('/').filter(Boolean).at(-1) || value;
}

function GuestResourceBody({
    target,
    kind,
    preview,
}: {
    target: ShareTarget;
    kind: ShareKind;
    preview: ResourcePreview;
}) {
    if (kind === 'document') return <GuestDocument target={target} preview={preview} />;
    if (kind === 'table') return <GuestTable target={target} />;
    return <GuestIdentityOnly kind={kind} />;
}

/**
 * Agents, apps, workflows and functions: what it is, not what it does.
 *
 * Running one is a different question from reading one — it spends money, hits
 * connectors and writes to the pod — so a guest never gets controls, only the
 * description the sharer wanted them to see.
 */
function GuestIdentityOnly({ kind }: { kind: ShareKind }) {
    return (
        <section className="rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface-1)] p-6">
            <p className="text-sm text-[var(--text-secondary)]">
                You can see that this {getShareKindCopy(kind).noun.toLowerCase()} exists and who
                shared it. Running it, and seeing what it has done, needs access to the pod it
                belongs to.
            </p>
        </section>
    );
}

const TEXT_PREVIEW_LIMIT = 400_000;

function GuestDocument({ target, preview }: { target: ShareTarget; preview: ResourcePreview }) {
    const path = preview.resource_name || target.resourceName;
    const client = useMemo(() => getLemmaClient(target.podId), [target.podId]);

    const { data, isLoading, error } = useQuery({
        queryKey: ['guest-document', target.podId, path],
        queryFn: async () => {
            const blob = await client.files.download(path!);
            const isText =
                blob.type.startsWith('text/')
                || blob.type.includes('json')
                || blob.type.includes('markdown')
                || /\.(md|txt|csv|json|ya?ml)$/i.test(path || '');
            return { blob, text: isText ? (await blob.text()).slice(0, TEXT_PREVIEW_LIMIT) : null };
        },
        enabled: Boolean(path),
        retry: false,
    });

    // Binary content needs an object URL to render. Derived rather than stored,
    // so the effect below is only responsible for revoking it — object URLs leak
    // for the lifetime of the document otherwise.
    const objectUrl = useMemo(
        () => (data?.blob && data.text === null ? URL.createObjectURL(data.blob) : null),
        [data],
    );

    useEffect(() => {
        if (!objectUrl) return;
        return () => URL.revokeObjectURL(objectUrl);
    }, [objectUrl]);

    if (isLoading) {
        return (
            <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                <StepLoader size="sm" /> Loading document…
            </div>
        );
    }

    if (error || !data) {
        return (
            <p className="text-sm text-[var(--state-error)]">
                This document could not be loaded.
            </p>
        );
    }

    if (data.text !== null) {
        return (
            <article className="overflow-x-auto rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface-1)] p-5">
                <pre className="whitespace-pre-wrap break-words font-mono text-sm text-[var(--text-primary)]">
                    {data.text}
                </pre>
            </article>
        );
    }

    if (data.blob.type.startsWith('image/') && objectUrl) {
        // eslint-disable-next-line @next/next/no-img-element -- a blob URL, not an optimizable asset.
        return <img src={objectUrl} alt={path || 'Shared image'} className="max-w-full rounded-lg" />;
    }

    if (data.blob.type === 'application/pdf' && objectUrl) {
        return <iframe src={objectUrl} title={path || 'Shared document'} className="h-[75dvh] w-full rounded-lg border border-[color:var(--border-subtle)]" />;
    }

    return (
        <section className="rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface-1)] p-6 text-center">
            <p className="mb-4 text-sm text-[var(--text-secondary)]">
                This file type can&apos;t be previewed here.
            </p>
            {objectUrl ? (
                <Button asChild variant="secondary">
                    <a href={objectUrl} download={lastSegment(path || 'download')}>
                        Download
                    </a>
                </Button>
            ) : null}
        </section>
    );
}

const RECORD_PREVIEW_LIMIT = 50;

function GuestTable({ target }: { target: ShareTarget }) {
    const client = useMemo(() => getLemmaClient(target.podId), [target.podId]);
    const tableName = target.resourceName!;

    const { data, isLoading, error } = useQuery({
        queryKey: ['guest-table', target.podId, tableName],
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
        return <p className="text-sm text-[var(--state-error)]">This table could not be loaded.</p>;
    }

    const columns = (data.table?.columns || []).map((column: { name: string }) => column.name);
    const rows = (data.records?.items || []) as Record<string, unknown>[];

    if (!columns.length) {
        return <p className="text-sm text-[var(--text-secondary)]">This table has no columns yet.</p>;
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
