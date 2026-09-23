'use client';

import { Fragment } from 'react';
import { useQuery } from '@tanstack/react-query';

import { Check, ChevronRight, Folder } from '@/components/ui/icons';
import { fetchChildFolders } from '@/lib/files/folder-picker';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { cn } from '@/lib/utils';

/**
 * The pod's folder tree, walked one directory at a time.
 *
 * Shared by every folder grant surface so a folder is picked the same way
 * wherever it is picked. Mounting a level is what fetches it, so depth costs a
 * request only once someone actually expands into it.
 */

function folderLevelQueryOptions(podId: string, directoryPath: string) {
    return {
        queryKey: ['folder-children', podId, directoryPath] as const,
        queryFn: () => fetchChildFolders(
            (args) => getLemmaClient(podId).files.list(args),
            directoryPath,
        ),
        staleTime: 5 * 60 * 1000,
        gcTime: 15 * 60 * 1000,
        refetchOnMount: false,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
    };
}

function FolderPickerNote({ depth, children }: { depth: number; children: React.ReactNode }) {
    return (
        /* eslint-disable-next-line no-restricted-syntax -- Tree indent scales with nesting depth, which has no fixed set of classes. */
        <p className="folder-picker-note" style={{ '--folder-depth': depth } as React.CSSProperties}>
            {children}
        </p>
    );
}

/** Subfolders of `directoryPath`, each expandable into its own level. */
export function FolderPickerLevel({
    podId,
    directoryPath,
    depth,
    selected,
    expandedPaths,
    onToggleFolder,
    onToggleExpanded,
}: {
    podId: string;
    directoryPath: string;
    depth: number;
    selected: string[];
    expandedPaths: Set<string>;
    onToggleFolder: (folderPath: string) => void;
    onToggleExpanded: (folderPath: string) => void;
}) {
    const { data, isPending, isError } = useQuery(folderLevelQueryOptions(podId, directoryPath));

    if (isPending) {
        return <FolderPickerNote depth={depth}>Loading…</FolderPickerNote>;
    }

    if (isError) {
        return <FolderPickerNote depth={depth}>Could not load folders.</FolderPickerNote>;
    }

    const folders = data?.folders || [];

    if (folders.length === 0) {
        return (
            <FolderPickerNote depth={depth}>
                {depth === 0 ? 'No folders found' : 'No subfolders'}
            </FolderPickerNote>
        );
    }

    return (
        <>
            {folders.map((folder) => {
                const isExpanded = expandedPaths.has(folder.path);
                const isSelected = selected.includes(folder.path);

                return (
                    <Fragment key={folder.path}>
                        <div
                            className="folder-picker-row"
                            data-selected={isSelected}
                            /* eslint-disable-next-line no-restricted-syntax -- Tree indent scales with nesting depth, which has no fixed set of classes. */
                            style={{ '--folder-depth': depth } as React.CSSProperties}
                        >
                            <button
                                type="button"
                                className="folder-picker-disclosure"
                                aria-expanded={isExpanded}
                                aria-label={`${isExpanded ? 'Collapse' : 'Expand'} ${folder.name}`}
                                onClick={() => onToggleExpanded(folder.path)}
                            >
                                <ChevronRight className={cn('h-3.5 w-3.5 transition-transform', isExpanded && 'rotate-90')} />
                            </button>
                            <button
                                type="button"
                                className="folder-picker-option"
                                aria-pressed={isSelected}
                                title={folder.path}
                                onClick={() => onToggleFolder(folder.path)}
                            >
                                <span className="folder-picker-check" data-checked={isSelected}>
                                    {isSelected ? <Check className="h-3 w-3" /> : null}
                                </span>
                                <Folder className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" />
                                <span className="truncate">{folder.name}</span>
                            </button>
                        </div>

                        {isExpanded ? (
                            <FolderPickerLevel
                                podId={podId}
                                directoryPath={folder.path}
                                depth={depth + 1}
                                selected={selected}
                                expandedPaths={expandedPaths}
                                onToggleFolder={onToggleFolder}
                                onToggleExpanded={onToggleExpanded}
                            />
                        ) : null}
                    </Fragment>
                );
            })}

            {data?.truncated ? (
                <FolderPickerNote depth={depth}>
                    Too many entries here to list every folder.
                </FolderPickerNote>
            ) : null}
        </>
    );
}
