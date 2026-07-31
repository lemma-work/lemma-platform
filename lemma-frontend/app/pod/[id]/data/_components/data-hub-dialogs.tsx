'use client';

import { Loader2 } from '@/components/ui/icons';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';

import type { PendingFolderUploadConfirmation } from '../_lib/folder-upload';

export interface FolderUploadConfirmDialogProps {
    pendingFolderUploadConfirmation: PendingFolderUploadConfirmation | null;
    isFolderUploading: boolean;
    isUploading: boolean;
    onOpenChange: (open: boolean) => void;
    onConfirm: () => void;
    onCancel: () => void;
}

export function FolderUploadConfirmDialog({
    pendingFolderUploadConfirmation,
    isFolderUploading,
    isUploading,
    onOpenChange,
    onConfirm,
    onCancel,
}: FolderUploadConfirmDialogProps) {
    return (
        <Dialog
            open={Boolean(pendingFolderUploadConfirmation)}
            onOpenChange={onOpenChange}
        >
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>
                        {pendingFolderUploadConfirmation?.resumeSession ? 'Confirm Folder Resume' : 'Confirm Folder Upload'}
                    </DialogTitle>
                </DialogHeader>
                {pendingFolderUploadConfirmation && (
                    <div className="space-y-3 py-2">
                        <p className="text-sm text-[var(--text-secondary)]">
                            Selected {pendingFolderUploadConfirmation.totalFiles} file
                            {pendingFolderUploadConfirmation.totalFiles === 1 ? '' : 's'} across{' '}
                            {pendingFolderUploadConfirmation.rootFolderNames.length} folder
                            {pendingFolderUploadConfirmation.rootFolderNames.length === 1 ? '' : 's'}.
                        </p>
                        <div className="space-y-1">
                            <p className="text-xs font-medium text-[var(--text-primary)]">Top-level folders to create</p>
                            <div className="max-h-28 overflow-auto rounded-md border border-[var(--border-subtle)] bg-[var(--bg-subtle)] px-2 py-1.5">
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
                )}
                <DialogFooter>
                    <Button variant="ghost" onClick={onCancel}>
                        Cancel
                    </Button>
                    <Button
                        onClick={() => void onConfirm()}
                        disabled={isFolderUploading || isUploading || !pendingFolderUploadConfirmation}
                    >
                        {pendingFolderUploadConfirmation?.resumeSession ? 'Yes, Resume Upload' : 'Yes, Start Upload'}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

export interface NewMarkdownFileDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    newFileName: string;
    onNewFileNameChange: (value: string) => void;
    isUploading: boolean;
    onCreate: () => void;
    onCancel: () => void;
}

export function NewMarkdownFileDialog({
    open,
    onOpenChange,
    newFileName,
    onNewFileNameChange,
    isUploading,
    onCreate,
    onCancel,
}: NewMarkdownFileDialogProps) {
    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>Create Markdown File</DialogTitle>
                </DialogHeader>
                <div className="space-y-2 py-2">
                    <Input
                        value={newFileName}
                        onChange={(event) => onNewFileNameChange(event.target.value)}
                        placeholder="e.g. meeting-notes"
                        onKeyDown={(event) => {
                            if (event.key === 'Enter') {
                                event.preventDefault();
                                void onCreate();
                            }
                        }}
                    />
                </div>
                <DialogFooter>
                    <Button variant="ghost" onClick={onCancel}>
                        Cancel
                    </Button>
                    <Button onClick={() => void onCreate()} disabled={isUploading}>
                        {isUploading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : 'Create'}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
