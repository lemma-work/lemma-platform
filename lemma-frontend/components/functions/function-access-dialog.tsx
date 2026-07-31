'use client';

import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { ConnectorsSelector, DatastoresSelector, FoldersSelector } from '@/components/pod/resource-selectors';
import { AccessMode, type Function as FunctionType } from '@/lib/types';

/**
 * What a function is allowed to touch.
 *
 * The agent version of this has six shelves and earns a rail; a function has
 * three, so they stack. Same promise either way: everything it can reach, and
 * nothing else.
 */
export function FunctionAccessDialog({
    open,
    onOpenChange,
    podId,
    functionData,
    onUpdate,
}: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    podId: string;
    functionData: FunctionType;
    onUpdate: (data: Partial<FunctionType>) => void;
}) {
    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-w-2xl gap-0 overflow-hidden p-0">
                <DialogHeader className="border-b border-[color:var(--border-subtle)] px-5 py-4 pr-12 text-left">
                    <DialogTitle>Access</DialogTitle>
                    <DialogDescription className="text-xs">
                        Everything <strong className="agent-identity-subject">{functionData.name}</strong> can reach.
                        Anything left off does not exist as far as it knows.
                    </DialogDescription>
                </DialogHeader>

                <div className="max-h-[min(70dvh,32rem)] space-y-4 overflow-y-auto px-5 py-4">
                    <ConnectorsSelector
                        podId={podId}
                        selected={functionData.accessible_connectors || []}
                        onChange={(configs) => onUpdate({ accessible_connectors: configs })}
                    />

                    <DatastoresSelector
                        podId={podId}
                        selected={(functionData.accessible_tables || []).map((entry) => entry.table_name)}
                        modeByName={Object.fromEntries(
                            (functionData.accessible_tables || []).map((entry) => [entry.table_name, entry.mode]),
                        )}
                        onChange={(names) => {
                            const modeByTable = new Map(
                                (functionData.accessible_tables || []).map((entry) => [entry.table_name, entry.mode]),
                            );
                            onUpdate({
                                accessible_tables: names.map((table_name) => ({
                                    table_name,
                                    mode: modeByTable.get(table_name) ?? AccessMode.READ,
                                })),
                            });
                        }}
                        onModeChange={(name, mode) => {
                            onUpdate({
                                accessible_tables: (functionData.accessible_tables || []).map((entry) =>
                                    entry.table_name === name ? { ...entry, mode } : entry,
                                ),
                            });
                        }}
                    />

                    <FoldersSelector
                        podId={podId}
                        selected={(functionData.accessible_folders || []).map((entry) => entry.folder_path)}
                        modeByPath={Object.fromEntries(
                            (functionData.accessible_folders || []).map((entry) => [entry.folder_path, entry.mode]),
                        )}
                        onChange={(folderPaths) => {
                            const modeByFolder = new Map(
                                (functionData.accessible_folders || []).map((entry) => [entry.folder_path, entry.mode]),
                            );
                            onUpdate({
                                accessible_folders: folderPaths.map((folder_path) => ({
                                    folder_path,
                                    mode: modeByFolder.get(folder_path) ?? AccessMode.READ,
                                })),
                            });
                        }}
                        onModeChange={(folderPath, mode) => {
                            onUpdate({
                                accessible_folders: (functionData.accessible_folders || []).map((entry) =>
                                    entry.folder_path === folderPath ? { ...entry, mode } : entry,
                                ),
                            });
                        }}
                    />
                </div>

                <DialogFooter className="flex-row items-center border-t border-[color:var(--border-subtle)] px-5 py-3">
                    <Button type="button" size="sm" className="ml-auto" onClick={() => onOpenChange(false)}>
                        Done
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
