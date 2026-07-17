'use client';

import { AlertTriangle, Database, Loader2, Sparkles } from '@/components/ui/icons';
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
        icon: Database,
        className: 'chip-muted text-[var(--text-tertiary)]',
        defaultTitle: 'Stored file. Data and binary files are kept but not indexed for search.',
    },
};

export function FileIndexStatusBadge({
    file,
    className,
}: {
    file: FileLike | null | undefined;
    className?: string;
}) {
    if (!file?.status) return null;

    const state = resolveIndexState(file.status);
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
            <Icon className={cn('h-3 w-3', state === 'indexing' && 'animate-spin')} />
            {config.label}
        </span>
    );
}
