'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';
import {
    CheckCircle2,
    Files,
    FileText,
    Folder,
    FolderPlus,
    Loader2,
    Search,
    Share2,
    Upload,
} from '@/components/ui/icons';
import { toast } from 'sonner';

import { DocumentViewer } from '@/components/documents/document-viewer';
import { FileIndexStatusBadge } from '@/components/documents/file-index-status-badge';
import {
    FolderUploadConfirmDialog,
    FolderUploadProgress,
    folderInputAttributes,
    useResumableFolderUpload,
} from '@/components/documents/resumable-folder-upload';
import { ProductIcon } from '@/components/pod/product-icon';
import { SectionPrimer } from '@/components/education/section-primer';
import { ResourceDetailHeader, ResourceIndexHeader, ResourceIndexShell } from '@/components/pod/resource-layout';
import { DestructiveConfirmationDialog } from '@/components/shared/destructive-confirmation-dialog';
import { QuietEmptyState } from '@/components/shared/empty-state';
import { DestructiveResourceActionItem, ResourceActionsMenu } from '@/components/shared/resource-actions-menu';
import { ResourceShareButton, ResourceVisibilityBadge, type ResourceVisibilityValue } from '@/components/shared/resource-visibility';
import { Button } from '@/components/ui/button';
import { DropdownMenuItem } from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import {
    useCreateDatastoreFolder,
    useDatastoreFiles,
    useDeleteDatastoreFile,
    useSearchDatastoreFiles,
    useUploadDatastoreFile,
} from '@/lib/hooks/use-datastores';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import type { DatastoreFile } from '@/lib/types';
import type { FileSearchResultSchema } from 'lemma-sdk';
import { cn } from '@/lib/utils';

type DocSearchResult = FileSearchResultSchema;

type DocSearchItem = {
    path: string;
    fileName: string;
    snippet: string;
    score: number;
    chunkIndex: number;
};

type DocsUploadStatus = {
    state: 'uploading' | 'complete';
    total: number;
    completed: number;
    failed: number;
    source: 'picker' | 'drop';
};

const DATASTORE_NAME = 'default';
const PERSONAL_FILES_ROOT = '/me';
const PERSONAL_FILES_LABEL = 'Personal files';

function activeDirectoryPath(folderPath: string | null): string {
    return folderPath || '/';
}

function getFileNameFromPath(path: string): string {
    const parts = path.replace(/\\/g, '/').split('/').filter(Boolean);
    return parts[parts.length - 1] || path;
}

function getParentDirectoryPath(path: string | null | undefined): string | null {
    if (!path) return null;
    const normalized = path.replace(/\\/g, '/').replace(/\/+$/g, '');
    const parts = normalized.split('/').filter(Boolean);
    if (parts.length <= 1) return null;
    return `/${parts.slice(0, -1).join('/')}`;
}

function getDirectoryLabel(path: string | null | undefined): string {
    if (!path || path === '/') return 'Docs';
    if (isPersonalRootPath(path)) return PERSONAL_FILES_LABEL;
    return getFileNameFromPath(path);
}

function isFolder(file: DatastoreFile): boolean {
    return file.kind === 'FOLDER';
}

function isPersonalPath(path: string | null | undefined): boolean {
    if (!path) return false;
    const normalized = path.startsWith('/') ? path : `/${path}`;
    return normalized === PERSONAL_FILES_ROOT || normalized.startsWith(`${PERSONAL_FILES_ROOT}/`);
}

function isPersonalRootPath(path: string | null | undefined): boolean {
    if (!path) return false;
    const normalized = path.startsWith('/') ? path : `/${path}`;
    return normalized === PERSONAL_FILES_ROOT;
}

function getFilePath(file: DatastoreFile): string {
    return file.path || file.id;
}

function getDocEntryLabel(file: DatastoreFile): string {
    return isFolder(file) && isPersonalRootPath(getFilePath(file)) ? PERSONAL_FILES_LABEL : file.name;
}

function getDocEntryVisibility(file: DatastoreFile): string {
    return file.visibility || (isPersonalPath(getFilePath(file)) ? 'PERSONAL' : 'POD');
}

function joinPath(basePath: string | null, segment: string): string {
    const cleanSegment = segment.trim().replace(/^\/+|\/+$/g, '');
    const normalizedBase = (basePath || '/').trim() || '/';
    if (!cleanSegment) return normalizedBase;
    if (normalizedBase === '/') return `/${cleanSegment}`;
    return `${normalizedBase.replace(/\/+$/, '')}/${cleanSegment}`;
}

function isMarkdownName(value: string): boolean {
    return /\.mdx?$/i.test(value) || /\.markdown$/i.test(value);
}

function pageFileName(rawName: string): string {
    const clean = rawName.trim();
    if (!clean) return '';
    return isMarkdownName(clean) ? clean : `${clean}.md`;
}

export function DocumentSpace({ podId }: { podId: string }) {
    const router = useRouter();
    const pathname = usePathname();
    const searchParams = useSearchParams();
    const queryClient = useQueryClient();
    const docsUploadInputRef = useRef<HTMLInputElement>(null);
    const docsDragDepthRef = useRef(0);
    const docsUploadResetTimerRef = useRef<number | null>(null);
    const searchRequestIdRef = useRef(0);
    const { mutateAsync: uploadFile, isPending: isUploadingFile } = useUploadDatastoreFile();
    const { mutateAsync: createFolder, isPending: isCreatingFolder } = useCreateDatastoreFolder();
    const { mutateAsync: deleteFile, isPending: isDeletingFile } = useDeleteDatastoreFile();
    const { mutate: searchFiles, isPending: isSearchingFiles } = useSearchDatastoreFiles();
    const [isCopyingAcrossNamespace, setIsCopyingAcrossNamespace] = useState(false);
    const [isDocsDragActive, setIsDocsDragActive] = useState(false);
    const [dragFileCount, setDragFileCount] = useState(0);
    const [docsUploadStatus, setDocsUploadStatus] = useState<DocsUploadStatus | null>(null);
    const [newPageName, setNewPageName] = useState('');
    const [newFolderName, setNewFolderName] = useState('');
    const [docsSearchQuery, setDocsSearchQuery] = useState('');
    const [debouncedDocsSearchQuery, setDebouncedDocsSearchQuery] = useState('');
    const [searchResults, setSearchResults] = useState<DocSearchResult[]>([]);
    const [entryPendingDelete, setEntryPendingDelete] = useState<DatastoreFile | null>(null);

    const folderPath = searchParams.get('folder');
    const isAssistantPresentation = Boolean(searchParams.get('assistantConversationId') || searchParams.get('conversationId'));
    const currentDirectoryPath = activeDirectoryPath(folderPath);
    const selectedFilePath = searchParams.get('file');
    const selectedFileName = selectedFilePath ? getFileNameFromPath(selectedFilePath) : '';
    const selectedFileIsPersonal = isPersonalPath(selectedFilePath);
    const folderUpload = useResumableFolderUpload({
        podId,
        datastoreName: DATASTORE_NAME,
        directoryPath: currentDirectoryPath,
        disabled: isUploadingFile,
    });
    const { data: docsFilesData, isLoading: isLoadingDocsFiles } = useDatastoreFiles(
        podId,
        DATASTORE_NAME,
        {
            directory_path: currentDirectoryPath,
            limit: 200,
        }
    );

    const docsEntries = useMemo(() => {
        return [...(docsFilesData?.items || [])].sort((left, right) => {
            if (isFolder(left) !== isFolder(right)) return isFolder(left) ? -1 : 1;
            return left.name.localeCompare(right.name);
        });
    }, [docsFilesData?.items]);

    const handleShareEntryVisibilityChange = async (entry: DatastoreFile, visibility: ResourceVisibilityValue) => {
        await getLemmaClient(podId).files.update(getFilePath(entry), {
            visibility,
        });
        queryClient.invalidateQueries({ queryKey: ['datastore-files', podId, DATASTORE_NAME] });
        toast.success('Sharing updated');
    };

    useEffect(() => {
        const timeout = window.setTimeout(() => {
            setDebouncedDocsSearchQuery(docsSearchQuery);
        }, 300);
        return () => {
            window.clearTimeout(timeout);
        };
    }, [docsSearchQuery]);

    useEffect(() => {
        return () => {
            if (docsUploadResetTimerRef.current !== null) {
                window.clearTimeout(docsUploadResetTimerRef.current);
            }
        };
    }, []);

    useEffect(() => {
        const query = debouncedDocsSearchQuery.trim();
        if (!query) {
            searchRequestIdRef.current += 1;
            setSearchResults([]);
            return;
        }

        const requestId = ++searchRequestIdRef.current;
        searchFiles(
            {
                podId,
                datastoreName: DATASTORE_NAME,
                query,
                limit: 80,
                search_method: 'HYBRID',
                scope_path: currentDirectoryPath,
                scope_mode: 'SUBTREE',
            },
            {
                onSuccess: (response) => {
                    if (requestId !== searchRequestIdRef.current) return;
                    setSearchResults(response.items);
                },
                onError: () => {
                    if (requestId !== searchRequestIdRef.current) return;
                    setSearchResults([]);
                    toast.error('Search failed');
                },
            }
        );
    }, [currentDirectoryPath, debouncedDocsSearchQuery, podId, searchFiles]);

    const isSearchMode = debouncedDocsSearchQuery.trim().length > 0;

    const searchResultItems = useMemo(() => {
        if (!isSearchMode) return [];

        const byPath = new Map<string, DocSearchItem>();
        searchResults.forEach((result) => {
            const path = (result.path || result.file_id || '').trim();
            if (!path) return;

            const current = byPath.get(path);
            if (current && current.score >= result.score) return;

            byPath.set(path, {
                path,
                fileName: getFileNameFromPath(path),
                snippet: (result.content || '').trim(),
                score: result.score || 0,
                chunkIndex: result.chunk_index || 0,
            });
        });

        return Array.from(byPath.values()).sort((left, right) => right.score - left.score);
    }, [isSearchMode, searchResults]);

    const buildDocsHref = (updates: Record<string, string | null>) => {
        const nextParams = new URLSearchParams(searchParams.toString());
        nextParams.delete('namespace');
        Object.entries(updates).forEach(([key, value]) => {
            if (value === null || value === '') nextParams.delete(key);
            else nextParams.set(key, value);
        });
        const nextQuery = nextParams.toString();
        return nextQuery ? `${pathname}?${nextQuery}` : pathname;
    };

    const updateQuery = (updates: Record<string, string | null>) => {
        router.push(buildDocsHref(updates), { scroll: false });
    };

    const handleCopyPersonalFileToPod = async () => {
        if (!selectedFilePath || !selectedFileIsPersonal) return;
        setIsCopyingAcrossNamespace(true);
        try {
            const blob = await getLemmaClient(podId).files.download(selectedFilePath);
            const filename = getFileNameFromPath(selectedFilePath);
            const file = new File([blob], filename, { type: blob.type || 'application/octet-stream' });
            await uploadFile({
                podId,
                datastoreName: DATASTORE_NAME,
                file,
                directory_path: '/',
            });
            toast.success('Duplicated to pod docs');
        } catch {
            toast.error('Failed to duplicate document');
        } finally {
            setIsCopyingAcrossNamespace(false);
        }
    };

    const openDocsFolder = (nextFolderPath: string | null) => {
        updateQuery({
            folder: nextFolderPath,
            file: null,
        });
    };

    const openDocFile = (filePath: string) => {
        updateQuery({ file: filePath });
    };

    const handleCreateDocPage = async () => {
        const filename = pageFileName(newPageName);
        if (!filename) {
            toast.error('Name the page first');
            return;
        }

        const title = filename.replace(/\.mdx?$/i, '').replace(/\.markdown$/i, '');
        const file = new File([`# ${title}\n\nStart writing...\n`], filename, { type: 'text/markdown' });

        try {
            await uploadFile({
                podId,
                datastoreName: DATASTORE_NAME,
                file,
                directory_path: currentDirectoryPath,
            });
            setNewPageName('');
            openDocFile(joinPath(currentDirectoryPath, filename));
            toast.success('Page created');
        } catch (error) {
            toast.error(error instanceof Error ? error.message : 'Failed to create page');
        }
    };

    const handleCreateDocsFolder = async () => {
        const name = newFolderName.trim();
        if (!name) {
            toast.error('Name the folder first');
            return;
        }

        try {
            await createFolder({
                podId,
                datastoreName: DATASTORE_NAME,
                name,
                directory_path: currentDirectoryPath,
            });
            setNewFolderName('');
            toast.success('Folder created');
        } catch (error) {
            toast.error(error instanceof Error ? error.message : 'Failed to create folder');
        }
    };

    const handleDeleteEntry = async () => {
        if (!entryPendingDelete) return;

        const deletingFolder = isFolder(entryPendingDelete);
        try {
            await deleteFile({
                podId,
                datastoreName: DATASTORE_NAME,
                file_path: getFilePath(entryPendingDelete),
            });
            toast.success(`${deletingFolder ? 'Folder' : 'File'} deleted`);
            setEntryPendingDelete(null);
        } catch (error) {
            toast.error(error instanceof Error ? error.message : `Failed to delete ${deletingFolder ? 'folder' : 'file'}`);
        }
    };

    const handleDocsUpload = async (files: FileList | null, source: DocsUploadStatus['source'] = 'picker') => {
        const selectedFiles = Array.from(files || []);
        if (selectedFiles.length === 0) return;

        if (docsUploadResetTimerRef.current !== null) {
            window.clearTimeout(docsUploadResetTimerRef.current);
            docsUploadResetTimerRef.current = null;
        }
        setDocsUploadStatus({
            state: 'uploading',
            total: selectedFiles.length,
            completed: 0,
            failed: 0,
            source,
        });

        const results = await Promise.allSettled(
            selectedFiles.map(async (file) => {
                try {
                    await uploadFile({
                        podId,
                        datastoreName: DATASTORE_NAME,
                        file,
                        directory_path: currentDirectoryPath,
                    });
                    setDocsUploadStatus((current) => current
                        ? { ...current, completed: current.completed + 1 }
                        : current
                    );
                } catch (error) {
                    setDocsUploadStatus((current) => current
                        ? { ...current, failed: current.failed + 1 }
                        : current
                    );
                    throw error;
                }
            })
        );

        const uploadedCount = results.filter((result) => result.status === 'fulfilled').length;
        const failedCount = selectedFiles.length - uploadedCount;
        setDocsUploadStatus({
            state: 'complete',
            total: selectedFiles.length,
            completed: uploadedCount,
            failed: failedCount,
            source,
        });
        docsUploadResetTimerRef.current = window.setTimeout(() => {
            setDocsUploadStatus(null);
            docsUploadResetTimerRef.current = null;
        }, 5000);
        if (uploadedCount > 0) toast.success(`Uploaded ${uploadedCount} file${uploadedCount === 1 ? '' : 's'}`);
        if (failedCount > 0) toast.error(`${failedCount} upload${failedCount === 1 ? '' : 's'} failed`);
    };

    const eventHasFiles = (event: React.DragEvent<HTMLElement>) => {
        return Array.from(event.dataTransfer.types || []).includes('Files');
    };

    const getDragFileCount = (event: React.DragEvent<HTMLElement>) => {
        return event.dataTransfer.items?.length || event.dataTransfer.files?.length || 0;
    };

    const handleDocsDragEnter = (event: React.DragEvent<HTMLDivElement>) => {
        if (!eventHasFiles(event)) return;
        event.preventDefault();
        docsDragDepthRef.current += 1;
        setDragFileCount(getDragFileCount(event));
        setIsDocsDragActive(true);
    };

    const handleDocsDragOver = (event: React.DragEvent<HTMLDivElement>) => {
        if (!eventHasFiles(event)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = 'copy';
        setDragFileCount(getDragFileCount(event));
        setIsDocsDragActive(true);
    };

    const handleDocsDragLeave = (event: React.DragEvent<HTMLDivElement>) => {
        if (!eventHasFiles(event)) return;
        event.preventDefault();
        docsDragDepthRef.current = Math.max(docsDragDepthRef.current - 1, 0);
        if (docsDragDepthRef.current === 0) {
            setDragFileCount(0);
            setIsDocsDragActive(false);
        }
    };

    const handleDocsDrop = (event: React.DragEvent<HTMLDivElement>) => {
        if (!eventHasFiles(event)) return;
        event.preventDefault();
        docsDragDepthRef.current = 0;
        setDragFileCount(0);
        setIsDocsDragActive(false);
        void handleDocsUpload(event.dataTransfer.files, 'drop');
    };

    if (!selectedFilePath) {
        const parentFolderPath = getParentDirectoryPath(folderPath);
        const folderBackLabel = getDirectoryLabel(parentFolderPath);
        const folderBackHref = buildDocsHref({ folder: parentFolderPath, file: null });
        const currentFolderName = folderPath
            ? isPersonalRootPath(folderPath) ? PERSONAL_FILES_LABEL : getFileNameFromPath(folderPath)
            : 'Home';

        return (
            <ResourceIndexShell>
                <div
                    className="relative w-full min-w-0"
                    onDragEnter={handleDocsDragEnter}
                    onDragOver={handleDocsDragOver}
                    onDragLeave={handleDocsDragLeave}
                    onDrop={handleDocsDrop}
                >
                    <input
                        ref={docsUploadInputRef}
                        type="file"
                        multiple
                        className="hidden"
                        onChange={(event) => {
                            void handleDocsUpload(event.target.files, 'picker');
                            event.currentTarget.value = '';
                        }}
                    />
                    <input
                        ref={folderUpload.uploadFolderInputRef}
                        type="file"
                        multiple
                        className="hidden"
                        {...folderInputAttributes}
                        onChange={(event) => {
                            folderUpload.handleFolderInputChange(event.target.files);
                            event.currentTarget.value = '';
                        }}
                    />

                    <ResourceIndexHeader
                        title={folderPath ? currentFolderName : 'Docs'}
                        productIconKind="docs"
                        backHref={folderPath ? folderBackHref : undefined}
                        backLabel={folderPath ? folderBackLabel : undefined}
                        actions={(
                            <TooltipProvider>
                            <div className="flex shrink-0 items-center gap-1">
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="sm"
                                    className="docs-topbar-action gap-2 px-2 sm:px-3"
                                    onClick={() => setNewPageName((current) => current || 'Untitled')}
                                    aria-label="New page"
                                    title="New page"
                                >
                                    <FileText className="h-4 w-4" />
                                    <span className="docs-action-label">New page</span>
                                </Button>
                                <Tooltip>
                                    <TooltipTrigger asChild>
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            size="icon"
                                            className="h-8 w-8 rounded"
                                            disabled={isUploadingFile}
                                            onClick={() => docsUploadInputRef.current?.click()}
                                            aria-label="Upload"
                                        >
                                            {isUploadingFile ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                                        </Button>
                                    </TooltipTrigger>
                                    <TooltipContent>Upload</TooltipContent>
                                </Tooltip>
                                <Tooltip>
                                    <TooltipTrigger asChild>
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            size="icon"
                                            className="h-8 w-8 rounded"
                                            disabled={isUploadingFile || folderUpload.isFolderUploading}
                                            onClick={() => void folderUpload.handleUploadFolderClick()}
                                            aria-label="Upload folder"
                                        >
                                            {folderUpload.isFolderUploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Folder className="h-4 w-4" />}
                                        </Button>
                                    </TooltipTrigger>
                                    <TooltipContent>Upload folder</TooltipContent>
                                </Tooltip>
                                <Button
                                    type="button"
                                    size="sm"
                                    className="docs-topbar-action gap-2 px-2 sm:px-3"
                                    disabled={isCreatingFolder}
                                    onClick={() => setNewFolderName((current) => current || 'Untitled folder')}
                                    aria-label="New folder"
                                    title="New folder"
                                >
                                    {isCreatingFolder ? <Loader2 className="h-4 w-4 animate-spin" /> : <FolderPlus className="h-4 w-4" />}
                                    <span className="docs-action-label">New folder</span>
                                </Button>
                            </div>
                            </TooltipProvider>
                        )}
                    />

                    {!folderPath ? <SectionPrimer concept="file" className="mb-4" /> : null}

                    {isDocsDragActive ? (
                        <div className="state-surface-info pointer-events-none absolute inset-x-0 top-2 z-10 flex h-[calc(100vh-10rem)] min-h-80 max-h-[44rem] items-center justify-center rounded-lg border-2 border-dashed shadow-[var(--shadow-sm)]">
                            <div className="surface-panel px-4 py-3 text-center">
                                <p className="text-sm font-medium text-[var(--text-primary)]">
                                    {dragFileCount > 0
                                        ? `Release to upload ${dragFileCount} file${dragFileCount === 1 ? '' : 's'}`
                                        : 'Release to upload files'}
                                </p>
                                <p className="mt-0.5 text-xs text-[var(--text-tertiary)]">Documents are indexed for search; data and binary files are stored as-is.</p>
                            </div>
                        </div>
                    ) : null}

                    {newPageName || newFolderName ? (
                        <div className="mb-5 grid gap-3 md:grid-cols-2">
                            {newPageName ? (
                                <InlineCreateRow
                                    value={newPageName}
                                    onChange={setNewPageName}
                                    placeholder="Page name"
                                    onSubmit={handleCreateDocPage}
                                    onCancel={() => setNewPageName('')}
                                    isBusy={isUploadingFile}
                                />
                            ) : null}
                            {newFolderName ? (
                                <InlineCreateRow
                                    value={newFolderName}
                                    onChange={setNewFolderName}
                                    placeholder="Folder name"
                                    onSubmit={handleCreateDocsFolder}
                                    onCancel={() => setNewFolderName('')}
                                    isBusy={isCreatingFolder}
                                />
                            ) : null}
                        </div>
                    ) : null}

                    <div>
                        <DocsUploadProgress status={docsUploadStatus} />
                        <FolderUploadProgress
                            activeFolderUpload={folderUpload.activeFolderUpload}
                            recentFolderUpload={folderUpload.recentFolderUpload}
                            stoppingFolderUploadId={folderUpload.stoppingFolderUploadId}
                            onStop={folderUpload.handleStopFolderUpload}
                            onResume={() => void folderUpload.handleResumeFolderUpload()}
                            onDismiss={folderUpload.removeFolderUploadSession}
                            disabled={isUploadingFile || folderUpload.isFolderUploading}
                        />
                        <div className="docs-list-toolbar mb-3 flex min-w-0 flex-wrap items-end gap-3">
                            <div className="docs-list-toolbar-title min-w-0 flex-[1_1_14rem]">
                                <h2 className="text-sm font-normal text-[var(--text-primary)]">
                                    {folderPath ? 'In this folder' : 'Folders and docs'}
                                </h2>
                                <p className="mt-1 text-xs text-[var(--text-tertiary)]">
                                    {isSearchMode
                                        ? isSearchingFiles ? 'Searching inside documents' : `${searchResultItems.length} result${searchResultItems.length === 1 ? '' : 's'}`
                                        : `${docsEntries.filter((entry) => isFolder(entry)).length} folders, ${docsEntries.filter((entry) => !isFolder(entry)).length} docs`}
                                </p>
                            </div>
                            <div className="docs-list-toolbar-search relative min-w-[min(18rem,100%)] flex-[1_1_18rem] max-w-sm">
                                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--text-tertiary)]" />
                                <Input
                                    value={docsSearchQuery}
                                    onChange={(event) => setDocsSearchQuery(event.target.value)}
                                    placeholder="Search inside docs"
                                    className="form-field-control h-10 pl-9 text-sm shadow-none"
                                />
                            </div>
                        </div>

                        <div className="overflow-hidden">
                            {isLoadingDocsFiles ? (
                                <div className="flex h-28 items-center justify-center gap-2 text-sm text-[var(--text-tertiary)]">
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                    Loading docs
                                </div>
                            ) : isSearchMode ? (
                                isSearchingFiles ? (
                                    <div className="flex h-28 items-center justify-center gap-2 text-sm text-[var(--text-tertiary)]">
                                        <Loader2 className="h-4 w-4 animate-spin" />
                                        Searching docs
                                    </div>
                                ) : searchResultItems.length === 0 ? (
                                    <QuietEmptyState className="h-28 justify-center px-6 text-center">
                                        No matching passages in this folder.
                                    </QuietEmptyState>
                                ) : (
                                    searchResultItems.map((result) => (
                                        <button
                                            key={`${result.path}-${result.chunkIndex}`}
                                            type="button"
                                            onClick={() => openDocFile(result.path)}
                                            className="document-space-result-button surface-list-row custom-focus-ring items-start gap-3 px-0 py-3 text-left text-sm"
                                        >
                                            <ProductIcon kind="docs" size="sm" />
                                            <span className="min-w-0 flex-1">
                                                <span className="block truncate font-normal text-[var(--text-primary)]">{result.fileName}</span>
                                                <span className="mt-0.5 block truncate text-xs text-[var(--text-tertiary)]">{result.path}</span>
                                                {result.snippet ? (
                                                    <span className="mt-2 line-clamp-2 block text-xs leading-5 text-[var(--text-secondary)]">
                                                        {result.snippet}
                                                    </span>
                                                ) : null}
                                            </span>
                                            <span className="chip chip-pill chip-sm chip-muted mt-0.5 shrink-0 text-[var(--text-tertiary)]">
                                                {Math.round((result.score || 0) * 100)}%
                                            </span>
                                        </button>
                                    ))
                                )
                            ) : docsEntries.length === 0 ? (
                                <QuietEmptyState className="h-28 justify-center px-6 text-center">
                                    No docs here yet. Create a page, upload source material, or make a folder.
                                </QuietEmptyState>
                            ) : (
                                docsEntries.map((entry) => {
                                    const folder = isFolder(entry);
                                    const path = getFilePath(entry);
                                    const label = getDocEntryLabel(entry);
                                    return (
                                        <div
                                            key={entry.id}
                                            className="surface-list-row custom-focus-ring group h-14 min-w-0 gap-3 px-0 text-left text-sm"
                                        >
                                            <button
                                                type="button"
                                                onClick={() => folder ? openDocsFolder(path) : openDocFile(path)}
                                                className="document-space-entry-button custom-focus-ring flex min-w-0 flex-1 items-center gap-3 rounded text-left"
                                            >
                                                {folder ? (
                                                    <span className="flex h-8 w-8 shrink-0 items-center justify-center">
                                                        <ProductIcon kind="folders" size="lg" />
                                                    </span>
                                                ) : (
                                                    <span className="flex h-8 w-8 shrink-0 items-center justify-center">
                                                        <ProductIcon kind="docs" size="lg" />
                                                    </span>
                                                )}
                                                <span className="min-w-0 flex-1 truncate text-[var(--text-primary)]">{label}</span>
                                                {!folder ? (
                                                    <FileIndexStatusBadge file={entry} className="hidden md:inline-flex" />
                                                ) : null}
                                                <span className="hidden shrink-0 text-xs text-[var(--text-tertiary)] sm:inline">
                                                    {entry.updated_at ? new Date(entry.updated_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : ''}
                                                </span>
                                                <ResourceVisibilityBadge visibility={getDocEntryVisibility(entry)} resourceLabel="files" compact />
                                            </button>
                                            <ResourceActionsMenu
                                                ariaLabel={`Open actions for ${label}`}
                                                triggerClassName="h-7 w-7 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
                                            >
                                                <ResourceShareButton
                                                    value={getDocEntryVisibility(entry)}
                                                    podId={podId}
                                                    resourceType={folder ? 'folder' : 'document'}
                                                    resourceId={path}
                                                    resourceLabel="files"
                                                    resourceName={label}
                                                    shareUrl={typeof window === 'undefined' ? undefined : `${window.location.origin}${buildDocsHref({ folder: folder ? path : null, file: folder ? null : path })}`}
                                                    onChange={(visibility) => handleShareEntryVisibilityChange(entry, visibility)}
                                                    className="contents"
                                                    trigger={({ openShare, disabled }) => (
                                                        <DropdownMenuItem
                                                            disabled={disabled}
                                                            onSelect={(event) => {
                                                                event.preventDefault();
                                                                openShare();
                                                            }}
                                                        >
                                                            <Share2 className="mr-2 h-4 w-4" />
                                                            Share
                                                        </DropdownMenuItem>
                                                    )}
                                                />
                                                <DestructiveResourceActionItem onSelect={() => setEntryPendingDelete(entry)}>
                                                    Delete {folder ? 'folder' : 'file'}
                                                </DestructiveResourceActionItem>
                                            </ResourceActionsMenu>
                                        </div>
                                    );
                                })
                            )}
                        </div>
                    </div>

                    <FolderUploadConfirmDialog
                        pendingFolderUploadConfirmation={folderUpload.pendingFolderUploadConfirmation}
                        isFolderUploading={folderUpload.isFolderUploading}
                        disabled={isUploadingFile}
                        onCancel={() => folderUpload.setPendingFolderUploadConfirmation(null)}
                        onConfirm={() => void folderUpload.handleConfirmFolderUpload()}
                    />
                    <DestructiveConfirmationDialog
                        open={Boolean(entryPendingDelete)}
                        onOpenChange={(open) => {
                            if (!open) setEntryPendingDelete(null);
                        }}
                        title={`Delete ${entryPendingDelete && isFolder(entryPendingDelete) ? 'folder' : 'file'}`}
                        description={`Delete "${entryPendingDelete?.name ?? 'this item'}"?`}
                        resourceName={entryPendingDelete?.name ?? 'item'}
                        confirmationText=""
                        consequences={[
                            entryPendingDelete && isFolder(entryPendingDelete)
                                ? 'The folder and its contents will be removed.'
                                : 'The file will be removed from pod docs.',
                            'This action cannot be undone.',
                        ]}
                        confirmLabel={`Delete ${entryPendingDelete && isFolder(entryPendingDelete) ? 'folder' : 'file'}`}
                        pendingLabel="Deleting..."
                        isPending={isDeletingFile}
                        onConfirm={() => void handleDeleteEntry()}
                    />
                </div>
            </ResourceIndexShell>
        );
    }

    const selectedFileParentPath = getParentDirectoryPath(selectedFilePath);
    const selectedFileBackHref = buildDocsHref({ folder: selectedFileParentPath, file: null });
    const selectedFileBackLabel = getDirectoryLabel(selectedFileParentPath);

    return (
        <div className="h-full min-h-0 bg-[var(--bg-canvas)]">
            {!isAssistantPresentation ? (
                <ResourceDetailHeader
                    title={selectedFileName || 'Docs'}
                    productIconKind="docs"
                    backHref={selectedFileBackHref}
                    backLabel={selectedFileBackLabel}
                />
            ) : null}
            <DocumentViewer
                podId={podId}
                datastoreName={DATASTORE_NAME}
                fileId={selectedFilePath}
                backLabel={selectedFileBackLabel}
                headerMode="topbar"
                topbarBackHref={selectedFileBackHref}
                topbarBackLabel={selectedFileBackLabel}
                contextLabel={selectedFileIsPersonal ? 'Personal file' : 'Shared doc'}
                onClose={() => updateQuery({ file: null })}
                onDeleted={() => updateQuery({ file: null })}
                extraActions={selectedFileIsPersonal ? (
                    <Tooltip>
                        <TooltipTrigger asChild>
                        <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8 rounded"
                            disabled={isCopyingAcrossNamespace}
                            onClick={() => void handleCopyPersonalFileToPod()}
                            aria-label="Duplicate to pod"
                        >
                            {isCopyingAcrossNamespace ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                                <Files className="h-4 w-4" />
                            )}
                        </Button>
                        </TooltipTrigger>
                        <TooltipContent>Duplicate to pod</TooltipContent>
                    </Tooltip>
                    ) : undefined}
            />
        </div>
    );
}

function DocsUploadProgress({ status }: { status: DocsUploadStatus | null }) {
    if (!status) return null;

    const finished = status.completed + status.failed;
    const hasFailures = status.failed > 0;
    const isComplete = status.state === 'complete';

    return (
        <div className="surface-panel-muted mb-3 px-3 py-2.5">
            <div className="flex items-center justify-between gap-3">
                <div className="flex min-w-0 items-center gap-2">
                    {isComplete ? (
                        <CheckCircle2 className={cn('h-4 w-4 shrink-0', hasFailures ? 'text-[var(--state-warning)]' : 'text-[var(--state-success)]')} />
                    ) : (
                        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-[var(--state-info)]" />
                    )}
                    <p className="min-w-0 truncate text-xs font-medium text-[var(--text-primary)]">
                        {isComplete
                            ? hasFailures
                                ? `Uploaded ${status.completed}, ${status.failed} failed`
                                : `Uploaded ${status.completed} file${status.completed === 1 ? '' : 's'}`
                            : `${status.source === 'drop' ? 'Uploading dropped files' : 'Uploading'} ${finished}/${status.total}`}
                    </p>
                </div>
                <span className="shrink-0 text-xs text-[var(--text-secondary)]">
                    {finished}/{status.total}
                </span>
            </div>
            {!isComplete ? (
                <progress
                    className="mt-2 h-1.5 w-full overflow-hidden rounded-full accent-[var(--state-info)]"
                    value={finished}
                    max={status.total}
                />
            ) : null}
        </div>
    );
}

function InlineCreateRow({
    value,
    onChange,
    placeholder,
    onSubmit,
    onCancel,
    isBusy,
}: {
    value: string;
    onChange: (value: string) => void;
    placeholder: string;
    onSubmit: () => void | Promise<void>;
    onCancel: () => void;
    isBusy?: boolean;
}) {
    return (
        <div className="rounded-lg bg-[color:color-mix(in_srgb,var(--surface-2)_30%,transparent)] p-2">
            <Input
                value={value}
                onChange={(event) => onChange(event.target.value)}
                placeholder={placeholder}
                className="h-9 border-transparent bg-transparent px-2 text-sm shadow-none"
                autoFocus
                onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                        event.preventDefault();
                        void onSubmit();
                    }
                    if (event.key === 'Escape') {
                        event.preventDefault();
                        onCancel();
                    }
                }}
            />
            <div className="mt-2 flex items-center justify-end gap-2">
                <button
                    type="button"
                    onClick={onCancel}
                    className="document-space-inline-button custom-focus-ring rounded px-3 py-1.5 text-xs text-[var(--text-tertiary)] hover:bg-[var(--surface-2)]"
                >
                    Cancel
                </button>
                <button
                    type="button"
                    disabled={isBusy || !value.trim()}
                    onClick={() => void onSubmit()}
                    className={cn(
                        'document-space-inline-button custom-focus-ring inline-flex min-w-16 items-center justify-center rounded px-3 py-1.5 text-xs font-medium text-[var(--text-on-brand)] disabled:opacity-60',
                        'bg-[var(--action-primary)]'
                    )}
                >
                    {isBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : 'Create'}
                </button>
            </div>
        </div>
    );
}
