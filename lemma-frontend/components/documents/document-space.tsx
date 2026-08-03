'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
    CheckCircle2,
    Files,
    FileText,
    Folder,
    FolderPlus,
    Search,
    Share2,
    Sparkles,
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
import { ResourceHeader, ResourceIndexShell } from '@/components/pod/resource-layout';
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
import { DocsSectionSwitcher } from '@/components/documents/docs-section-switcher';
import { SkillEntriesList } from '@/components/documents/skill-entries-list';
import {
    docSection,
    docSectionForPath,
    isPersonalPath,
    isSectionRoot,
    type DocSectionId,
} from '@/lib/files/doc-sections';
import {
    SKILLS_ROOT,
    SKILL_MANIFEST_NAME,
    buildSkillScaffold,
    isSkillManifestPath,
    isSkillsRootPath,
    skillNameFromPath,
    skillFolderPath,
    skillManifestPath,
    suggestSkillName,
    validateSkillName,
} from '@/lib/files/skills';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import type { DatastoreFile } from '@/lib/types';
import type { FileSearchResultSchema } from 'lemma-sdk';
import { cn } from '@/lib/utils';
import { StepLoader } from '@/components/brand/loader';
import { Skeleton } from '@/components/shared/loading';

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

/** Names vary, so the placeholders do — equal bars read as a table, not a list. */
const DOCS_ROW_WIDTHS = ['w-2/5', 'w-1/3', 'w-1/2', 'w-5/12', 'w-1/3', 'w-2/5'];

/**
 * The docs list, waiting.
 *
 * Built from the settled row — `surface-list-row`, `h-11`, the 5×5 icon slot,
 * and the fixed trailing columns — so the panel holds its height and the columns
 * do not step sideways when the real rows land.
 */
function DocsRowsSkeleton({ rows = 6 }: { rows?: number }) {
    return (
        <div role="status" aria-label="Loading docs">
            {DOCS_ROW_WIDTHS.slice(0, rows).map((width, index) => (
                <div key={index} className="surface-list-row h-11 min-w-0 gap-2 px-3" data-skeleton="true">
                    <div className="flex min-w-0 flex-1 items-center gap-2.5">
                        <span className="flex h-5 w-5 shrink-0 items-center justify-center">
                            <Skeleton shape="block" className="h-4 w-4" />
                        </span>
                        <Skeleton className={cn('h-3', width)} />
                        <span className="ml-auto hidden w-10 shrink-0 lg:block">
                            <Skeleton className="h-2.5 w-full" />
                        </span>
                        <span className="hidden w-16 shrink-0 md:block">
                            <Skeleton className="h-2.5 w-full" />
                        </span>
                    </div>
                </div>
            ))}
        </div>
    );
}

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

/** A section root is titled by its section; everything else by its folder name. */
function getDirectoryLabel(path: string | null | undefined): string {
    if (!path || path === '/') return 'Docs';
    if (isSectionRoot(path)) return docSection(docSectionForPath(path)).title;
    return getFileNameFromPath(path);
}

function isFolder(file: DatastoreFile): boolean {
    return file.kind === 'FOLDER';
}

function getFilePath(file: DatastoreFile): string {
    return file.path || file.id;
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

/**
 * What the typed name will actually become. Skill names are constrained — the
 * runtime only loads lowercase-and-hyphens — so showing the slug up front beats
 * rejecting the input after the fact.
 */
function skillNameHint(input: string): string {
    const name = suggestSkillName(input);
    return validateSkillName(name) || `Creates ${skillManifestPath(name)}`;
}

function isMarkdownName(value: string): boolean {
    return /\.mdx?$/i.test(value) || /\.markdown$/i.test(value);
}

function pageFileName(rawName: string): string {
    const clean = rawName.trim();
    if (!clean) return '';
    return isMarkdownName(clean) ? clean : `${clean}.md`;
}

/** Extension, upper-cased — the one word that says what a row is. */
function getFileKindLabel(file: DatastoreFile): string {
    const extension = /\.([a-z0-9]{1,8})$/i.exec(file.name)?.[1];
    if (!extension) return '';
    return extension.toUpperCase();
}

function formatFileSize(bytes: number | null | undefined): string {
    if (!bytes || bytes < 0) return '';
    if (bytes < 1024) return `${bytes} B`;
    const units = ['KB', 'MB', 'GB', 'TB'];
    let value = bytes / 1024;
    let unitIndex = 0;
    while (value >= 1024 && unitIndex < units.length - 1) {
        value /= 1024;
        unitIndex += 1;
    }
    return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unitIndex]}`;
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
    const [isPromotingToPodDocs, setIsPromotingToPodDocs] = useState(false);
    const [isDocsDragActive, setIsDocsDragActive] = useState(false);
    const [dragFileCount, setDragFileCount] = useState(0);
    const [docsUploadStatus, setDocsUploadStatus] = useState<DocsUploadStatus | null>(null);
    const [newPageName, setNewPageName] = useState('');
    const [newSkillName, setNewSkillName] = useState('');
    const [newFolderName, setNewFolderName] = useState('');
    const [docsSearchQuery, setDocsSearchQuery] = useState('');
    const [debouncedDocsSearchQuery, setDebouncedDocsSearchQuery] = useState('');
    const [searchResults, setSearchResults] = useState<DocSearchResult[]>([]);
    const [entryPendingDelete, setEntryPendingDelete] = useState<DatastoreFile | null>(null);

    const folderPath = searchParams.get('folder');
    const isAssistantPresentation = Boolean(searchParams.get('assistantConversationId') || searchParams.get('conversationId'));
    const currentDirectoryPath = activeDirectoryPath(folderPath);
    const activeSectionId = docSectionForPath(currentDirectoryPath);
    const activeSection = docSection(activeSectionId);
    /** Inside `/skills` the thing you make is a skill, so the action says so. */
    const isSkillsFolder = isSkillsRootPath(currentDirectoryPath);
    const isSkillsSection = activeSectionId === 'SKILLS';
    const isPersonalSection = activeSectionId === 'PERSONAL';
    const selectedFilePath = searchParams.get('file');
    const selectedFileName = selectedFilePath ? getFileNameFromPath(selectedFilePath) : '';
    const selectedFileIsPersonal = isPersonalPath(selectedFilePath);
    /**
     * Share links address a file by id; this view works in paths. Resolve the id
     * once on arrival and swap it for the canonical path.
     *
     * Links used to carry the path directly, which quietly broke for anything
     * under `/me`: that prefix is an alias for the *reader's* own folder, so a
     * recipient opening `/me/notes.md` got their own file — a 404 that reads as
     * "deleted", or, on a name collision, the wrong document with no error at
     * all. An id means the same file for everyone.
     */
    const sharedFileId = searchParams.get('fileId');
    const shouldResolveSharedFile = Boolean(sharedFileId) && !selectedFilePath;
    const { data: sharedFile, error: sharedFileError } = useQuery({
        queryKey: ['datastore-file-by-id', podId, sharedFileId],
        queryFn: () => getLemmaClient(podId).files.getById(sharedFileId!),
        enabled: shouldResolveSharedFile,
        retry: false,
    });
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
        // Skills and personal files answer to the switcher now. Leaving their
        // folders in the pod listing as well would offer two doors into one room
        // — and the folder row is the door that describes them worst.
        const items = (docsFilesData?.items || []).filter((entry) => !isSectionRoot(getFilePath(entry)));

        // Personal files are a drafting space, not a library: what you touched
        // last is what you came back for.
        if (isPersonalSection) {
            return [...items].sort((left, right) => {
                if (isFolder(left) !== isFolder(right)) return isFolder(left) ? -1 : 1;
                const leftTime = left.updated_at ? Date.parse(left.updated_at) : 0;
                const rightTime = right.updated_at ? Date.parse(right.updated_at) : 0;
                if (leftTime !== rightTime) return rightTime - leftTime;
                return left.name.localeCompare(right.name);
            });
        }

        return [...items].sort((left, right) => {
            if (isFolder(left) !== isFolder(right)) return isFolder(left) ? -1 : 1;
            return left.name.localeCompare(right.name);
        });
    }, [docsFilesData?.items, isPersonalSection]);

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
    /** Settled, browsing, and nothing in this folder — the one state that gets a single region. */
    const isFolderBlank = !isSearchMode && !isLoadingDocsFiles && docsEntries.length === 0;

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

    /**
     * The URL to hand to someone else — as opposed to the one this view
     * navigates with.
     *
     * A file is addressed by id so the link means the same file for every
     * reader and survives a rename. Folders have no id-addressable read
     * endpoint yet, so they still carry a path; that is safe for pod folders
     * and is why personal ones offer promotion instead of a share link.
     *
     * Built from scratch rather than from the current query, so a share never
     * carries along whatever search or filter happened to be open.
     */
    const buildShareableHref = (entry: DatastoreFile): string | undefined => {
        if (typeof window === 'undefined') return undefined;
        const params = new URLSearchParams();
        if (isFolder(entry)) params.set('folder', getFilePath(entry));
        else params.set('fileId', entry.id);
        return `${window.location.origin}${pathname}?${params.toString()}`;
    };

    // Swap a resolved share id for the path this view navigates in, and drop the
    // id so a refresh does not re-resolve it. `replace`, not `push`: the id and
    // the path are the same destination, and Back should leave the document
    // rather than bounce between two spellings of it.
    useEffect(() => {
        if (!sharedFile?.path) return;
        const nextParams = new URLSearchParams(searchParams.toString());
        nextParams.delete('fileId');
        nextParams.set('file', sharedFile.path);
        router.replace(`${pathname}?${nextParams.toString()}`, { scroll: false });
    }, [pathname, router, searchParams, sharedFile]);

    /**
     * The one gesture personal files need that pod docs do not: this stops
     * being mine and starts being ours.
     *
     * A move, not a copy. This is the only way to share a personal file — `/me`
     * is an alias for whoever is reading, so a personal file has no address that
     * means the same thing to anyone else — and a copy would leave the original
     * behind as the one the owner keeps editing, while everyone they shared with
     * reads a snapshot that silently stops matching. One file, one address.
     *
     * `new_path` and `visibility` move together: the path alone would leave the
     * file PERSONAL at a pod address, readable to nobody but absent from no
     * listing either.
     */
    const handlePromoteToPodDocs = async (filePath: string) => {
        if (!isPersonalPath(filePath)) return;
        setIsPromotingToPodDocs(true);
        const filename = getFileNameFromPath(filePath);
        const destination = `/${filename}`;
        try {
            await getLemmaClient(podId).files.update(filePath, {
                newPath: destination,
                visibility: 'POD',
            });
            queryClient.invalidateQueries({ queryKey: ['datastore-files', podId, DATASTORE_NAME] });
            // The path this view navigates by just changed underneath it. Follow
            // the file rather than leaving the viewer pointed at a path that no
            // longer resolves.
            if (selectedFilePath === filePath) {
                updateQuery({ file: destination });
            }
            toast.success('Moved to pod docs');
        } catch (error) {
            // A name already taken in pod docs is the one failure the owner can
            // actually act on, so it says so instead of "something went wrong".
            const conflict = String((error as { message?: string })?.message || '')
                .toLowerCase()
                .includes('already');
            toast.error(
                conflict
                    ? `Pod docs already has a file named ${filename}. Rename it first.`
                    : 'Failed to move document to pod docs',
            );
        } finally {
            setIsPromotingToPodDocs(false);
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

    /**
     * A skill is a folder plus a `SKILL.md` whose frontmatter names it — and the
     * runtime refuses the skill if the two names disagree. So the name is typed
     * once here and written to both places, rather than left for someone to
     * keep in sync by hand.
     */
    const handleCreateSkill = async () => {
        const name = suggestSkillName(newSkillName);
        const problem = validateSkillName(name);
        if (problem) {
            toast.error(problem);
            return;
        }

        if (docsEntries.some((entry) => isFolder(entry) && entry.name === name)) {
            toast.error(`A skill named "${name}" already exists`);
            return;
        }

        const manifest = new File(
            [buildSkillScaffold(name, '')],
            SKILL_MANIFEST_NAME,
            { type: 'text/markdown' }
        );

        try {
            await createFolder({
                podId,
                datastoreName: DATASTORE_NAME,
                name,
                directory_path: SKILLS_ROOT,
            });
            await uploadFile({
                podId,
                datastoreName: DATASTORE_NAME,
                file: manifest,
                directory_path: skillFolderPath(name),
            });
            setNewSkillName('');
            openDocFile(skillManifestPath(name));
            toast.success('Skill created');
        } catch (error) {
            toast.error(error instanceof Error ? error.message : 'Failed to create skill');
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

    // A share link is still resolving, or names something this reader cannot
    // open. Either way the folder listing behind it is not the answer — showing
    // it would look like the document simply was not there.
    if (shouldResolveSharedFile) {
        return (
            <div className="flex h-full items-center justify-center px-4">
                <div className="max-w-md text-center">
                    {sharedFileError ? (
                        <>
                            <h2 className="mb-2 font-display text-lg font-semibold text-[var(--text-primary)]">
                                This document isn&apos;t available to you
                            </h2>
                            <p className="text-sm text-[var(--text-secondary)]">
                                It may have been deleted, or you may not have access to it. Ask
                                whoever shared the link to check.
                            </p>
                        </>
                    ) : (
                        <p className="text-sm text-[var(--text-secondary)]">Opening document…</p>
                    )}
                </div>
            </div>
        );
    }

    if (!selectedFilePath) {
        const parentFolderPath = getParentDirectoryPath(folderPath);
        const folderBackLabel = getDirectoryLabel(parentFolderPath);
        const folderBackHref = buildDocsHref({ folder: parentFolderPath, file: null });
        // Inside a section root the heading is the section's own title; deeper
        // in, it is the folder you opened.
        const isSectionHome = !folderPath || isSectionRoot(folderPath);
        const currentFolderName = isSectionHome ? activeSection.title : getFileNameFromPath(folderPath);
        const openSection = (sectionId: DocSectionId) => openDocsFolder(docSection(sectionId).root);

        const folderCount = docsEntries.filter((entry) => isFolder(entry)).length;
        const fileCount = docsEntries.length - folderCount;
        /** A skill is a folder, but counting folders is not what you came to know. */
        const sectionListLabel = isSkillsSection ? 'Skills' : isPersonalSection ? 'Recent first' : 'Folders and docs';
        const sectionCountLabel = isSkillsSection
            ? `${folderCount} skill${folderCount === 1 ? '' : 's'}`
            : `${folderCount} folders · ${fileCount} docs`;
        /** Skill rows lead with the description; every other listing is a file list. */
        const showsSkillRows = isSkillsFolder && !isSearchMode;

        const indexingNote = 'Documents are indexed for search; data and binary files are stored as-is.';
        const dropTargetHint = isSkillsSection
            ? 'Drop a skill folder here, or use New skill to write one. Each skill needs a SKILL.md naming and describing it.'
            : isPersonalSection
                ? `Only you can see what lands here. Share one to the pod from its row when it is ready. ${indexingNote}`
                : isFolderBlank
                    ? `You can also create a page or a folder from the top of this pane. ${indexingNote}`
                    : indexingNote;

        const renderEntryActions = (entry: DatastoreFile) => {
            const folder = isFolder(entry);
            const path = getFilePath(entry);
            /**
             * Personal files promote; they do not share.
             *
             * `/me` is an alias resolved against whoever is reading, so a
             * personal file has no address that means the same thing to anyone
             * else — a share link would resolve to the recipient's own folder.
             * Its grants are unreachable for the same reason: they key on the
             * stored `/{user_id}/…` path, which the dialog never sees. And a
             * visibility flip alone would leave the file authorized but absent
             * from every listing, since the personal root is synthetic.
             *
             * Promotion is the honest move, and the one the namespace exists to
             * make: this stops being mine and starts being ours.
             */
            const isPersonal = isPersonalPath(path);
            return (
                <ResourceActionsMenu
                    ariaLabel={`Open actions for ${entry.name}`}
                    triggerClassName="h-7 w-7 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
                >
                    {isPersonal ? (
                        !folder ? (
                            <DropdownMenuItem
                                disabled={isPromotingToPodDocs}
                                onSelect={(event) => {
                                    event.preventDefault();
                                    void handlePromoteToPodDocs(path);
                                }}
                            >
                                <Files className="mr-2 h-4 w-4" />
                                Share to pod docs
                            </DropdownMenuItem>
                        ) : (
                            // A personal *folder* cannot promote the way a file
                            // can: moving it would carry its children to pod
                            // paths while they stay PERSONAL — authorized to
                            // nobody, listed for nobody. Say why, rather than
                            // leaving a menu whose only entry is Delete.
                            <DropdownMenuItem disabled>
                                <Files className="mr-2 h-4 w-4" />
                                Share files individually
                            </DropdownMenuItem>
                        )
                    ) : (
                        <ResourceShareButton
                            value={getDocEntryVisibility(entry)}
                            podId={podId}
                            resourceType={folder ? 'folder' : 'document'}
                            resourceId={path}
                            resourceLabel="files"
                            resourceName={entry.name}
                            shareUrl={buildShareableHref(entry)}
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
                    )}
                    <DestructiveResourceActionItem onSelect={() => setEntryPendingDelete(entry)}>
                        Delete {folder ? 'folder' : 'file'}
                    </DestructiveResourceActionItem>
                </ResourceActionsMenu>
            );
        };

        const renderDocRow = (entry: DatastoreFile) => {
            const folder = isFolder(entry);
            const path = getFilePath(entry);
            const kindLabel = folder ? '' : getFileKindLabel(entry);
            const sizeLabel = folder ? '' : formatFileSize(entry.size_bytes);
            return (
                <div
                    key={entry.id}
                    className="surface-list-row custom-focus-ring group h-11 min-w-0 gap-2 px-3 text-left text-sm"
                >
                    <button
                        type="button"
                        onClick={() => folder ? openDocsFolder(path) : openDocFile(path)}
                        className="document-space-entry-button custom-focus-ring flex min-w-0 flex-1 items-center gap-2.5 rounded text-left"
                    >
                        <span className="flex h-5 w-5 shrink-0 items-center justify-center">
                            <ProductIcon kind={folder ? 'folders' : 'docs'} size="sm" />
                        </span>
                        <span className="min-w-0 flex-1 truncate text-[var(--text-primary)]">{entry.name}</span>
                        {/* What a row is, how big it is, whether the pod can read
                            it — the three facts that were missing while the row
                            still spent 56px.

                            The badge goes first because it is the only
                            variable-width thing here: with the name absorbing the
                            slack and every column after the badge a fixed width,
                            type / size / date line up down the list instead of
                            stepping left and right with each status label. */}
                        {!folder ? (
                            <FileIndexStatusBadge file={entry} className="hidden md:inline-flex" />
                        ) : null}
                        {/* Rendered even when empty: a folder row with no type or
                            size still has to hold the columns open, or the dates
                            zig-zag. */}
                        <span className="type-eyebrow-sm hidden w-10 shrink-0 text-right lg:inline">
                            {kindLabel}
                        </span>
                        <span className="hidden w-16 shrink-0 text-right text-xs tabular-nums text-[var(--text-tertiary)] md:inline">
                            {sizeLabel}
                        </span>
                        <span className="hidden w-14 shrink-0 text-right text-xs text-[var(--text-tertiary)] sm:inline">
                            {entry.updated_at ? new Date(entry.updated_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : ''}
                        </span>
                        {/* Inside Personal every row is personal, so the badge is
                            a column of the same word. It earns its place in the
                            pod listing, where visibility actually varies. */}
                        {isPersonalSection ? null : (
                            <ResourceVisibilityBadge visibility={getDocEntryVisibility(entry)} resourceLabel="files" compact />
                        )}
                    </button>
                    {renderEntryActions(entry)}
                </div>
            );
        };

        return (
            <ResourceIndexShell className="flex flex-col">
                <div
                    className="relative flex min-h-0 w-full min-w-0 flex-1 flex-col"
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

                    <ResourceHeader
                        title={currentFolderName}
                        backHref={isSectionHome ? undefined : folderBackHref}
                        backLabel={isSectionHome ? undefined : folderBackLabel}
                        actions={(
                            <TooltipProvider>
                            <div className="flex shrink-0 items-center gap-1">
                                {isSkillsFolder ? (
                                    <Button
                                        type="button"
                                        variant="secondary"
                                        size="sm"
                                        className="docs-topbar-action gap-2 px-2 sm:px-3"
                                        disabled={isCreatingFolder || isUploadingFile}
                                        onClick={() => setNewSkillName((current) => current || 'untitled-skill')}
                                        aria-label="New skill"
                                        title="New skill"
                                    >
                                        <Sparkles className="h-4 w-4" />
                                        <span className="docs-action-label">New skill</span>
                                    </Button>
                                ) : (
                                    <Button
                                        type="button"
                                        variant="secondary"
                                        size="sm"
                                        className="docs-topbar-action gap-2 px-2 sm:px-3"
                                        onClick={() => setNewPageName((current) => current || 'Untitled')}
                                        aria-label="New page"
                                        title="New page"
                                    >
                                        <FileText className="h-4 w-4" />
                                        <span className="docs-action-label">New page</span>
                                    </Button>
                                )}
                                <Tooltip>
                                    <TooltipTrigger asChild>
                                        <Button
                                            type="button"
                                            variant="quiet"
                                            size="icon"
                                            className="h-8 w-8 rounded"
                                            disabled={isUploadingFile}
                                            onClick={() => docsUploadInputRef.current?.click()}
                                            aria-label="Upload"
                                        >
                                            {isUploadingFile ? <StepLoader size="sm" /> : <Upload className="h-4 w-4" />}
                                        </Button>
                                    </TooltipTrigger>
                                    <TooltipContent>Upload</TooltipContent>
                                </Tooltip>
                                <Tooltip>
                                    <TooltipTrigger asChild>
                                        <Button
                                            type="button"
                                            variant="quiet"
                                            size="icon"
                                            className="h-8 w-8 rounded"
                                            disabled={isUploadingFile || folderUpload.isFolderUploading}
                                            onClick={() => void folderUpload.handleUploadFolderClick()}
                                            aria-label="Upload folder"
                                        >
                                            {folderUpload.isFolderUploading ? <StepLoader size="sm" /> : <Folder className="h-4 w-4" />}
                                        </Button>
                                    </TooltipTrigger>
                                    <TooltipContent>Upload folder</TooltipContent>
                                </Tooltip>
                                {/* A bare folder under `/skills` is not a skill —
                                    it is a row that will only ever say it won't
                                    load. "New skill" is the way in there. */}
                                {isSkillsFolder ? null : (
                                    <Button variant="primary"
                                        type="button"
                                        size="sm"
                                        className="docs-topbar-action gap-2 px-2 sm:px-3"
                                        disabled={isCreatingFolder}
                                        onClick={() => setNewFolderName((current) => current || 'Untitled folder')}
                                        aria-label="New folder"
                                        title="New folder"
                                    >
                                        {isCreatingFolder ? <StepLoader size="sm" /> : <FolderPlus className="h-4 w-4" />}
                                        <span className="docs-action-label">New folder</span>
                                    </Button>
                                )}
                            </div>
                            </TooltipProvider>
                        )}
                    />

                    {/* The three doors, always in the same place. Which one you
                        are behind decides what the rest of this pane offers. */}
                    <div className="mb-4 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
                        <DocsSectionSwitcher activeSection={activeSectionId} onSectionChange={openSection} />
                        <p className="min-w-0 flex-1 truncate text-xs text-[var(--text-tertiary)]">
                            {activeSection.blurb}
                        </p>
                    </div>

                    {/* Teaching where it helps, gone once it doesn't. The primer
                        used to sit above every listing, so a folder with content
                        carried three bands of chrome over two rows of substance. */}
                    {!folderPath && isFolderBlank ? <SectionPrimer concept="file" className="mb-4" /> : null}

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

                    {newPageName || newSkillName || newFolderName ? (
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
                            {newSkillName ? (
                                <InlineCreateRow
                                    value={newSkillName}
                                    onChange={setNewSkillName}
                                    placeholder="Skill name"
                                    onSubmit={handleCreateSkill}
                                    onCancel={() => setNewSkillName('')}
                                    isBusy={isCreatingFolder || isUploadingFile}
                                    hint={skillNameHint(newSkillName)}
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

                    {/* One column that owns the pane. The listing takes the space
                        it needs and the drop target takes the rest, so a folder
                        with two rows in it stops looking like a failed render.
                        The height comes down the flex chain from `pod-page-surface`
                        rather than from a guessed `100dvh - chrome` subtraction. */}
                    <div className="flex min-h-0 flex-1 flex-col">
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
                        {/* Label and count on one line with the search field. Two
                            stacked lines of heading for a two-row list read as a
                            section that lost its content. */}
                        <div className="docs-list-toolbar mb-2 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
                            <p className="docs-list-toolbar-title min-w-0 flex-1 truncate text-xs text-[var(--text-tertiary)]">
                                <span className="text-[var(--text-secondary)]">
                                    {isSectionHome ? sectionListLabel : 'In this folder'}
                                </span>
                                <span className="px-1.5 text-[var(--border-strong)]">·</span>
                                {isSearchMode
                                    ? isSearchingFiles ? 'Searching inside documents' : `${searchResultItems.length} result${searchResultItems.length === 1 ? '' : 's'}`
                                    : sectionCountLabel}
                            </p>
                            <div className="docs-list-toolbar-search relative min-w-[min(16rem,100%)] flex-[0_1_17rem]">
                                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--text-tertiary)]" />
                                <Input
                                    value={docsSearchQuery}
                                    onChange={(event) => setDocsSearchQuery(event.target.value)}
                                    placeholder={activeSection.searchPlaceholder}
                                    className="form-field-control h-9 pl-9 text-sm shadow-none"
                                />
                            </div>
                        </div>

                        {/* The listing has an edge now. Rows on bare canvas gave
                            the content no bottom, which is most of why the page
                            read as empty even when it wasn't.

                            An empty folder skips the panel entirely: an empty box
                            stacked on top of the drop target is two ways of saying
                            "nothing here", and the drop target already says it
                            while also being the thing you act on. */}
                        {/* `min-h-0` is what makes this scroll rather than clip: a
                            flex item's automatic minimum size is its content, so
                            without it the panel refuses to shrink and the rows
                            past the fold are simply cut off. `overflow-x-hidden`
                            keeps the rounded corners clipping rows as before. */}
                        <div
                            className={cn(
                                'surface-panel-quiet min-h-0 overflow-y-auto overflow-x-hidden',
                                isFolderBlank && 'hidden'
                            )}
                        >
                            {/* Rows, at the row's own height. An `h-28` caption is
                                one box where the list will be six, so the panel
                                used to shrink or grow the moment docs arrived. */}
                            {isLoadingDocsFiles ? (
                                <DocsRowsSkeleton />
                            ) : isSearchMode ? (
                                isSearchingFiles ? (
                                    <DocsRowsSkeleton rows={4} />
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
                                            className="document-space-result-button surface-list-row custom-focus-ring items-start gap-3 px-3 py-3 text-left text-sm"
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
                                    {activeSection.emptyLine}
                                </QuietEmptyState>
                            ) : showsSkillRows ? (
                                <>
                                    <SkillEntriesList
                                        podId={podId}
                                        folders={docsEntries.filter((entry) => isFolder(entry))}
                                        onOpenSkill={(skillName) => openDocFile(skillManifestPath(skillName))}
                                        renderActions={renderEntryActions}
                                    />
                                    {/* A loose file dropped into `/skills` is not a
                                        skill and should not pretend to be one. */}
                                    {docsEntries.filter((entry) => !isFolder(entry)).map(renderDocRow)}
                                </>
                            ) : (
                                docsEntries.map(renderDocRow)
                            )}
                        </div>

                        {/* The space under a short listing was doing nothing, and
                            drop-to-upload was already wired — it just had no
                            standing target. Now the leftover height is the target. */}
                        {!isSearchMode ? (
                            <Button
                                type="button"
                                variant="quiet"
                                onClick={() => docsUploadInputRef.current?.click()}
                                disabled={isUploadingFile}
                                className={cn(
                                    'flex h-auto min-h-[8rem] flex-1 flex-col items-center justify-center gap-1.5 whitespace-normal rounded-lg border border-dashed border-[color:color-mix(in_srgb,var(--border-subtle)_88%,transparent)] px-6 py-8 text-center hover:border-[color:var(--border-strong)] hover:bg-[color:color-mix(in_srgb,var(--surface-2)_42%,transparent)]',
                                    isFolderBlank ? 'mt-0' : 'mt-3'
                                )}
                            >
                                <Upload className="h-4 w-4 text-[var(--text-tertiary)]" />
                                <span className="text-sm text-[var(--text-secondary)]">
                                    {isFolderBlank
                                        ? activeSection.emptyLine
                                        : 'Drop files here, or click to browse'}
                                </span>
                                <span className="max-w-sm text-xs leading-5 text-[var(--text-tertiary)]">
                                    {dropTargetHint}
                                </span>
                            </Button>
                        ) : null}
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

    /**
     * Opening a skill opens the skill, so closing it returns to the skills
     * list. Walking up one directory would strand you in `/skills/<name>` — a
     * folder holding the single file you just closed, which is a dead end
     * dressed as a destination.
     */
    const selectedSkillName = isSkillManifestPath(selectedFilePath) ? skillNameFromPath(selectedFilePath) : null;
    const selectedFileParentPath = selectedSkillName ? SKILLS_ROOT : getParentDirectoryPath(selectedFilePath);
    const selectedFileBackHref = buildDocsHref({ folder: selectedFileParentPath, file: null });
    const selectedFileBackLabel = getDirectoryLabel(selectedFileParentPath);
    const selectedFileTitle = selectedSkillName || selectedFileName || 'Docs';

    return (
        <div className="h-full min-h-0 bg-[var(--bg-canvas)]">
            {!isAssistantPresentation ? (
                <ResourceHeader
                    title={selectedFileTitle}
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
                contextLabel={selectedSkillName ? 'Skill' : selectedFileIsPersonal ? 'Personal file' : 'Shared doc'}
                onClose={() => updateQuery({ file: null })}
                onDeleted={() => updateQuery({ file: null })}
                extraActions={selectedFileIsPersonal ? (
                    <Tooltip>
                        <TooltipTrigger asChild>
                        <Button
                            type="button"
                            variant="quiet"
                            size="icon"
                            className="h-8 w-8 rounded"
                            disabled={isPromotingToPodDocs}
                            onClick={() => void handlePromoteToPodDocs(selectedFilePath)}
                            aria-label="Share to pod docs"
                        >
                            {isPromotingToPodDocs ? (
                                <StepLoader size="sm" />
                            ) : (
                                <Files className="h-4 w-4" />
                            )}
                        </Button>
                        </TooltipTrigger>
                        <TooltipContent>Share to pod docs</TooltipContent>
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
                        <StepLoader size="sm" className="shrink-0 text-[var(--state-info)]" />
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
    hint,
}: {
    value: string;
    onChange: (value: string) => void;
    placeholder: string;
    onSubmit: () => void | Promise<void>;
    onCancel: () => void;
    isBusy?: boolean;
    hint?: string;
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
                {hint ? (
                    <p className="min-w-0 flex-1 truncate px-2 text-xs text-[var(--text-tertiary)]">{hint}</p>
                ) : null}
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
                    {isBusy ? <StepLoader size="xs" /> : 'Create'}
                </button>
            </div>
        </div>
    );
}
