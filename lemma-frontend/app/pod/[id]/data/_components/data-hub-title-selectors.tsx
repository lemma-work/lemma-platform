'use client';

import { Check, ChevronDown, Database, FolderPlus, Loader2, Plus } from '@/components/ui/icons';

import { ProductIcon } from '@/components/pod/product-icon';
import { ResourceTitleButton } from '@/components/pod/resource-layout';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import type { DatastoreFile } from '@/lib/types';

import { getDatastoreFilePath, getFileNameFromPath } from '../_lib/file-helpers';

export interface FolderTitleSelectorProps {
    selectedFilePath: string | null;
    currentFolderLabel: string;
    currentFolderPath: string | null;
    folderTrail: DatastoreFile[];
    canWriteFiles: boolean;
    activeDatastoreName: string | null;
    onBreadcrumbClick: (folderPath: string | null) => void;
    onNewFolder: () => void;
    onNewFile: () => void;
}

export function FolderTitleSelector({
    selectedFilePath,
    currentFolderLabel,
    currentFolderPath,
    folderTrail,
    canWriteFiles,
    activeDatastoreName,
    onBreadcrumbClick,
    onNewFolder,
    onNewFile,
}: FolderTitleSelectorProps) {
    return (
        <DropdownMenu>
            <DropdownMenuTrigger asChild>
                <ResourceTitleButton
                    trailing={<ChevronDown className="h-4 w-4" />}
                >
                    {selectedFilePath ? getFileNameFromPath(selectedFilePath) : currentFolderLabel}
                </ResourceTitleButton>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="w-72">
                <DropdownMenuLabel>Folders</DropdownMenuLabel>
                <DropdownMenuItem onClick={() => onBreadcrumbClick(null)}>
                    <span className="mr-2 inline-flex">
                        <ProductIcon kind="folders" size="sm" />
                    </span>
                    Home
                    {!currentFolderPath ? <Check className="ml-auto h-3.5 w-3.5" /> : null}
                </DropdownMenuItem>
                {folderTrail.map((folder) => {
                    const path = getDatastoreFilePath(folder);
                    return (
                        <DropdownMenuItem key={folder.id} onClick={() => onBreadcrumbClick(path)}>
                            <span className="mr-2 inline-flex">
                                <ProductIcon kind="folders" size="sm" />
                            </span>
                            <span className="truncate">{folder.name}</span>
                            {currentFolderPath === path ? <Check className="ml-auto h-3.5 w-3.5" /> : null}
                        </DropdownMenuItem>
                    );
                })}
                {canWriteFiles ? (
                    <>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem onClick={onNewFolder} disabled={!activeDatastoreName}>
                            <FolderPlus className="mr-2 h-3.5 w-3.5" />
                            New folder
                        </DropdownMenuItem>
                        <DropdownMenuItem onClick={onNewFile}>
                            <Plus className="mr-2 h-3.5 w-3.5" />
                            New file
                        </DropdownMenuItem>
                    </>
                ) : null}
            </DropdownMenuContent>
        </DropdownMenu>
    );
}

export interface TableTitleSelectorProps {
    loadingTables: boolean;
    activeTableName: string | null;
    tables: { name: string }[];
    canCreateTable: boolean;
    onSelectTable: (tableName: string) => void;
    onNewTable: () => void;
}

export function TableTitleSelector({
    loadingTables,
    activeTableName,
    tables,
    canCreateTable,
    onSelectTable,
    onNewTable,
}: TableTitleSelectorProps) {
    return (
        <DropdownMenu>
            <DropdownMenuTrigger asChild>
                <ResourceTitleButton
                    trailing={<ChevronDown className="h-4 w-4" />}
                >
                    {loadingTables ? 'Loading tables' : activeTableName ?? 'No tables yet'}
                </ResourceTitleButton>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="w-72">
                <DropdownMenuLabel>Tables</DropdownMenuLabel>
                {loadingTables ? (
                    <DropdownMenuItem disabled>
                        <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                        Loading tables
                    </DropdownMenuItem>
                ) : tables.length > 0 ? (
                    tables.map((item) => (
                        <DropdownMenuItem
                            key={item.name}
                            onClick={() => onSelectTable(item.name)}
                        >
                            <Database className="mr-2 h-3.5 w-3.5" />
                            <span className="truncate">{item.name}</span>
                            {item.name === activeTableName ? <Check className="ml-auto h-3.5 w-3.5" /> : null}
                        </DropdownMenuItem>
                    ))
                ) : (
                    <DropdownMenuItem disabled>
                        <Database className="mr-2 h-3.5 w-3.5" />
                        No tables yet
                    </DropdownMenuItem>
                )}
                {canCreateTable ? (
                    <>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem onClick={onNewTable}>
                            <Plus className="mr-2 h-3.5 w-3.5" />
                            New table
                        </DropdownMenuItem>
                    </>
                ) : null}
            </DropdownMenuContent>
        </DropdownMenu>
    );
}
