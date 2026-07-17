'use client';

import { use, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';
import { Database, Loader2, Plus } from '@/components/ui/icons';
import { toast } from 'sonner';

import { DatastoreTableView } from '@/components/data/datastore-table-view';
import { DatastoreTableSkeleton } from '@/components/data/datastore-table-skeleton';
import { DocumentViewer } from '@/components/documents/document-viewer';
import { ProductIcon } from '@/components/pod/product-icon';
import { ConceptHint } from '@/components/education/concept-hint';
import { SectionPrimer } from '@/components/education/section-primer';
import { ResourceIndexHeader, ResourceIndexShell } from '@/components/pod/resource-layout';
import { DestructiveConfirmationDialog } from '@/components/shared/destructive-confirmation-dialog';
import { EmptyState } from '@/components/shared/empty-state';
import {
    showResourceCreatedToast,
    showResourceErrorToast,
} from '@/components/shared/resource-feedback';
import { TableBuilder } from '@/components/tables/table-builder';
import { Button } from '@/components/ui/button';
import { resourceAllows } from '@/lib/authz/resource-actions';
import { Input } from '@/components/ui/input';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import {
    useCreateDatastoreFolder,
    useDatastoreFiles,
    useDeleteDatastoreFile,
    useSearchDatastoreFiles,
    useTables,
    useUploadDatastoreFile,
} from '@/lib/hooks/use-datastores';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import type { DatastoreFile } from '@/lib/types';

import {
    FILES_TAB_VALUE,
    getDatastoreFilePath,
    getFileNameFromPath,
    getParentPath,
    isFolder,
    isImageFile,
    joinPath,
    normalizeFileNamespace,
} from './_lib/file-helpers';
import type {
    DatastoreSearchResult,
    FileNamespaceMode,
    SearchResultItem,
} from './_lib/file-helpers';
import {
    MAX_FOLDER_UPLOAD_FAILURES,
    extractRelativePath,
    markRunningUploadsInterrupted,
    readFolderUploadHistory,
    upsertFolderUploadHistory,
    writeFolderUploadHistory,
} from './_lib/folder-upload';
import type {
    DirectoryHandleWithEntries,
    FolderUploadCandidate,
    FolderUploadSession,
    FolderUploadStatus,
    PendingFolderUploadConfirmation,
    RelativeFileEntry,
} from './_lib/folder-upload';
import { parseTableRouteFilters } from './_lib/route-filters';
import { DataHubHeaderActions } from './_components/data-hub-header-actions';
import { FolderUploadStatusBanner } from './_components/folder-upload-status-banner';
import { FileSearchResults } from './_components/file-search-results';
import { FileEntriesBrowser } from './_components/file-entries-browser';
import { FolderUploadConfirmDialog, NewMarkdownFileDialog } from './_components/data-hub-dialogs';
import { FolderTitleSelector, TableTitleSelector } from './_components/data-hub-title-selectors';

export default function DataHubPage({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    const { id: podId } = use(params);
    const router = useRouter();
    const pathname = usePathname();
    const searchParams = useSearchParams();
    const queryClient = useQueryClient();
    const podAccess = usePodAccess(podId);
    const canCreateTable = podAccess.can('datastore.table.create');
    const canUpdateTable = podAccess.can('datastore.table.update');
    const canDeleteTable = podAccess.can('datastore.table.delete');
    const canWriteRecords = podAccess.can('datastore.record.write');
    const canWriteFiles = podAccess.can('folder.write');
    const canDeleteFiles = podAccess.can('folder.delete');

    const { mutateAsync: uploadFile, isPending: isUploading } = useUploadDatastoreFile();
    const { mutate: searchFiles, isPending: isSearchingFiles } = useSearchDatastoreFiles();
    const { mutateAsync: createFolder, isPending: isCreatingFolder } = useCreateDatastoreFolder();
    const { mutateAsync: deleteFile, isPending: isDeletingFile } = useDeleteDatastoreFile();

    const tabParam = searchParams.get('tab');
    const createParam = searchParams.get('new');
    const folderParam = searchParams.get('folder');
    const fileParam = searchParams.get('file');
    const namespaceParam = searchParams.get('namespace');
    const searchParamsString = searchParams.toString();
    const activeFileNamespace = normalizeFileNamespace(namespaceParam);
    const filesRootPath = activeFileNamespace === 'PERSONAL' ? '/me' : '/';
    const routeFilterParams = searchParams.getAll('filter');
    const routeTableFilters = parseTableRouteFilters(routeFilterParams);
    const routeTableFiltersKey = JSON.stringify(routeFilterParams);

    const updateQuery = useCallback(
        (
            updates: Record<string, string | null>,
            options: {
                history?: 'push' | 'replace';
            } = {}
        ) => {
            const nextParams = new URLSearchParams(searchParamsString);
            Object.entries(updates).forEach(([key, value]) => {
                if (value === null || value === '') nextParams.delete(key);
                else nextParams.set(key, value);
            });

            const nextQuery = nextParams.toString();
            if (nextQuery === searchParamsString) return;

            const nextUrl = nextQuery ? `${pathname}?${nextQuery}` : pathname;
            const navigate = options.history === 'push' ? router.push : router.replace;
            navigate(nextUrl, { scroll: false });
        },
        [pathname, router, searchParamsString]
    );

    const activeDatastoreName = 'default';
    const isFilesRoute = pathname === `/pod/${podId}/files` || pathname.startsWith(`/pod/${podId}/files/`);
    const { data: tablesData, isLoading: loadingTables } = useTables(podId, activeDatastoreName);
    const tables = useMemo(() => {
        const items = tablesData?.items ?? [];
        const seenNames = new Set<string>();
        const deduped: typeof items = [];

        for (const table of items) {
            const normalizedName = table.name.trim();
            if (!normalizedName || seenNames.has(normalizedName)) continue;

            seenNames.add(normalizedName);
            deduped.push({ ...table, name: normalizedName });
        }

        return deduped;
    }, [tablesData?.items]);

    const requestedTableTab = !isFilesRoute && tabParam && tabParam !== FILES_TAB_VALUE ? tabParam : null;
    const hasRequestedTable = !!requestedTableTab && tables.some((table) => table.name === requestedTableTab);
    const activeTableName = isFilesRoute
        ? null
        : hasRequestedTable
            ? requestedTableTab
            : tables[0]?.name ?? null;
    const showingFiles = isFilesRoute;
    // Keep the tables loading state in the workbench layout so the skeleton fills
    // the pane and matches the loaded table instead of collapsing to a small box.
    const usesWorkbenchLayout = showingFiles || Boolean(activeTableName) || (!showingFiles && loadingTables);

    useEffect(() => {
        if (isFilesRoute && tabParam) {
            updateQuery({ tab: null, filter: null });
            return;
        }
        if (tabParam === FILES_TAB_VALUE) {
            updateQuery({ tab: null, filter: null });
            return;
        }
        if (requestedTableTab && !loadingTables && !hasRequestedTable) {
            updateQuery({ tab: null, filter: null });
        }
    }, [hasRequestedTable, isFilesRoute, loadingTables, requestedTableTab, tabParam, updateQuery]);

    const currentFolderPath = showingFiles ? folderParam || null : null;
    const fileQueryOptions = useMemo(
        () => ({
            directory_path: currentFolderPath ?? filesRootPath,
            limit: 200,
        }),
        [currentFolderPath, filesRootPath]
    );

    const { data: filesData, isLoading: loadingFiles, isFetching: refreshingFiles } = useDatastoreFiles(
        podId,
        showingFiles ? activeDatastoreName : undefined,
        fileQueryOptions
    );

    const [folderTrail, setFolderTrail] = useState<DatastoreFile[]>([]);
    const [searchQuery, setSearchQuery] = useState('');
    const [newFolderName, setNewFolderName] = useState('');
    const [showNewFolderInput, setShowNewFolderInput] = useState(false);
    const [filesView, setFilesView] = useState<'grid' | 'list'>('grid');
    const [isNewFileOpen, setIsNewFileOpen] = useState(false);
    const [newFileName, setNewFileName] = useState('');
    const [imagePreviewUrls, setImagePreviewUrls] = useState<Record<string, string>>({});
    const uploadInputRef = useRef<HTMLInputElement>(null);
    const uploadFolderInputRef = useRef<HTMLInputElement>(null);
    const previewUrlsRef = useRef<Record<string, string>>({});
    const pendingPreviewIdsRef = useRef<Set<string>>(new Set());
    const pendingResumeUploadSessionIdRef = useRef<string | null>(null);
    const folderUploadStopRequestsRef = useRef<Set<string>>(new Set());
    const searchRequestIdRef = useRef(0);
    const tableCreatedRef = useRef(false);
    const [folderUploadHistory, setFolderUploadHistory] = useState<FolderUploadSession[]>([]);
    const [activeFolderUploadId, setActiveFolderUploadId] = useState<string | null>(null);
    const [stoppingFolderUploadId, setStoppingFolderUploadId] = useState<string | null>(null);
    const [pendingFolderUploadConfirmation, setPendingFolderUploadConfirmation] = useState<PendingFolderUploadConfirmation | null>(null);
    const [entryPendingDelete, setEntryPendingDelete] = useState<DatastoreFile | null>(null);
    const [debouncedSearchQuery, setDebouncedSearchQuery] = useState('');
    const [searchResults, setSearchResults] = useState<DatastoreSearchResult[]>([]);

    const persistFolderUploadSession = useCallback((session: FolderUploadSession) => {
        setFolderUploadHistory((prev) => {
            const next = upsertFolderUploadHistory(prev, session);
            writeFolderUploadHistory(next);
            return next;
        });
    }, []);

    const removeFolderUploadSession = useCallback((sessionId: string) => {
        setFolderUploadHistory((prev) => {
            const next = prev.filter((session) => session.id !== sessionId);
            writeFolderUploadHistory(next);
            return next;
        });

        if (activeFolderUploadId === sessionId) {
            setActiveFolderUploadId(null);
        }
        if (pendingResumeUploadSessionIdRef.current === sessionId) {
            pendingResumeUploadSessionIdRef.current = null;
        }
    }, [activeFolderUploadId]);

    useEffect(() => {
        const history = markRunningUploadsInterrupted(readFolderUploadHistory());
        setFolderUploadHistory(history);
    }, []);

    const scopedFolderUploads = useMemo(
        () =>
            folderUploadHistory.filter(
                (session) =>
                    session.podId === podId &&
                    session.namespace === activeFileNamespace &&
                    (!activeDatastoreName || session.datastoreName === activeDatastoreName)
            ),
        [activeDatastoreName, activeFileNamespace, folderUploadHistory, podId]
    );

    const activeFolderUpload = useMemo(() => {
        const byId = activeFolderUploadId ? scopedFolderUploads.find((session) => session.id === activeFolderUploadId) : null;
        if (byId) return byId;
        return scopedFolderUploads.find((session) => session.status === 'running') ?? null;
    }, [activeFolderUploadId, scopedFolderUploads]);

    const recentFolderUpload = useMemo(() => {
        if (!scopedFolderUploads.length) return null;
        if (!activeFolderUpload) return scopedFolderUploads[0];
        return scopedFolderUploads.find((session) => session.id !== activeFolderUpload.id) ?? activeFolderUpload;
    }, [activeFolderUpload, scopedFolderUploads]);
    const isFolderUploading = activeFolderUpload?.status === 'running';
    const resumableFolderUpload = useMemo(
        () => scopedFolderUploads.find((session) => session.status === 'interrupted') ?? null,
        [scopedFolderUploads]
    );

    useEffect(() => {
        if (!isFolderUploading) return;
        const handleBeforeUnload = (event: BeforeUnloadEvent) => {
            event.preventDefault();
            event.returnValue = 'A folder upload is still in progress.';
        };

        window.addEventListener('beforeunload', handleBeforeUnload);
        return () => {
            window.removeEventListener('beforeunload', handleBeforeUnload);
        };
    }, [isFolderUploading]);

    useEffect(() => {
        if (!showingFiles || !activeDatastoreName || !currentFolderPath) {
            setFolderTrail([]);
            return;
        }

        let cancelled = false;

        const resolveTrail = async () => {
            const trail: DatastoreFile[] = [];
            const seen = new Set<string>();
            let nextPath: string | null = currentFolderPath;

            while (nextPath && !seen.has(nextPath)) {
                seen.add(nextPath);
                try {
                    const node = await getLemmaClient(podId).files.get(nextPath);
                    if (!isFolder(node)) throw new Error('Selected path is not a folder');
                    trail.unshift(node);
                    nextPath = getParentPath(node.path);
                } catch {
                    if (!cancelled) updateQuery({ folder: null });
                    return;
                }
            }

            if (!cancelled) setFolderTrail(trail);
        };

        void resolveTrail();
        return () => {
            cancelled = true;
        };
    }, [activeDatastoreName, currentFolderPath, podId, showingFiles, updateQuery]);

    const entries = useMemo(() => {
        const items = filesData?.items || [];
        const folders = items.filter(isFolder).sort((a, b) => a.name.localeCompare(b.name));
        const files = items
            .filter((item) => !isFolder(item))
            .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
        return [...folders, ...files];
    }, [filesData?.items]);

    const filteredEntries = useMemo(() => {
        const query = searchQuery.trim().toLowerCase();
        if (!query) return entries;
        return entries.filter((entry) => entry.name.toLowerCase().includes(query));
    }, [entries, searchQuery]);

    useEffect(() => {
        const timeout = window.setTimeout(() => {
            setDebouncedSearchQuery(searchQuery);
        }, 350);
        return () => {
            window.clearTimeout(timeout);
        };
    }, [searchQuery]);

    useEffect(() => {
        const query = debouncedSearchQuery.trim();

        if (!showingFiles || !activeDatastoreName) {
            searchRequestIdRef.current += 1;
            setSearchResults([]);
            return;
        }

        if (!query) {
            searchRequestIdRef.current += 1;
            setSearchResults([]);
            return;
        }

        const requestId = ++searchRequestIdRef.current;
        searchFiles(
            {
                podId,
                datastoreName: activeDatastoreName,
                query,
                limit: 80,
                search_method: 'HYBRID',
                scope_path: currentFolderPath || '/',
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
    }, [
        activeDatastoreName,
        currentFolderPath,
        debouncedSearchQuery,
        podId,
        searchFiles,
        showingFiles,
    ]);

    const isSearchMode = debouncedSearchQuery.trim().length > 0;
    const isTableBuilderOpen = createParam === 'table';

    const searchResultItems = useMemo(() => {
        if (!isSearchMode) return [];

        const byPath = new Map<string, SearchResultItem>();
        searchResults.forEach((result) => {
            const path = (result.path || result.file_id || '').trim();
            if (!path) return;

            const current = byPath.get(path);
            if (current && current.score >= result.score) return;

            byPath.set(path, {
                path,
                fileId: result.file_id,
                fileName: getFileNameFromPath(path),
                snippet: (result.content || '').trim(),
                score: result.score || 0,
                chunkIndex: result.chunk_index || 0,
            });
        });

        return Array.from(byPath.values()).sort((a, b) => b.score - a.score);
    }, [isSearchMode, searchResults]);

    useEffect(() => {
        if (!showingFiles || !activeDatastoreName) return;

        const imageEntries = filteredEntries.filter((entry) => !isFolder(entry) && isImageFile(entry));
        imageEntries.forEach((entry) => {
            const entryPath = getDatastoreFilePath(entry);
            if (imagePreviewUrls[entryPath] || pendingPreviewIdsRef.current.has(entryPath)) return;

            pendingPreviewIdsRef.current.add(entryPath);
            void getLemmaClient(podId).files
                .download(entryPath)
                .then((blob) => {
                    const url = URL.createObjectURL(blob);
                    previewUrlsRef.current[entryPath] = url;
                    setImagePreviewUrls((prev) => ({ ...prev, [entryPath]: url }));
                })
                .catch(() => {
                    // Ignore preview failures and fallback to file icon.
                })
                .finally(() => {
                    pendingPreviewIdsRef.current.delete(entryPath);
                });
        });
    }, [activeDatastoreName, filteredEntries, imagePreviewUrls, podId, showingFiles]);

    useEffect(() => {
        const previewUrls = previewUrlsRef.current;
        return () => {
            Object.values(previewUrls).forEach((url) => URL.revokeObjectURL(url));
        };
    }, []);

    const handleCreateMarkdownFile = async () => {
        if (!canWriteFiles) return;
        if (!activeDatastoreName) return;

        const rawName = newFileName.trim();
        if (!rawName) {
            toast.error('Please enter a file name');
            return;
        }

        const filename = rawName.toLowerCase().endsWith('.md') ? rawName : `${rawName}.md`;
        const heading = rawName.replace(/\.md$/i, '').trim() || 'Untitled';
        const content = `# ${heading}\n\nStart writing...`;
        const blob = new Blob([content], { type: 'text/markdown' });
        const file = new File([blob], filename, { type: 'text/markdown' });

        try {
            await uploadFile({
                podId,
                datastoreName: activeDatastoreName,
                file,
                directory_path: currentFolderPath ?? filesRootPath,
            });
            showResourceCreatedToast('File', filename);
            setIsNewFileOpen(false);
            setNewFileName('');
        } catch (error) {
            showResourceErrorToast(error, 'Failed to create file');
        }
    };

    const handleFolderOpen = (folderPath: string) => {
        updateQuery({ folder: folderPath }, { history: 'push' });
    };

    const handleBreadcrumbClick = (folderPath: string | null) => {
        updateQuery({ folder: folderPath }, { history: 'push' });
    };

    const ensureFolderPathExists = useCallback(
        async (folderPath: string) => {
            const normalized = folderPath.trim();
            if (!normalized || normalized === '/') return;

            const parentDirectoryPath = getParentPath(normalized) ?? '/';
            const folderName = normalized.replace(/\/+$/, '').split('/').pop();
            if (!folderName) return;

            try {
                await getLemmaClient(podId).files.folder.create(folderName, {
                    directoryPath: parentDirectoryPath,
                });
            } catch {
                const existingNode = await getLemmaClient(podId).files.get(normalized);
                if (!isFolder(existingNode)) {
                    throw new Error(`Path already exists and is not a folder: ${normalized}`);
                }
            }
        },
        [podId]
    );

    const handleUploadSelection = async (files: FileList | null) => {
        if (!canWriteFiles) return;
        if (!files || !activeDatastoreName || !showingFiles) return;
        const selectedFiles = Array.from(files);
        if (selectedFiles.length === 0) return;

        const results = await Promise.allSettled(
            selectedFiles.map((file) =>
                uploadFile({
                    podId,
                    datastoreName: activeDatastoreName,
                    file,
                    directory_path: currentFolderPath ?? filesRootPath,
                })
            )
        );

        const successCount = results.filter((result) => result.status === 'fulfilled').length;
        const failedCount = results.length - successCount;
        if (successCount > 0) toast.success(`Uploaded ${successCount} file${successCount > 1 ? 's' : ''}`);
        if (failedCount > 0) toast.error(`${failedCount} file${failedCount > 1 ? 's' : ''} failed to upload`);
    };

    const buildFolderUploadCandidates = useCallback(
        (entries: RelativeFileEntry[]) => {
            return entries
                .map((entry) => {
                    const relativePath = entry.relativePath
                        .split('/')
                        .map((segment) => segment.trim())
                        .filter((segment) => segment && segment !== '.' && segment !== '..')
                        .join('/');
                    if (!relativePath) return null;

                    const segments = relativePath.split('/').filter(Boolean);
                    if (segments.length < 2) return null;

                    const rootFolder = segments[0];
                    const fileName = segments[segments.length - 1];
                    const nestedFolders = segments.slice(1, -1);
                    let targetDirectoryPath = joinPath(currentFolderPath ?? filesRootPath, rootFolder);
                    nestedFolders.forEach((segment) => {
                        targetDirectoryPath = joinPath(targetDirectoryPath, segment);
                    });

                    return {
                        file: entry.file,
                        relativePath,
                        targetDirectoryPath,
                        targetFileName: fileName,
                    };
                })
                .filter((item): item is FolderUploadCandidate => Boolean(item));
        },
        [currentFolderPath, filesRootPath]
    );

    const queueFolderUploadConfirmation = useCallback(
        (candidates: FolderUploadCandidate[], resumeSession: FolderUploadSession | null = null) => {
            const rootFolderNames = Array.from(
                new Set(candidates.map((item) => item.relativePath.split('/').filter(Boolean)[0]).filter(Boolean))
            ).sort((left, right) => left.localeCompare(right));
            const completedSet = new Set(resumeSession?.completedRelativePaths || []);
            const pendingFiles = candidates.filter((candidate) => !completedSet.has(candidate.relativePath)).length;

            setPendingFolderUploadConfirmation({
                candidates,
                resumeSession,
                rootFolderNames,
                targetRootPaths: rootFolderNames.map((rootFolderName) => joinPath(currentFolderPath ?? filesRootPath, rootFolderName)),
                totalFiles: candidates.length,
                pendingFiles,
            });
        },
        [currentFolderPath, filesRootPath]
    );

    const runFolderUpload = useCallback(
        async (
            candidates: FolderUploadCandidate[],
            options?: {
                resumeSession?: FolderUploadSession | null;
            }
        ) => {
            if (!activeDatastoreName || !showingFiles) return;
            setPendingFolderUploadConfirmation(null);
            const resumeSession = options?.resumeSession ?? null;
            const rootFolderNames = Array.from(
                new Set(candidates.map((item) => item.relativePath.split('/').filter(Boolean)[0]).filter(Boolean))
            );

            if (resumeSession) {
                const selectedRoots = new Set(rootFolderNames);
                const rootMismatch = resumeSession.rootFolderNames.some((rootName) => !selectedRoots.has(rootName));
                if (rootMismatch) {
                    toast.error('Please select the same folder that was used for the interrupted upload');
                    return;
                }
            }

            const now = new Date().toISOString();
            let session: FolderUploadSession;

            if (resumeSession) {
                const completedRelativePaths = Array.from(new Set(resumeSession.completedRelativePaths || []));
                session = {
                    ...resumeSession,
                    rootFolderNames: rootFolderNames.length > 0 ? rootFolderNames : resumeSession.rootFolderNames,
                    totalFiles: Math.max(resumeSession.totalFiles || 0, candidates.length),
                    completedFiles: completedRelativePaths.length,
                    currentRelativePath: null,
                    status: 'running',
                    completedRelativePaths,
                    interruptedByUser: false,
                    updatedAt: now,
                };
            } else {
                session = {
                    id: `folder-upload-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                    podId,
                    datastoreName: activeDatastoreName,
                    namespace: activeFileNamespace,
                    startedInDirectoryPath: currentFolderPath || null,
                    rootFolderNames,
                    totalFiles: candidates.length,
                    completedFiles: 0,
                    failedFiles: 0,
                    currentRelativePath: null,
                    failures: [],
                    completedRelativePaths: [],
                    interruptedByUser: false,
                    status: 'running',
                    createdAt: now,
                    updatedAt: now,
                };
            }

            const completedSet = new Set(session.completedRelativePaths);
            const pendingCandidates = candidates.filter((candidate) => !completedSet.has(candidate.relativePath));

            if (pendingCandidates.length === 0) {
                const finalSession: FolderUploadSession = {
                    ...session,
                    status: 'completed',
                    interruptedByUser: false,
                    currentRelativePath: null,
                    failedFiles: 0,
                    updatedAt: new Date().toISOString(),
                };
                persistFolderUploadSession(finalSession);
                toast.success('Folder upload already completed');
                return;
            }

            const updateSession = (updates: Partial<FolderUploadSession>) => {
                session = {
                    ...session,
                    ...updates,
                    updatedAt: new Date().toISOString(),
                };
                persistFolderUploadSession(session);
            };
            const stopRequested = () => folderUploadStopRequestsRef.current.has(session.id);
            const finalizeAsInterrupted = () => {
                updateSession({
                    status: 'interrupted',
                    interruptedByUser: true,
                    currentRelativePath: null,
                });
                queryClient.invalidateQueries({ queryKey: ['datastore-files', podId, activeDatastoreName] });
                toast.success('Folder upload stopped. You can resume it anytime.');
            };
            const clearStopRequest = () => {
                folderUploadStopRequestsRef.current.delete(session.id);
                setStoppingFolderUploadId((current) => (current === session.id ? null : current));
            };

            persistFolderUploadSession(session);
            setActiveFolderUploadId(session.id);

            const folderPaths = new Set<string>();
            pendingCandidates.forEach((candidate) => {
                const segments = candidate.targetDirectoryPath.split('/').filter(Boolean);
                let currentPath = '/';
                segments.forEach((segment) => {
                    currentPath = joinPath(currentPath, segment);
                    folderPaths.add(currentPath);
                });
            });

            try {
                const sortedFolders = Array.from(folderPaths).sort(
                    (left, right) =>
                        left.split('/').filter(Boolean).length - right.split('/').filter(Boolean).length
                );
                for (const folderPath of sortedFolders) {
                    if (stopRequested()) {
                        finalizeAsInterrupted();
                        setActiveFolderUploadId((current) => (current === session.id ? null : current));
                        clearStopRequest();
                        return;
                    }
                    await ensureFolderPathExists(folderPath);
                }
            } catch {
                updateSession({
                    status: 'failed',
                    interruptedByUser: false,
                    currentRelativePath: null,
                    failures: ['Unable to create one or more folders before upload started.'],
                });
                setActiveFolderUploadId((current) => (current === session.id ? null : current));
                clearStopRequest();
                toast.error('Failed to create folder structure');
                return;
            }

            for (const candidate of pendingCandidates) {
                if (stopRequested()) {
                    finalizeAsInterrupted();
                    setActiveFolderUploadId((current) => (current === session.id ? null : current));
                    clearStopRequest();
                    return;
                }

                updateSession({
                    currentRelativePath: candidate.relativePath,
                });

                try {
                    await getLemmaClient(podId).files.upload(candidate.file, {
                        name: candidate.targetFileName,
                        directoryPath: candidate.targetDirectoryPath,
                    });
                    const completedRelativePaths = Array.from(
                        new Set([...session.completedRelativePaths, candidate.relativePath])
                    );
                    updateSession({
                        completedRelativePaths,
                        completedFiles: completedRelativePaths.length,
                    });
                } catch (error) {
                    const failureMessage = error instanceof Error ? error.message : 'Upload failed';
                    updateSession({
                        failedFiles: session.failedFiles + 1,
                        failures: [...session.failures, `${candidate.relativePath}: ${failureMessage}`].slice(
                            -MAX_FOLDER_UPLOAD_FAILURES
                        ),
                    });
                }
            }

            const uploadedSet = new Set(session.completedRelativePaths);
            const unresolvedFiles = candidates.filter((candidate) => !uploadedSet.has(candidate.relativePath));
            const finalFailedCount = unresolvedFiles.length;
            const finalCompletedCount = session.completedFiles;
            const finalStatus: FolderUploadStatus =
                finalFailedCount === 0 ? 'completed' : finalCompletedCount > 0 ? 'completed_with_errors' : 'failed';

            updateSession({
                status: finalStatus,
                interruptedByUser: false,
                currentRelativePath: null,
                failedFiles: finalFailedCount,
            });

            setActiveFolderUploadId((current) => (current === session.id ? null : current));
            clearStopRequest();
            queryClient.invalidateQueries({ queryKey: ['datastore-files', podId, activeDatastoreName] });

            if (finalStatus === 'completed') {
                toast.success(`Folder uploaded (${finalCompletedCount} file${finalCompletedCount > 1 ? 's' : ''})`);
            } else if (finalStatus === 'completed_with_errors') {
                toast.error(
                    `Folder upload finished with errors (${finalCompletedCount} succeeded, ${finalFailedCount} failed)`
                );
            } else {
                toast.error('Folder upload failed');
            }
        },
        [
            activeDatastoreName,
            activeFileNamespace,
            currentFolderPath,
            ensureFolderPathExists,
            persistFolderUploadSession,
            podId,
            queryClient,
            showingFiles,
        ]
    );

    const handleFolderUploadSelection = useCallback(
        async (files: FileList | null, resumeSession?: FolderUploadSession | null) => {
            if (!canWriteFiles) return;
            if (!files || !activeDatastoreName || !showingFiles) return;
            const selectedFiles = Array.from(files);
            if (selectedFiles.length === 0) return;

            const candidates = buildFolderUploadCandidates(
                selectedFiles.map((file) => ({
                    file,
                    relativePath: extractRelativePath(file),
                }))
            );

            if (candidates.length === 0) {
                toast.error('No valid files found in this folder');
                return;
            }

            queueFolderUploadConfirmation(candidates, resumeSession ?? null);
        },
        [activeDatastoreName, buildFolderUploadCandidates, canWriteFiles, queueFolderUploadConfirmation, showingFiles]
    );

    const pickDirectoryEntries = useCallback(async () => {
        const browserWindow = window as Window & {
            showDirectoryPicker?: () => Promise<FileSystemDirectoryHandle>;
        };

        if (typeof browserWindow.showDirectoryPicker !== 'function') return null;

        const directoryHandle = await browserWindow.showDirectoryPicker();
        const stack: Array<{ handle: FileSystemDirectoryHandle; segments: string[] }> = [
            { handle: directoryHandle, segments: [directoryHandle.name] },
        ];
        const entries: RelativeFileEntry[] = [];

        while (stack.length > 0) {
            const current = stack.pop();
            if (!current) continue;
            const currentHandle = current.handle as DirectoryHandleWithEntries;

            for await (const [entryName, handle] of currentHandle.entries()) {
                if (handle.kind === 'file') {
                    const file = await handle.getFile();
                    entries.push({
                        file,
                        relativePath: [...current.segments, entryName].join('/'),
                    });
                } else if (handle.kind === 'directory') {
                    stack.push({
                        handle,
                        segments: [...current.segments, entryName],
                    });
                }
            }
        }

        return entries;
    }, []);

    const handleUploadFolderClick = useCallback(async () => {
        if (!canWriteFiles) return;
        if (!activeDatastoreName || !showingFiles || isFolderUploading || isUploading) return;

        try {
            const entries = await pickDirectoryEntries();
            if (entries) {
                const candidates = buildFolderUploadCandidates(entries);
                if (candidates.length === 0) {
                    toast.error('No files found in the selected folder');
                    return;
                }
                queueFolderUploadConfirmation(candidates);
                return;
            }
        } catch (error) {
            if (error instanceof DOMException && error.name === 'AbortError') {
                return;
            }
            toast.error('Could not read folder directly, falling back to browser upload picker');
        }

        pendingResumeUploadSessionIdRef.current = null;
        uploadFolderInputRef.current?.click();
    }, [
        activeDatastoreName,
        buildFolderUploadCandidates,
        canWriteFiles,
        isFolderUploading,
        isUploading,
        pickDirectoryEntries,
        queueFolderUploadConfirmation,
        showingFiles,
    ]);

    const handleResumeFolderUpload = useCallback(async () => {
        if (!resumableFolderUpload || isFolderUploading || isUploading) return;

        try {
            const entries = await pickDirectoryEntries();
            if (entries) {
                const candidates = buildFolderUploadCandidates(entries);
                if (candidates.length === 0) {
                    toast.error('No files found in the selected folder');
                    return;
                }

                queueFolderUploadConfirmation(candidates, resumableFolderUpload);
                return;
            }
        } catch (error) {
            if (error instanceof DOMException && error.name === 'AbortError') {
                return;
            }
            toast.error('Could not read folder directly, falling back to browser upload picker');
        }

        pendingResumeUploadSessionIdRef.current = resumableFolderUpload.id;
        uploadFolderInputRef.current?.click();
    }, [
        buildFolderUploadCandidates,
        isFolderUploading,
        isUploading,
        pickDirectoryEntries,
        queueFolderUploadConfirmation,
        resumableFolderUpload,
    ]);

    const handleConfirmFolderUpload = useCallback(async () => {
        if (!canWriteFiles) return;
        if (!pendingFolderUploadConfirmation) return;
        const confirmation = pendingFolderUploadConfirmation;
        setPendingFolderUploadConfirmation(null);
        await runFolderUpload(confirmation.candidates, { resumeSession: confirmation.resumeSession });
    }, [canWriteFiles, pendingFolderUploadConfirmation, runFolderUpload]);

    const handleStopFolderUpload = useCallback(() => {
        if (!activeFolderUpload || stoppingFolderUploadId === activeFolderUpload.id) return;
        folderUploadStopRequestsRef.current.add(activeFolderUpload.id);
        setStoppingFolderUploadId(activeFolderUpload.id);
        toast.success('Stopping folder upload after the current file...');
    }, [activeFolderUpload, stoppingFolderUploadId]);

    const handleCreateFolder = async () => {
        if (!canWriteFiles) return;
        const folderName = newFolderName.trim();
        if (!folderName || !activeDatastoreName || !showingFiles) {
            toast.error('Enter a folder name');
            return;
        }

        try {
            await createFolder({
                podId,
                datastoreName: activeDatastoreName,
                name: folderName,
                directory_path: currentFolderPath ?? filesRootPath,
            });
            showResourceCreatedToast('Folder', folderName);
            setNewFolderName('');
            setShowNewFolderInput(false);
        } catch (error) {
            showResourceErrorToast(error, 'Failed to create folder');
        }
    };

    const handleDeleteEntry = async () => {
        if (!activeDatastoreName || !showingFiles || !entryPendingDelete) return;
        if (!resourceAllows(entryPendingDelete, 'folder.delete', canDeleteFiles)) return;
        const targetLabel = isFolder(entryPendingDelete) ? 'folder' : 'file';

        try {
            await deleteFile({
                podId,
                datastoreName: activeDatastoreName,
                file_path: getDatastoreFilePath(entryPendingDelete),
            });
            toast.success(`${targetLabel === 'folder' ? 'Folder' : 'File'} deleted`);
            setEntryPendingDelete(null);
        } catch {
            toast.error(`Failed to delete ${targetLabel}`);
        }
    };

    const selectedFilePath = showingFiles ? fileParam : null;
    const handleDownload = async (entry: DatastoreFile) => {
        if (!activeDatastoreName || isFolder(entry) || !showingFiles) return;

        try {
            const blob = await getLemmaClient(podId).files.download(getDatastoreFilePath(entry));
            const url = URL.createObjectURL(blob);
            const anchor = document.createElement('a');
            anchor.href = url;
            anchor.download = entry.name;
            document.body.appendChild(anchor);
            anchor.click();
            document.body.removeChild(anchor);
            URL.revokeObjectURL(url);
        } catch {
            toast.error('Failed to download file');
        }
    };

    const handleDownloadByPath = async (filePath: string, fileName?: string) => {
        if (!activeDatastoreName || !showingFiles) return;

        try {
            const blob = await getLemmaClient(podId).files.download(filePath);
            const url = URL.createObjectURL(blob);
            const anchor = document.createElement('a');
            anchor.href = url;
            anchor.download = fileName || getFileNameFromPath(filePath);
            document.body.appendChild(anchor);
            anchor.click();
            document.body.removeChild(anchor);
            URL.revokeObjectURL(url);
        } catch {
            toast.error('Failed to download file');
        }
    };

    const handleOpenFile = (entry: DatastoreFile) => {
        if (!activeDatastoreName || isFolder(entry) || !showingFiles) return;
        updateQuery({ file: getDatastoreFilePath(entry) }, { history: 'push' });
    };

    const handleCloseTableBuilder = () => {
        if (tableCreatedRef.current) {
            tableCreatedRef.current = false;
            return;
        }

        updateQuery({ new: null });
    };

    const handleTableCreated = (tableName?: string) => {
        tableCreatedRef.current = true;
        updateQuery({ tab: tableName || null, new: null }, { history: 'replace' });
        showResourceCreatedToast('Table', tableName);
    };

    const handleFileNamespaceChange = (namespace: FileNamespaceMode) => {
        if (namespace === activeFileNamespace) return;
        setSearchQuery('');
        setSearchResults([]);
        updateQuery(
            {
                namespace: namespace === 'PERSONAL' ? null : namespace,
                folder: null,
                file: null,
            },
            { history: 'replace' }
        );
    };

    const currentFolderLabel = folderTrail.length > 0 ? folderTrail[folderTrail.length - 1]?.name || 'Folder' : 'Home';

    return (
        <ResourceIndexShell
            mode={usesWorkbenchLayout ? 'workbench' : 'ledger'}
            className={usesWorkbenchLayout ? 'data-workspace-shell' : undefined}
        >
            <ResourceIndexHeader
                className="mb-4"
                productIconKind={showingFiles ? 'docs' : 'data'}
                title={showingFiles ? (
                    <FolderTitleSelector
                        selectedFilePath={selectedFilePath}
                        currentFolderLabel={currentFolderLabel}
                        currentFolderPath={currentFolderPath}
                        folderTrail={folderTrail}
                        canWriteFiles={canWriteFiles}
                        activeDatastoreName={activeDatastoreName}
                        onBreadcrumbClick={handleBreadcrumbClick}
                        onNewFolder={() => setShowNewFolderInput(true)}
                        onNewFile={() => setIsNewFileOpen(true)}
                    />
                ) : activeTableName ? (
                    <TableTitleSelector
                        loadingTables={loadingTables}
                        activeTableName={activeTableName}
                        tables={tables}
                        canCreateTable={canCreateTable}
                        onSelectTable={(tableName) => updateQuery({ tab: tableName, filter: null }, { history: 'push' })}
                        onNewTable={() => updateQuery({ new: 'table' }, { history: 'push' })}
                    />
                ) : 'Data'}
                meta={<ConceptHint concept={showingFiles ? 'file' : 'table'} />}
                actions={(
                    <DataHubHeaderActions
                        showingFiles={showingFiles}
                        activeFileNamespace={activeFileNamespace}
                        onNamespaceChange={handleFileNamespaceChange}
                        filesView={filesView}
                        onFilesViewChange={setFilesView}
                        uploadInputRef={uploadInputRef}
                        uploadFolderInputRef={uploadFolderInputRef}
                        onUploadSelection={(files) => void handleUploadSelection(files)}
                        onFolderInputChange={(files) => {
                            const resumeSessionId = pendingResumeUploadSessionIdRef.current;
                            pendingResumeUploadSessionIdRef.current = null;
                            const resumeSession = resumeSessionId
                                ? folderUploadHistory.find((session) => session.id === resumeSessionId) ?? null
                                : null;
                            void handleFolderUploadSelection(files, resumeSession);
                        }}
                        canWriteFiles={canWriteFiles}
                        canCreateTable={canCreateTable}
                        isUploading={isUploading}
                        isFolderUploading={isFolderUploading}
                        activeDatastoreName={activeDatastoreName}
                        onUploadFilesClick={() => uploadInputRef.current?.click()}
                        onUploadFolderClick={() => void handleUploadFolderClick()}
                        onToggleNewFolderInput={() => setShowNewFolderInput((prev) => !prev)}
                        onOpenNewFile={() => setIsNewFileOpen(true)}
                        onNewTable={() => updateQuery({ new: 'table' }, { history: 'push' })}
                    />
                )}
            />
            {!usesWorkbenchLayout ? <SectionPrimer concept="table" className="mb-4" /> : null}
            <div className={usesWorkbenchLayout ? 'flex min-h-0 min-w-0 flex-1 flex-col' : undefined}>
                {showingFiles ? (
                    selectedFilePath && activeDatastoreName ? (
                        <div className="surface-card flex min-h-0 flex-1 flex-col border-0 overflow-hidden p-0">
                            <DocumentViewer
                                podId={podId}
                                datastoreName={activeDatastoreName}
                                fileId={selectedFilePath}
                                onClose={() => updateQuery({ file: null }, { history: 'push' })}
                                onDeleted={() => updateQuery({ file: null }, { history: 'replace' })}
                                canWrite={canWriteFiles}
                                canDelete={canDeleteFiles}
                            />
                        </div>
                    ) : (
                        <>
                            <div className="mb-1 flex min-w-0 items-center border-b border-[color:color-mix(in_srgb,var(--border-subtle)_54%,transparent)] pb-3">
                                <Input
                                    value={searchQuery}
                                    onChange={(event) => setSearchQuery(event.target.value)}
                                    placeholder={`Search ${activeFileNamespace === 'PERSONAL' ? 'personal' : 'pod'} files`}
                                    className="h-8 w-full max-w-[20rem] border-[color:color-mix(in_srgb,var(--border-subtle)_72%,transparent)] bg-transparent text-sm shadow-none"
                                />
                            </div>

                            <div className="flex min-h-0 flex-1 flex-col pb-2">
                            {showNewFolderInput && (
                                <div className="mb-3 flex items-center gap-2 rounded-lg bg-[var(--bg-subtle)] p-2.5">
                                    <Input
                                        value={newFolderName}
                                        onChange={(event) => setNewFolderName(event.target.value)}
                                        placeholder="Folder name"
                                        className="h-8"
                                        onKeyDown={(event) => {
                                            if (event.key === 'Enter') {
                                                event.preventDefault();
                                                void handleCreateFolder();
                                            }
                                        }}
                                    />
                                    <Button size="sm" onClick={() => void handleCreateFolder()} disabled={isCreatingFolder}>
                                        {isCreatingFolder ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Create'}
                                    </Button>
                                    <Button
                                        variant="ghost"
                                        size="sm"
                                        onClick={() => {
                                            setShowNewFolderInput(false);
                                            setNewFolderName('');
                                        }}
                                    >
                                        Cancel
                                    </Button>
                                </div>
                            )}

                            <FolderUploadStatusBanner
                                activeFolderUpload={activeFolderUpload}
                                recentFolderUpload={recentFolderUpload}
                                stoppingFolderUploadId={stoppingFolderUploadId}
                                isFolderUploading={isFolderUploading}
                                isUploading={isUploading}
                                onStopFolderUpload={handleStopFolderUpload}
                                onDismissFolderUpload={removeFolderUploadSession}
                                onResumeFolderUpload={() => void handleResumeFolderUpload()}
                            />

                            <div className="min-h-0 flex-1 overflow-auto">
                                {isSearchMode ? (
                                    <FileSearchResults
                                        currentFolderPath={currentFolderPath}
                                        isSearchingFiles={isSearchingFiles}
                                        searchResultItems={searchResultItems}
                                        onOpenSearchResult={(path) => updateQuery({ file: path }, { history: 'push' })}
                                        onDownloadByPath={(filePath, fileName) => void handleDownloadByPath(filePath, fileName)}
                                    />
                                ) : (
                                    <>
                                        {(loadingFiles || refreshingFiles) && (
                                            <div className="flex h-full min-h-[200px] items-center justify-center py-12">
                                                <Loader2 className="h-6 w-6 animate-spin text-[var(--text-tertiary)]" />
                                            </div>
                                        )}

                                        {!loadingFiles && !refreshingFiles && filteredEntries.length === 0 && (
                                            <EmptyState
                                                variant="compact"
                                                icon={<ProductIcon kind="files" size="sm" />}
                                                title={searchQuery ? 'No files or folders match this search' : 'This folder is empty'}
                                                description={searchQuery ? 'Try another search term.' : 'Upload files or create a folder when this pod needs source material.'}
                                            />
                                        )}

                                        {!loadingFiles && !refreshingFiles && filteredEntries.length > 0 && (
                                            <FileEntriesBrowser
                                                filesView={filesView}
                                                filteredEntries={filteredEntries}
                                                imagePreviewUrls={imagePreviewUrls}
                                                activeFileNamespace={activeFileNamespace}
                                                canDeleteFiles={canDeleteFiles}
                                                isDeletingFile={isDeletingFile}
                                                onFolderOpen={handleFolderOpen}
                                                onOpenFile={(entry) => void handleOpenFile(entry)}
                                                onDownload={(entry) => void handleDownload(entry)}
                                                onRequestDelete={setEntryPendingDelete}
                                            />
                                        )}
                                    </>
                                )}
                            </div>
                            </div>
                        </>
                    )
                ) : loadingTables ? (
                    <DatastoreTableSkeleton />
                ) : activeTableName ? (
                    <DatastoreTableView
                        key={`${activeTableName}:${routeTableFiltersKey}`}
                        podId={podId}
                        datastoreName={activeDatastoreName!}
                        tableName={activeTableName}
                        embedded
                        onTableDeleted={() => updateQuery({ tab: null })}
                        canWriteRecords={canWriteRecords}
                        canUpdateTable={canUpdateTable}
                        canDeleteTable={canDeleteTable}
                        initialFilters={routeTableFilters}
                        headerLeft={({ totalRecords }) => (
                            <div className="text-xs text-[var(--text-tertiary)]">
                                {totalRecords} record{totalRecords === 1 ? '' : 's'}
                            </div>
                        )}
                    />
                ) : (
                    <EmptyState
                        variant="panel"
                        icon={<Database className="h-5 w-5" />}
                        title="No tables yet"
                        description={canCreateTable
                            ? "Create a table when this pod needs structured records."
                            : "No tables are available to you yet."}
                        action={canCreateTable ? (
                            <Button
                                size="sm"
                                className="gap-2"
                                onClick={() => updateQuery({ new: 'table' }, { history: 'push' })}
                            >
                                <Plus className="h-4 w-4" />
                                New table
                            </Button>
                        ) : undefined}
                    />
                )}
            </div>

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
                        : 'The file will be removed from this datastore.',
                    'This action cannot be undone.',
                ]}
                confirmLabel={`Delete ${entryPendingDelete && isFolder(entryPendingDelete) ? 'folder' : 'file'}`}
                pendingLabel="Deleting..."
                isPending={isDeletingFile}
                onConfirm={() => void handleDeleteEntry()}
            />

            <FolderUploadConfirmDialog
                pendingFolderUploadConfirmation={pendingFolderUploadConfirmation}
                isFolderUploading={isFolderUploading}
                isUploading={isUploading}
                onOpenChange={(open) => {
                    if (!open) setPendingFolderUploadConfirmation(null);
                }}
                onConfirm={() => void handleConfirmFolderUpload()}
                onCancel={() => setPendingFolderUploadConfirmation(null)}
            />

            <NewMarkdownFileDialog
                open={isNewFileOpen}
                onOpenChange={setIsNewFileOpen}
                newFileName={newFileName}
                onNewFileNameChange={setNewFileName}
                isUploading={isUploading}
                onCreate={() => void handleCreateMarkdownFile()}
                onCancel={() => setIsNewFileOpen(false)}
            />

            {canCreateTable && isTableBuilderOpen && (
                <TableBuilder
                    podId={podId}
                    datastoreName={activeDatastoreName}
                    onClose={handleCloseTableBuilder}
                    onSuccess={handleTableCreated}
                />
            )}
        </ResourceIndexShell>
    );
}
