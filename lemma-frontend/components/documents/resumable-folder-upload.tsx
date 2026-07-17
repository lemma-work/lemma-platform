'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Folder, Loader2 } from '@/components/ui/icons';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import type { DatastoreFile } from '@/lib/types';
import { cn } from '@/lib/utils';

const FOLDER_UPLOAD_STORAGE_KEY = 'lemma:pod-folder-upload-history:v1';
const MAX_FOLDER_UPLOAD_HISTORY = 20;
const MAX_FOLDER_UPLOAD_FAILURES = 6;

type FolderUploadStatus = 'running' | 'completed' | 'completed_with_errors' | 'failed' | 'interrupted';

export type FolderUploadSession = {
    id: string;
    podId: string;
    datastoreName: string;
    startedInDirectoryPath: string | null;
    rootFolderNames: string[];
    totalFiles: number;
    completedFiles: number;
    failedFiles: number;
    currentRelativePath: string | null;
    failures: string[];
    completedRelativePaths: string[];
    interruptedByUser?: boolean;
    status: FolderUploadStatus;
    createdAt: string;
    updatedAt: string;
};

type FolderUploadCandidate = {
    file: File;
    relativePath: string;
    targetDirectoryPath: string;
    targetFileName: string;
};

type PendingFolderUploadConfirmation = {
    candidates: FolderUploadCandidate[];
    resumeSession: FolderUploadSession | null;
    rootFolderNames: string[];
    targetRootPaths: string[];
    totalFiles: number;
    pendingFiles: number;
};

type RelativeFileEntry = {
    file: File;
    relativePath: string;
};

type DirectoryHandleWithEntries = FileSystemDirectoryHandle & {
    entries: () => AsyncIterableIterator<[string, FileSystemFileHandle | FileSystemDirectoryHandle]>;
};

export const folderInputAttributes = {
    webkitdirectory: '',
} as const;

function isFolder(file: DatastoreFile): boolean {
    return file.kind === 'FOLDER';
}

function joinPath(basePath: string | null | undefined, segment: string): string {
    const cleanSegment = segment.trim().replace(/^\/+|\/+$/g, '');
    if (!cleanSegment) {
        const normalizedBase = (basePath || '/').trim();
        return normalizedBase || '/';
    }

    const normalizedBase = (basePath || '/').trim() || '/';
    if (normalizedBase === '/') return `/${cleanSegment}`;
    return `${normalizedBase.replace(/\/+$/, '')}/${cleanSegment}`;
}

function getParentPath(path: string | null | undefined): string | null {
    const normalized = (path || '').trim();
    if (!normalized || normalized === '/') return null;

    const withoutTrailingSlash = normalized.replace(/\/+$/, '');
    const slashIndex = withoutTrailingSlash.lastIndexOf('/');
    if (slashIndex <= 0) return null;

    return withoutTrailingSlash.slice(0, slashIndex);
}

function readFolderUploadHistory(): FolderUploadSession[] {
    if (typeof window === 'undefined') return [];
    try {
        const raw = window.localStorage.getItem(FOLDER_UPLOAD_STORAGE_KEY);
        if (!raw) return [];
        const parsed = JSON.parse(raw);
        if (!Array.isArray(parsed)) return [];
        return parsed
            .filter((entry) => Boolean(entry && typeof entry === 'object'))
            .map((entry) => {
                const safeEntry = entry as Partial<FolderUploadSession>;
                const completedRelativePaths = Array.isArray(safeEntry.completedRelativePaths)
                    ? safeEntry.completedRelativePaths.filter((path): path is string => typeof path === 'string')
                    : [];

                const completedFiles = typeof safeEntry.completedFiles === 'number'
                    ? safeEntry.completedFiles
                    : completedRelativePaths.length;
                const totalFiles = typeof safeEntry.totalFiles === 'number'
                    ? safeEntry.totalFiles
                    : completedFiles;

                return {
                    id: typeof safeEntry.id === 'string' ? safeEntry.id : '',
                    podId: typeof safeEntry.podId === 'string' ? safeEntry.podId : '',
                    datastoreName: typeof safeEntry.datastoreName === 'string' ? safeEntry.datastoreName : '',
                    startedInDirectoryPath: typeof safeEntry.startedInDirectoryPath === 'string' ? safeEntry.startedInDirectoryPath : null,
                    rootFolderNames: Array.isArray(safeEntry.rootFolderNames)
                        ? safeEntry.rootFolderNames.filter((name): name is string => typeof name === 'string')
                        : [],
                    totalFiles,
                    completedFiles,
                    failedFiles: typeof safeEntry.failedFiles === 'number' ? safeEntry.failedFiles : Math.max(totalFiles - completedFiles, 0),
                    currentRelativePath: typeof safeEntry.currentRelativePath === 'string' ? safeEntry.currentRelativePath : null,
                    failures: Array.isArray(safeEntry.failures)
                        ? safeEntry.failures.filter((item): item is string => typeof item === 'string')
                        : [],
                    completedRelativePaths,
                    interruptedByUser: typeof safeEntry.interruptedByUser === 'boolean' ? safeEntry.interruptedByUser : false,
                    status: (safeEntry.status || 'interrupted') as FolderUploadStatus,
                    createdAt: typeof safeEntry.createdAt === 'string' ? safeEntry.createdAt : new Date().toISOString(),
                    updatedAt: typeof safeEntry.updatedAt === 'string' ? safeEntry.updatedAt : new Date().toISOString(),
                } satisfies FolderUploadSession;
            })
            .filter((entry) => entry.id && entry.podId && entry.datastoreName);
    } catch {
        return [];
    }
}

function writeFolderUploadHistory(history: FolderUploadSession[]) {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(FOLDER_UPLOAD_STORAGE_KEY, JSON.stringify(history));
}

function upsertFolderUploadHistory(history: FolderUploadSession[], session: FolderUploadSession): FolderUploadSession[] {
    return [session, ...history.filter((item) => item.id !== session.id)]
        .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
        .slice(0, MAX_FOLDER_UPLOAD_HISTORY);
}

function markRunningUploadsInterrupted(history: FolderUploadSession[]): FolderUploadSession[] {
    let changed = false;
    const next = history.map((session) => {
        if (session.status !== 'running') return session;
        changed = true;
        return {
            ...session,
            status: 'interrupted' as const,
            interruptedByUser: false,
            currentRelativePath: null,
            updatedAt: new Date().toISOString(),
        };
    });

    if (changed) writeFolderUploadHistory(next);
    return next;
}

function extractRelativePath(file: File): string {
    const maybeRelativePath = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
    const segments = maybeRelativePath
        .split('/')
        .map((part) => part.trim())
        .filter((part) => part && part !== '.' && part !== '..');
    return segments.join('/');
}

export function useResumableFolderUpload({
    podId,
    datastoreName,
    directoryPath,
    disabled,
}: {
    podId: string;
    datastoreName: string;
    directoryPath: string | null;
    disabled?: boolean;
}) {
    const queryClient = useQueryClient();
    const uploadFolderInputRef = useRef<HTMLInputElement>(null);
    const pendingResumeUploadSessionIdRef = useRef<string | null>(null);
    const folderUploadStopRequestsRef = useRef<Set<string>>(new Set());
    const [folderUploadHistory, setFolderUploadHistory] = useState<FolderUploadSession[]>([]);
    const [activeFolderUploadId, setActiveFolderUploadId] = useState<string | null>(null);
    const [stoppingFolderUploadId, setStoppingFolderUploadId] = useState<string | null>(null);
    const [pendingFolderUploadConfirmation, setPendingFolderUploadConfirmation] = useState<PendingFolderUploadConfirmation | null>(null);

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
        if (activeFolderUploadId === sessionId) setActiveFolderUploadId(null);
    }, [activeFolderUploadId]);

    useEffect(() => {
        setFolderUploadHistory(markRunningUploadsInterrupted(readFolderUploadHistory()));
    }, []);

    const scopedFolderUploads = useMemo(
        () =>
            folderUploadHistory.filter(
                (session) =>
                    session.podId === podId &&
                    session.datastoreName === datastoreName
            ),
        [datastoreName, folderUploadHistory, podId]
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
                    let targetDirectoryPath = joinPath(directoryPath, rootFolder);
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
        [directoryPath]
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
                targetRootPaths: rootFolderNames.map((rootFolderName) => joinPath(directoryPath, rootFolderName)),
                totalFiles: candidates.length,
                pendingFiles,
            });
        },
        [directoryPath]
    );

    const runFolderUpload = useCallback(
        async (candidates: FolderUploadCandidate[], resumeSession: FolderUploadSession | null = null) => {
            if (disabled) return;
            setPendingFolderUploadConfirmation(null);
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
            let session: FolderUploadSession = resumeSession
                ? {
                    ...resumeSession,
                    rootFolderNames: rootFolderNames.length > 0 ? rootFolderNames : resumeSession.rootFolderNames,
                    totalFiles: Math.max(resumeSession.totalFiles || 0, candidates.length),
                    completedFiles: Array.from(new Set(resumeSession.completedRelativePaths || [])).length,
                    currentRelativePath: null,
                    status: 'running',
                    completedRelativePaths: Array.from(new Set(resumeSession.completedRelativePaths || [])),
                    interruptedByUser: false,
                    updatedAt: now,
                }
                : {
                    id: `folder-upload-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                    podId,
                    datastoreName,
                    startedInDirectoryPath: directoryPath || null,
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
            const clearStopRequest = () => {
                folderUploadStopRequestsRef.current.delete(session.id);
                setStoppingFolderUploadId((current) => (current === session.id ? null : current));
            };
            const finalizeAsInterrupted = () => {
                updateSession({
                    status: 'interrupted',
                    interruptedByUser: true,
                    currentRelativePath: null,
                });
                queryClient.invalidateQueries({ queryKey: ['datastore-files', podId, datastoreName] });
                toast.success('Folder upload stopped. You can resume it anytime.');
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
                    (left, right) => left.split('/').filter(Boolean).length - right.split('/').filter(Boolean).length
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

                updateSession({ currentRelativePath: candidate.relativePath });

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
            queryClient.invalidateQueries({ queryKey: ['datastore-files', podId, datastoreName] });

            if (finalStatus === 'completed') {
                toast.success(`Folder uploaded (${finalCompletedCount} file${finalCompletedCount > 1 ? 's' : ''})`);
            } else if (finalStatus === 'completed_with_errors') {
                toast.error(`Folder upload finished with errors (${finalCompletedCount} succeeded, ${finalFailedCount} failed)`);
            } else {
                toast.error('Folder upload failed');
            }
        },
        [datastoreName, directoryPath, disabled, ensureFolderPathExists, persistFolderUploadSession, podId, queryClient]
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

    const handleFolderUploadSelection = useCallback(
        async (files: FileList | null, resumeSession?: FolderUploadSession | null) => {
            if (!files || disabled) return;
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
        [buildFolderUploadCandidates, disabled, queueFolderUploadConfirmation]
    );

    const handleUploadFolderClick = useCallback(async () => {
        if (disabled || isFolderUploading) return;

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
            if (error instanceof DOMException && error.name === 'AbortError') return;
            toast.error('Could not read folder directly, falling back to browser upload picker');
        }

        pendingResumeUploadSessionIdRef.current = null;
        uploadFolderInputRef.current?.click();
    }, [buildFolderUploadCandidates, disabled, isFolderUploading, pickDirectoryEntries, queueFolderUploadConfirmation]);

    const handleResumeFolderUpload = useCallback(async () => {
        if (!resumableFolderUpload || disabled || isFolderUploading) return;

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
            if (error instanceof DOMException && error.name === 'AbortError') return;
            toast.error('Could not read folder directly, falling back to browser upload picker');
        }

        pendingResumeUploadSessionIdRef.current = resumableFolderUpload.id;
        uploadFolderInputRef.current?.click();
    }, [
        buildFolderUploadCandidates,
        disabled,
        isFolderUploading,
        pickDirectoryEntries,
        queueFolderUploadConfirmation,
        resumableFolderUpload,
    ]);

    const handleFolderInputChange = useCallback(
        (files: FileList | null) => {
            const resumeSessionId = pendingResumeUploadSessionIdRef.current;
            pendingResumeUploadSessionIdRef.current = null;
            const resumeSession = resumeSessionId
                ? folderUploadHistory.find((session) => session.id === resumeSessionId) ?? null
                : null;
            void handleFolderUploadSelection(files, resumeSession);
        },
        [folderUploadHistory, handleFolderUploadSelection]
    );

    const handleConfirmFolderUpload = useCallback(async () => {
        if (!pendingFolderUploadConfirmation) return;
        const confirmation = pendingFolderUploadConfirmation;
        setPendingFolderUploadConfirmation(null);
        await runFolderUpload(confirmation.candidates, confirmation.resumeSession);
    }, [pendingFolderUploadConfirmation, runFolderUpload]);

    const handleStopFolderUpload = useCallback(() => {
        if (!activeFolderUpload || stoppingFolderUploadId === activeFolderUpload.id) return;
        folderUploadStopRequestsRef.current.add(activeFolderUpload.id);
        setStoppingFolderUploadId(activeFolderUpload.id);
        toast.success('Stopping folder upload after the current file...');
    }, [activeFolderUpload, stoppingFolderUploadId]);

    return {
        activeFolderUpload,
        handleConfirmFolderUpload,
        handleFolderInputChange,
        handleResumeFolderUpload,
        handleStopFolderUpload,
        handleUploadFolderClick,
        isFolderUploading,
        pendingFolderUploadConfirmation,
        recentFolderUpload,
        removeFolderUploadSession,
        setPendingFolderUploadConfirmation,
        stoppingFolderUploadId,
        uploadFolderInputRef,
    };
}

export function FolderUploadProgress({
    activeFolderUpload,
    recentFolderUpload,
    stoppingFolderUploadId,
    onStop,
    onResume,
    onDismiss,
    disabled,
}: {
    activeFolderUpload: FolderUploadSession | null | undefined;
    recentFolderUpload: FolderUploadSession | null | undefined;
    stoppingFolderUploadId: string | null;
    onStop: () => void;
    onResume: () => void;
    onDismiss: (sessionId: string) => void;
    disabled?: boolean;
}) {
    if (!activeFolderUpload && !recentFolderUpload) return null;

    return (
        <div className="mb-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-2)] p-2.5">
            {activeFolderUpload ? (
                <>
                    <div className="flex items-center justify-between gap-2">
                        <p className="min-w-0 truncate text-xs font-medium text-[var(--text-primary)]">
                            Uploading folder {activeFolderUpload.rootFolderNames.join(', ')}
                        </p>
                        <div className="flex items-center gap-2">
                            <span className="text-xs text-[var(--text-secondary)]">
                                {activeFolderUpload.completedFiles + activeFolderUpload.failedFiles}/{activeFolderUpload.totalFiles}
                            </span>
                            <Button
                                variant="ghost"
                                size="sm"
                                className="h-6 px-2 text-xs"
                                onClick={onStop}
                                disabled={stoppingFolderUploadId === activeFolderUpload.id}
                            >
                                {stoppingFolderUploadId === activeFolderUpload.id
                                    ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                    : 'Stop upload'}
                            </Button>
                        </div>
                    </div>
                    {activeFolderUpload.currentRelativePath ? (
                        <p className="mt-1 truncate text-xs text-[var(--text-secondary)]">
                            Current: {activeFolderUpload.currentRelativePath}
                        </p>
                    ) : null}
                    {stoppingFolderUploadId === activeFolderUpload.id ? (
                        <p className="mt-1 text-xs text-[var(--text-secondary)]">
                            Stopping after the current file finishes...
                        </p>
                    ) : null}
                </>
            ) : recentFolderUpload ? (
                <>
                    <div className="flex items-center justify-between gap-2">
                        <p className="min-w-0 truncate text-xs font-medium text-[var(--text-primary)]">
                            Last folder upload {recentFolderUpload.rootFolderNames.join(', ')}
                        </p>
                        <div className="flex items-center gap-2">
                            <span className="text-xs text-[var(--text-secondary)]">
                                {recentFolderUpload.completedFiles}/{recentFolderUpload.totalFiles}
                            </span>
                            <Button
                                variant="ghost"
                                size="sm"
                                className="h-6 px-2 text-xs"
                                onClick={() => onDismiss(recentFolderUpload.id)}
                            >
                                Dismiss
                            </Button>
                        </div>
                    </div>
                    <p className="mt-1 truncate text-xs text-[var(--text-secondary)]">
                        {recentFolderUpload.status === 'completed' && 'Completed'}
                        {recentFolderUpload.status === 'completed_with_errors' &&
                            `Completed with ${recentFolderUpload.failedFiles} failed file${recentFolderUpload.failedFiles > 1 ? 's' : ''}`}
                        {recentFolderUpload.status === 'failed' &&
                            `Failed (${recentFolderUpload.failedFiles} file${recentFolderUpload.failedFiles > 1 ? 's' : ''})`}
                        {recentFolderUpload.status === 'interrupted' &&
                            (recentFolderUpload.interruptedByUser
                                ? 'Stopped before completion'
                                : 'Interrupted (page reloaded while upload was running)')}
                    </p>
                    {recentFolderUpload.failures.length > 0 ? (
                        <p className="mt-1 truncate text-xs text-[var(--state-error)]">
                            {recentFolderUpload.failures[0]}
                        </p>
                    ) : null}
                    {recentFolderUpload.status === 'interrupted' ? (
                        <div className="mt-2">
                            <Button
                                variant="outline"
                                size="sm"
                                className="h-7 text-xs"
                                onClick={onResume}
                                disabled={disabled}
                            >
                                Resume upload
                            </Button>
                        </div>
                    ) : null}
                </>
            ) : null}
        </div>
    );
}

export function FolderUploadConfirmDialog({
    pendingFolderUploadConfirmation,
    isFolderUploading,
    disabled,
    onCancel,
    onConfirm,
}: {
    pendingFolderUploadConfirmation: PendingFolderUploadConfirmation | null;
    isFolderUploading?: boolean;
    disabled?: boolean;
    onCancel: () => void;
    onConfirm: () => void;
}) {
    return (
        <Dialog
            open={Boolean(pendingFolderUploadConfirmation)}
            onOpenChange={(open) => {
                if (!open) onCancel();
            }}
        >
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>
                        {pendingFolderUploadConfirmation?.resumeSession ? 'Confirm folder resume' : 'Confirm folder upload'}
                    </DialogTitle>
                </DialogHeader>
                {pendingFolderUploadConfirmation ? (
                    <div className="space-y-3 py-2">
                        <p className="text-sm text-[var(--text-secondary)]">
                            Selected {pendingFolderUploadConfirmation.totalFiles} file
                            {pendingFolderUploadConfirmation.totalFiles === 1 ? '' : 's'} across{' '}
                            {pendingFolderUploadConfirmation.rootFolderNames.length} folder
                            {pendingFolderUploadConfirmation.rootFolderNames.length === 1 ? '' : 's'}.
                        </p>
                        <div className="space-y-1">
                            <p className="text-xs font-medium text-[var(--text-primary)]">Top-level folders to create</p>
                            <div className="max-h-28 overflow-auto rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] px-2 py-1.5">
                                {pendingFolderUploadConfirmation.targetRootPaths.map((targetPath) => (
                                    <p key={targetPath} className="truncate text-xs text-[var(--text-secondary)]">
                                        {targetPath}
                                    </p>
                                ))}
                            </div>
                        </div>
                        <p className="text-xs text-[var(--text-tertiary)]">
                            Files to upload now: {pendingFolderUploadConfirmation.pendingFiles}
                            {pendingFolderUploadConfirmation.resumeSession
                                ? ` (already completed: ${Math.max(pendingFolderUploadConfirmation.totalFiles - pendingFolderUploadConfirmation.pendingFiles, 0)})`
                                : ''}
                        </p>
                    </div>
                ) : null}
                <DialogFooter>
                    <Button variant="ghost" onClick={onCancel}>
                        Cancel
                    </Button>
                    <Button
                        onClick={onConfirm}
                        disabled={isFolderUploading || disabled || !pendingFolderUploadConfirmation}
                    >
                        {pendingFolderUploadConfirmation?.resumeSession ? 'Yes, resume upload' : 'Yes, start upload'}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

export function UploadFolderButton({
    isFolderUploading,
    disabled,
    onClick,
    label = 'Upload folder',
    variant = 'outline',
    className,
    labelClassName,
}: {
    isFolderUploading?: boolean;
    disabled?: boolean;
    onClick: () => void;
    label?: string;
    variant?: 'ghost' | 'outline';
    className?: string;
    labelClassName?: string;
}) {
    return (
        <Button
            type="button"
            variant={variant}
            size="sm"
            className={cn('gap-2', className)}
            disabled={disabled}
            onClick={onClick}
            aria-label={label}
            title={label}
        >
            {isFolderUploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Folder className="h-4 w-4" />}
            <span className={labelClassName}>{label}</span>
        </Button>
    );
}
