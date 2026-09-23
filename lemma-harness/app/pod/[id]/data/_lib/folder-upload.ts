import { normalizeFileNamespace } from './file-helpers';
import type { FileNamespaceMode } from './file-helpers';

export const FOLDER_UPLOAD_STORAGE_KEY = 'lemma:pod-folder-upload-history:v1';
export const MAX_FOLDER_UPLOAD_HISTORY = 20;
export const MAX_FOLDER_UPLOAD_FAILURES = 6;

export type FolderUploadStatus = 'running' | 'completed' | 'completed_with_errors' | 'failed' | 'interrupted';
export type FolderUploadSession = {
    id: string;
    podId: string;
    datastoreName: string;
    namespace: FileNamespaceMode;
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

export type FolderUploadCandidate = {
    file: File;
    relativePath: string;
    targetDirectoryPath: string;
    targetFileName: string;
};
export type PendingFolderUploadConfirmation = {
    candidates: FolderUploadCandidate[];
    resumeSession: FolderUploadSession | null;
    rootFolderNames: string[];
    targetRootPaths: string[];
    totalFiles: number;
    pendingFiles: number;
};

export type RelativeFileEntry = {
    file: File;
    relativePath: string;
};
export type DirectoryHandleWithEntries = FileSystemDirectoryHandle & {
    entries: () => AsyncIterableIterator<[string, FileSystemFileHandle | FileSystemDirectoryHandle]>;
};

export const folderInputAttributes = {
    webkitdirectory: '',
} as const;

export function readFolderUploadHistory(): FolderUploadSession[] {
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
                    namespace: normalizeFileNamespace((safeEntry as Partial<FolderUploadSession>).namespace || 'POD'),
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

export function writeFolderUploadHistory(history: FolderUploadSession[]) {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(FOLDER_UPLOAD_STORAGE_KEY, JSON.stringify(history));
}

export function upsertFolderUploadHistory(history: FolderUploadSession[], session: FolderUploadSession): FolderUploadSession[] {
    const next = [session, ...history.filter((item) => item.id !== session.id)]
        .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
        .slice(0, MAX_FOLDER_UPLOAD_HISTORY);
    return next;
}

export function markRunningUploadsInterrupted(history: FolderUploadSession[]): FolderUploadSession[] {
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

export function extractRelativePath(file: File): string {
    const maybeRelativePath = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
    const segments = maybeRelativePath
        .split('/')
        .map((part) => part.trim())
        .filter((part) => part && part !== '.' && part !== '..');
    return segments.join('/');
}
