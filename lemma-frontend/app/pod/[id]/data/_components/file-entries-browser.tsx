'use client';

import { Download, MoreHorizontal, Trash2 } from '@/components/ui/icons';

import { formatDate, formatFileSize } from '@/components/documents/file-type-icon';
import { ProductIcon } from '@/components/pod/product-icon';
import { ResourceList, ResourceRow } from '@/components/pod/resource-layout';
import { ResourceVisibilityBadge } from '@/components/shared/resource-visibility';
import { Button } from '@/components/ui/button';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { resourceAllows } from '@/lib/authz/resource-actions';
import type { DatastoreFile } from '@/lib/types';

import {
    getDatastoreFilePath,
    getFileVisibility,
    isFolder,
    isImageFile,
} from '../_lib/file-helpers';
import type { FileNamespaceMode } from '../_lib/file-helpers';

export interface FileEntriesBrowserProps {
    filesView: 'grid' | 'list';
    filteredEntries: DatastoreFile[];
    imagePreviewUrls: Record<string, string>;
    activeFileNamespace: FileNamespaceMode;
    canDeleteFiles: boolean;
    isDeletingFile: boolean;
    onFolderOpen: (folderPath: string) => void;
    onOpenFile: (entry: DatastoreFile) => void;
    onDownload: (entry: DatastoreFile) => void;
    onRequestDelete: (entry: DatastoreFile) => void;
}

export function FileEntriesBrowser({
    filesView,
    filteredEntries,
    imagePreviewUrls,
    activeFileNamespace,
    canDeleteFiles,
    isDeletingFile,
    onFolderOpen,
    onOpenFile,
    onDownload,
    onRequestDelete,
}: FileEntriesBrowserProps) {
    if (filesView === 'list') {
        return (
            <ResourceList>
                {filteredEntries.map((entry) => {
                    const folderEntry = isFolder(entry);
                    return (
                        <ResourceRow key={entry.id} className="group grid grid-cols-[minmax(0,1fr)_120px_150px_48px] items-center gap-3 px-3 py-2.5 text-sm">
                            {folderEntry ? (
                                <button
                                    type="button"
                                    className="data-file-list-entry-button flex min-w-0 items-center gap-2 rounded px-1 py-1 text-left"
                                    onClick={() => onFolderOpen(getDatastoreFilePath(entry))}
                                >
                                    <ProductIcon kind="folders" size="sm" />
                                    <span className="truncate text-[var(--text-primary)]">{entry.name}</span>
                                </button>
                            ) : (
                                <button
                                    type="button"
                                    className="data-file-list-entry-button flex min-w-0 items-center gap-2 rounded px-1 py-1 text-left"
                                    onClick={() => void onOpenFile(entry)}
                                >
                                    {isImageFile(entry) && imagePreviewUrls[getDatastoreFilePath(entry)] ? (
                                        // eslint-disable-next-line @next/next/no-img-element
                                        <img
                                            src={imagePreviewUrls[getDatastoreFilePath(entry)]}
                                            alt={entry.name}
                                            className="h-6 w-6 shrink-0 rounded object-cover"
                                        />
                                    ) : (
                                        <ProductIcon kind="files" size="sm" />
                                    )}
                                    <span className="truncate text-[var(--text-primary)]">{entry.name}</span>
                                </button>
                            )}

                            <span className="text-xs text-[var(--text-secondary)]">{folderEntry ? 'Folder' : 'File'}</span>
                            <div className="flex min-w-0 items-center gap-2">
                                <span className="truncate text-xs text-[var(--text-secondary)]">
                                    {folderEntry ? `Updated ${formatDate(entry.updated_at)}` : formatFileSize(entry.size_bytes)}
                                </span>
                                <ResourceVisibilityBadge visibility={getFileVisibility(entry, activeFileNamespace)} resourceLabel="files" compact />
                            </div>

                            <div className="flex items-center justify-end opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                                <DropdownMenu>
                                    <DropdownMenuTrigger asChild>
                                        <Button variant="ghost" size="icon" className="h-7 w-7">
                                            <MoreHorizontal className="h-4 w-4" />
                                        </Button>
                                    </DropdownMenuTrigger>
                                    <DropdownMenuContent align="end">
                                        {!folderEntry && (
                                            <DropdownMenuItem onClick={() => void onDownload(entry)}>
                                                <Download className="mr-2 h-4 w-4" />
                                                Download
                                            </DropdownMenuItem>
                                        )}
                                        {resourceAllows(entry, 'folder.delete', canDeleteFiles) ? <DropdownMenuItem
                                            onClick={() => onRequestDelete(entry)}
                                            disabled={isDeletingFile}
                                            className="text-[var(--state-error)] focus:text-[var(--state-error)]"
                                        >
                                            <Trash2 className="mr-2 h-4 w-4" />
                                            Delete
                                        </DropdownMenuItem> : null}
                                    </DropdownMenuContent>
                                </DropdownMenu>
                            </div>
                        </ResourceRow>
                    );
                })}
            </ResourceList>
        );
    }

    return (
        <div className="grid grid-cols-2 gap-x-5 gap-y-6 px-1 py-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
            {filteredEntries.map((entry) => {
                const folderEntry = isFolder(entry);
                return (
                    <div
                        key={entry.id}
                                        className="group rounded-lg p-2.5 transition-colors hover:bg-[color:color-mix(in_srgb,var(--surface-2)_42%,transparent)]"
                    >
                        <button
                            type="button"
                            className="data-file-grid-entry-button w-full text-left"
                            onClick={() => (folderEntry ? onFolderOpen(getDatastoreFilePath(entry)) : onOpenFile(entry))}
                        >
                            <div className="mb-3">
                                {folderEntry ? (
                                    <div className="flex aspect-[4/3] w-full items-center justify-center">
                                        <ProductIcon kind="folders" size="xl" />
                                    </div>
                                ) : isImageFile(entry) && imagePreviewUrls[getDatastoreFilePath(entry)] ? (
                                    // eslint-disable-next-line @next/next/no-img-element
                                    <img
                                        src={imagePreviewUrls[getDatastoreFilePath(entry)]}
                                        alt={entry.name}
                                        className="aspect-[4/3] w-full rounded-lg object-cover"
                                    />
                                ) : (
                                    <div className="flex aspect-[4/3] w-full items-center justify-center">
                                        <ProductIcon kind="files" size="xl" />
                                    </div>
                                )}
                            </div>
                            <p className="truncate text-sm font-normal text-[var(--text-primary)]">{entry.name}</p>
                            <p className="mt-1 text-xs text-[var(--text-secondary)]">
                                {folderEntry ? 'Folder' : formatFileSize(entry.size_bytes)}
                            </p>
                            <div className="mt-1 flex min-w-0 items-center gap-2 text-xs text-[var(--text-tertiary)]">
                                <span className="truncate">{formatDate(entry.updated_at)}</span>
                                <ResourceVisibilityBadge visibility={getFileVisibility(entry, activeFileNamespace)} resourceLabel="files" compact />
                            </div>
                        </button>
                        <div className="mt-2 flex items-center justify-end opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                            <DropdownMenu>
                                <DropdownMenuTrigger asChild>
                                    <Button variant="ghost" size="icon" className="h-7 w-7">
                                        <MoreHorizontal className="h-4 w-4" />
                                    </Button>
                                </DropdownMenuTrigger>
                                <DropdownMenuContent align="end">
                                    {!folderEntry && (
                                        <DropdownMenuItem onClick={() => void onDownload(entry)}>
                                            <Download className="mr-2 h-4 w-4" />
                                            Download
                                        </DropdownMenuItem>
                                    )}
                                    {resourceAllows(entry, 'folder.delete', canDeleteFiles) ? <DropdownMenuItem
                                        onClick={() => onRequestDelete(entry)}
                                        disabled={isDeletingFile}
                                        className="text-[var(--state-error)] focus:text-[var(--state-error)]"
                                    >
                                        <Trash2 className="mr-2 h-4 w-4" />
                                        Delete
                                    </DropdownMenuItem> : null}
                                </DropdownMenuContent>
                            </DropdownMenu>
                        </div>
                    </div>
                );
            })}
        </div>
    );
}
