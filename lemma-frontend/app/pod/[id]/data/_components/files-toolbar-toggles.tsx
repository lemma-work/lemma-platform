'use client';

import { Database, LayoutGrid, List as ListIcon, UserRound } from '@/components/ui/icons';

import type { FileNamespaceMode } from '../_lib/file-helpers';

export interface FileNamespaceToggleProps {
    activeFileNamespace: FileNamespaceMode;
    onNamespaceChange: (namespace: FileNamespaceMode) => void;
}

export function FileNamespaceToggle({ activeFileNamespace, onNamespaceChange }: FileNamespaceToggleProps) {
    return (
        <div className="segmented-control">
            {(['PERSONAL', 'POD'] as const).map((namespace) => {
                const active = activeFileNamespace === namespace;
                const Icon = namespace === 'PERSONAL' ? UserRound : Database;
                return (
                    <button
                        key={namespace}
                        type="button"
                        onClick={() => onNamespaceChange(namespace)}
                        className="segmented-control-item"
                        data-active={active}
                        aria-pressed={active}
                        title={namespace === 'PERSONAL' ? 'Personal/private files' : 'Pod-shared files'}
                    >
                        <Icon className="h-3.5 w-3.5" />
                        <span className="hidden sm:inline">{namespace === 'PERSONAL' ? 'Personal' : 'Pod'}</span>
                    </button>
                );
            })}
        </div>
    );
}

export interface FilesViewToggleProps {
    filesView: 'grid' | 'list';
    onFilesViewChange: (view: 'grid' | 'list') => void;
}

export function FilesViewToggle({ filesView, onFilesViewChange }: FilesViewToggleProps) {
    return (
        <div className="segmented-control">
            <button
                type="button"
                onClick={() => onFilesViewChange('grid')}
                className="segmented-control-item segmented-control-item-icon"
                data-active={filesView === 'grid'}
                aria-pressed={filesView === 'grid'}
                title="Grid view"
            >
                <LayoutGrid className="h-3.5 w-3.5" />
            </button>
            <button
                type="button"
                onClick={() => onFilesViewChange('list')}
                className="segmented-control-item segmented-control-item-icon"
                data-active={filesView === 'list'}
                aria-pressed={filesView === 'list'}
                title="List view"
            >
                <ListIcon className="h-3.5 w-3.5" />
            </button>
        </div>
    );
}
