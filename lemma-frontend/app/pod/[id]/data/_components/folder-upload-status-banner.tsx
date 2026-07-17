'use client';

import { Loader2 } from '@/components/ui/icons';

import { Button } from '@/components/ui/button';

import type { FolderUploadSession } from '../_lib/folder-upload';

export interface FolderUploadStatusBannerProps {
    activeFolderUpload: FolderUploadSession | null;
    recentFolderUpload: FolderUploadSession | null;
    stoppingFolderUploadId: string | null;
    isFolderUploading: boolean;
    isUploading: boolean;
    onStopFolderUpload: () => void;
    onDismissFolderUpload: (sessionId: string) => void;
    onResumeFolderUpload: () => void;
}

export function FolderUploadStatusBanner({
    activeFolderUpload,
    recentFolderUpload,
    stoppingFolderUploadId,
    isFolderUploading,
    isUploading,
    onStopFolderUpload,
    onDismissFolderUpload,
    onResumeFolderUpload,
}: FolderUploadStatusBannerProps) {
    if (!activeFolderUpload && !recentFolderUpload) return null;

    return (
        <div className="mb-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-subtle)] p-2.5">
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
                                onClick={onStopFolderUpload}
                                disabled={stoppingFolderUploadId === activeFolderUpload.id}
                            >
                                {stoppingFolderUploadId === activeFolderUpload.id
                                    ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                    : 'Stop Upload'}
                            </Button>
                        </div>
                    </div>
                    {activeFolderUpload.currentRelativePath && (
                        <p className="mt-1 truncate text-xs text-[var(--text-secondary)]">
                            Current: {activeFolderUpload.currentRelativePath}
                        </p>
                    )}
                    {stoppingFolderUploadId === activeFolderUpload.id && (
                        <p className="mt-1 text-xs text-[var(--text-secondary)]">
                            Stopping after the current file finishes...
                        </p>
                    )}
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
                                onClick={() => onDismissFolderUpload(recentFolderUpload.id)}
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
                    {recentFolderUpload.failures.length > 0 && (
                        <p className="mt-1 truncate text-xs text-[var(--state-error)]">
                            {recentFolderUpload.failures[0]}
                        </p>
                    )}
                    {recentFolderUpload.status === 'interrupted' && (
                        <div className="mt-2">
                            <Button
                                variant="outline"
                                size="sm"
                                className="h-7 text-xs"
                                onClick={() => void onResumeFolderUpload()}
                                disabled={isFolderUploading || isUploading}
                            >
                                Resume Upload
                            </Button>
                        </div>
                    )}
                </>
            ) : null}
        </div>
    );
}
