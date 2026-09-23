'use client';

import { AlertTriangle, Table, Loader2, Sparkles } from '@/components/ui/icons';
import { cn } from '@/lib/utils';
import type { DatastoreFile } from '@/lib/types';

type FileIndexState = 'searchable' | 'indexing' | 'failed' | 'stored';

type FileLike = Pick<DatastoreFile, 'status' | 'last_processing_error'> & {
    search_enabled?: boolean | null;
};

function resolveIndexState(status: string | null | undefined): FileIndexState {
    switch ((status || '').toUpperCase()) {
        case 'COMPLETED':
            return 'searchable';
        case 'PENDING':
        case 'PROCESSING':
            return 'indexing';
        case 'FAILED':
            return 'failed';
        case 'NOT_REQUIRED':
        default:
            return 'stored';
    }
}

const STATE_CONFIG: Record<
    FileIndexState,
    { label: string; icon: typeof Sparkles; className: string; defaultTitle: string }
> = {
    searchable: {
        label: 'Searchable',
        icon: Sparkles,
        className: 'state-badge-success',
        defaultTitle: 'Indexed for semantic (RAG) search.',
    },
    indexing: {
        label: 'Indexing…',
        icon: Loader2,
        className: 'state-badge-info',
        defaultTitle: 'This document is being indexed for search.',
    },
    failed: {
        label: 'Indexing failed',
        icon: AlertTriangle,
        className: 'state-badge-error',
        defaultTitle: 'Indexing failed for this file.',
    },
    stored: {
        label: 'Stored (not searchable)',
        icon: Table,
        className: 'chip-muted text-[var(--text-tertiary)]',
        defaultTitle: 'Stored file. Data and binary files are kept but not indexed for search.',
    },
};

/**
 * Says something only when there is something to say.
 *
 * "Searchable" is the steady state of every document in the pod, so a green
 * pill announcing it appeared on every file, in every list, next to every
 * title — a badge that is always on is not a status, it is decoration, and it
 * sat in the doc header competing with the filename for the eye. The three
 * states worth a badge are the ones that break the expectation: still
 * indexing, indexing failed, or stored without being indexed at all — that
 * last one being the answer to "why can't the agent find this file?".
 */
export function FileIndexStatusBadge({
    file,
    className,
}: {
    file: FileLike | null | undefined;
    className?: string;
}) {
    if (!file?.status) return null;

    const state = resolveIndexState(file.status);
    if (state === 'searchable') return null;
    const config = STATE_CONFIG[state];
    const Icon = config.icon;
    const title = state === 'failed' && file.last_processing_error
        ? file.last_processing_error
        : config.defaultTitle;

    return (
        <span
            className={cn('chip chip-pill chip-sm shrink-0 gap-1', config.className, className)}
            title={title}
        >
            <Icon className={cn('h-3 w-3', state === 'indexing' && 'lemma-spin')} />
            {config.label}
        </span>
    );
}
