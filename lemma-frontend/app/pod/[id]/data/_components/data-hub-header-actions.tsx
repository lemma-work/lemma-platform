'use client';

import type { RefObject } from 'react';
import { Folder, FolderPlus, Loader2, Plus, Upload } from '@/components/ui/icons';

import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';

import { folderInputAttributes } from '../_lib/folder-upload';
import type { FileNamespaceMode } from '../_lib/file-helpers';
import { FileNamespaceToggle, FilesViewToggle } from './files-toolbar-toggles';

export interface DataHubHeaderActionsProps {
    showingFiles: boolean;
    activeFileNamespace: FileNamespaceMode;
    onNamespaceChange: (namespace: FileNamespaceMode) => void;
    filesView: 'grid' | 'list';
    onFilesViewChange: (view: 'grid' | 'list') => void;
    uploadInputRef: RefObject<HTMLInputElement | null>;
    uploadFolderInputRef: RefObject<HTMLInputElement | null>;
    onUploadSelection: (files: FileList | null) => void;
    onFolderInputChange: (files: FileList | null) => void;
    canWriteFiles: boolean;
    canCreateTable: boolean;
    isUploading: boolean;
    isFolderUploading: boolean;
    activeDatastoreName: string | null;
    onUploadFilesClick: () => void;
    onUploadFolderClick: () => void;
    onToggleNewFolderInput: () => void;
    onOpenNewFile: () => void;
    onNewTable: () => void;
}

export function DataHubHeaderActions({
    showingFiles,
    activeFileNamespace,
    onNamespaceChange,
    filesView,
    onFilesViewChange,
    uploadInputRef,
    uploadFolderInputRef,
    onUploadSelection,
    onFolderInputChange,
    canWriteFiles,
    canCreateTable,
    isUploading,
    isFolderUploading,
    activeDatastoreName,
    onUploadFilesClick,
    onUploadFolderClick,
    onToggleNewFolderInput,
    onOpenNewFile,
    onNewTable,
}: DataHubHeaderActionsProps) {
    return (
        <TooltipProvider>
        <>
            {showingFiles ? (
                <>
                    <FileNamespaceToggle activeFileNamespace={activeFileNamespace} onNamespaceChange={onNamespaceChange} />
                    <FilesViewToggle filesView={filesView} onFilesViewChange={onFilesViewChange} />
                    <input
                        ref={uploadInputRef}
                        type="file"
                        multiple
                        className="hidden"
                        onChange={(event) => {
                            void onUploadSelection(event.target.files);
                            event.currentTarget.value = '';
                        }}
                    />
                    <input
                        ref={uploadFolderInputRef}
                        type="file"
                        multiple
                        className="hidden"
                        {...folderInputAttributes}
                        onChange={(event) => {
                            void onFolderInputChange(event.target.files);
                            event.currentTarget.value = '';
                        }}
                    />
                    {canWriteFiles ? (
                        <Tooltip>
                            <TooltipTrigger asChild>
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    className="h-8 w-8 rounded"
                                    onClick={onUploadFilesClick}
                                    disabled={isUploading || isFolderUploading || !activeDatastoreName}
                                    aria-label="Upload files"
                                >
                                    {isUploading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
                                </Button>
                            </TooltipTrigger>
                            <TooltipContent>Upload files</TooltipContent>
                        </Tooltip>
                    ) : null}
                    {canWriteFiles ? (
                        <Tooltip>
                            <TooltipTrigger asChild>
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    className="h-8 w-8 rounded"
                                    onClick={() => void onUploadFolderClick()}
                                    disabled={isFolderUploading || isUploading || !activeDatastoreName}
                                    aria-label="Upload folder"
                                >
                                    {isFolderUploading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Folder className="h-3.5 w-3.5" />}
                                </Button>
                            </TooltipTrigger>
                            <TooltipContent>Upload folder</TooltipContent>
                        </Tooltip>
                    ) : null}
                    {canWriteFiles ? <Button
                        variant="ghost"
                        size="sm"
                        className="h-8 gap-1.5 rounded px-2 text-xs text-[var(--text-secondary)] hover:bg-[var(--surface-2)]"
                        onClick={onToggleNewFolderInput}
                        disabled={!activeDatastoreName}
                    >
                        <FolderPlus className="h-3.5 w-3.5" />
                        New folder
                    </Button> : null}
                    {canWriteFiles ? <Button
                        size="sm"
                        className="h-8 gap-1.5"
                        onClick={onOpenNewFile}
                    >
                        <Plus className="h-3.5 w-3.5" />
                        New file
                    </Button> : null}
                </>
            ) : (
                canCreateTable ? <Button
                    size="sm"
                    className="h-8 gap-1.5"
                    onClick={onNewTable}
                >
                    <Plus className="h-3.5 w-3.5" />
                    New table
                </Button> : null
            )}
        </>
        </TooltipProvider>
    );
}
