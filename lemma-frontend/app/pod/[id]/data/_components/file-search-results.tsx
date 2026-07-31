'use client';

import { Loader2 } from '@/components/ui/icons';

import { ProductIcon } from '@/components/pod/product-icon';
import { ResourceList, ResourceRow } from '@/components/pod/resource-layout';
import { EmptyState } from '@/components/shared/empty-state';
import { Button } from '@/components/ui/button';

import type { SearchResultItem } from '../_lib/file-helpers';

export interface FileSearchResultsProps {
    currentFolderPath: string | null;
    isSearchingFiles: boolean;
    searchResultItems: SearchResultItem[];
    onOpenSearchResult: (path: string) => void;
    onDownloadByPath: (filePath: string, fileName?: string) => void;
}

export function FileSearchResults({
    currentFolderPath,
    isSearchingFiles,
    searchResultItems,
    onOpenSearchResult,
    onDownloadByPath,
}: FileSearchResultsProps) {
    return (
        <>
            <div className="mb-3 flex items-center justify-between px-1 text-xs text-[var(--text-secondary)]">
                <span>
                    Searching in {currentFolderPath || '/'} (including subfolders)
                </span>
                <span>
                    {isSearchingFiles ? 'Searching…' : `${searchResultItems.length} result${searchResultItems.length === 1 ? '' : 's'}`}
                </span>
            </div>

            {isSearchingFiles && (
                <div className="flex h-full min-h-[200px] items-center justify-center py-12">
                    <Loader2 className="h-6 w-6 animate-spin text-[var(--text-tertiary)]" />
                </div>
            )}

            {!isSearchingFiles && searchResultItems.length === 0 && (
                <EmptyState
                    variant="compact"
                    icon={<ProductIcon kind="files" size="sm" />}
                    title="No files match this search"
                    description="Try different keywords or search from a higher-level folder."
                />
            )}

            {!isSearchingFiles && searchResultItems.length > 0 && (
                <ResourceList>
                    {searchResultItems.map((result) => (
                        <ResourceRow
                            key={`${result.path}-${result.chunkIndex}`}
                            className="group px-3 py-3"
                        >
                            <div className="flex items-start justify-between gap-3">
                                <button
                                    type="button"
                                    className="data-search-result-button min-w-0 flex-1 text-left"
                                    onClick={() => onOpenSearchResult(result.path)}
                                >
                                    <div className="flex min-w-0 items-center gap-2">
                                        <ProductIcon kind="files" size="sm" />
                                        <p className="truncate text-sm font-normal text-[var(--text-primary)]">
                                            {result.fileName}
                                        </p>
                                    </div>
                                    <p className="mt-1 truncate text-xs text-[var(--text-tertiary)]">
                                        {result.path}
                                    </p>
                                </button>

                                <div className="flex items-center gap-2 opacity-70 transition-opacity group-hover:opacity-100">
                                    <span className="chip chip-pill chip-sm chip-muted text-[var(--text-tertiary)]">
                                        {Math.round((result.score || 0) * 100)}%
                                    </span>
                                    <Button
                                        variant="ghost"
                                        size="sm"
                                        className="h-7 px-2 text-xs"
                                        onClick={() => void onDownloadByPath(result.path, result.fileName)}
                                    >
                                        Download
                                    </Button>
                                </div>
                            </div>

                            {result.snippet && (
                                <p className="mt-2 line-clamp-2 max-w-3xl text-xs leading-5 text-[var(--text-secondary)]">
                                    {result.snippet}
                                </p>
                            )}
                        </ResourceRow>
                    ))}
                </ResourceList>
            )}
        </>
    );
}
