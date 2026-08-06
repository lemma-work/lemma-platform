'use client';

import { useCallback, useRef, useState, type ReactNode } from 'react';
import dynamic from 'next/dynamic';
import { useRouter } from 'next/navigation';
import { ArrowDown, ArrowUp, Plus, Filter, Trash2, Edit, MoreVertical, Maximize2, Table as TableIcon } from '@/components/ui/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { DestructiveConfirmationDialog } from '@/components/shared/destructive-confirmation-dialog';
import { DatastoreTableSkeleton } from '@/components/data/datastore-table-skeleton';
import { TableRowsSkeleton } from '@/components/shared/loading';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { EditableCell } from '@/components/tables/cells/editable-cell';
import { ColumnResizeHandle } from '@/components/tables/column-resize-handle';
import { ColumnTypeIcon } from '@/components/tables/column-type-icon';
import { ViewSwitcher, type ViewType } from '@/components/tables/view-switcher';
import { ResourceShareButton, ResourceVisibilityBadge, type ResourceVisibilityValue } from '@/components/shared/resource-visibility';
import { resourceAllows } from '@/lib/authz/resource-actions';
import {
    normalizeTable,
    tableQueryOptions,
    tableRecordsQueryOptions,
    tablesQueryOptions,
    useDeleteRecord,
    useDeleteTable,
} from '@/lib/hooks/use-datastores';
import {
    ACTIONS_COLUMN_WIDTH,
    SELECT_COLUMN_WIDTH,
    defaultColumnWidth,
    useTableColumnWidths,
} from '@/lib/hooks/use-table-column-widths';
import { useTableShortcuts } from '@/lib/hooks/use-table-shortcuts';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { buildResourceShareUrl } from '@/lib/assistant/conversation-presentation';
import type { Column, Table } from '@/lib/types';
import type { FilterRule } from '@/lib/types/app';
import { getDisplayColumns, sanitizeRecordPayload } from '@/lib/utils/datastore-records';

const FilterBuilder = dynamic(
    () => import('@/components/records/filter-builder').then((module) => module.FilterBuilder)
);
const RecordEditor = dynamic(
    () => import('@/components/records/record-editor').then((module) => module.RecordEditor)
);
const AddColumnDialog = dynamic(
    () => import('@/components/tables/add-column-dialog').then((module) => module.AddColumnDialog)
);
const RecordDetail = dynamic(
    () => import('@/components/tables/record-detail').then((module) => module.RecordDetail)
);
const ListView = dynamic(
    () => import('@/components/tables/views/list-view').then((module) => module.ListView)
);

type TableRecord = Record<string, unknown>;
interface DatastoreTableViewProps {
    podId: string;
    datastoreName: string;
    tableName: string;
    embedded?: boolean;
    onTableDeleted?: () => void;
    headerLeft?: ReactNode | ((args: { table: Table; totalRecords: number }) => ReactNode);
    headerRightPrefix?: ReactNode;
    canWriteRecords?: boolean;
    canUpdateTable?: boolean;
    canDeleteTable?: boolean;
    initialFilters?: FilterRule[];
}

export function DatastoreTableView({
    podId,
    datastoreName,
    tableName,
    embedded = false,
    onTableDeleted,
    headerLeft,
    headerRightPrefix,
    canWriteRecords: canWriteRecordsFallback = true,
    canUpdateTable: canUpdateTableFallback = true,
    canDeleteTable: canDeleteTableFallback = true,
    initialFilters,
}: DatastoreTableViewProps) {
    const router = useRouter();
    const queryClient = useQueryClient();
    const deleteTableMutation = useDeleteTable();
    const deleteRecordMutation = useDeleteRecord();

    const [selectedRows, setSelectedRows] = useState<Set<string>>(new Set());
    const [filters, setFilters] = useState<FilterRule[]>(() => initialFilters ?? []);
    const [sortField, setSortField] = useState<string | null>(null);
    const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');
    const [page, setPage] = useState(0);
    const [showFilters, setShowFilters] = useState(false);
    const [showRecordEditor, setShowRecordEditor] = useState(false);
    const [editingRecord, setEditingRecord] = useState<TableRecord | null>(null);
    const [showRecordDetail, setShowRecordDetail] = useState(false);
    const [detailRecord, setDetailRecord] = useState<TableRecord | null>(null);
    const [showDeleteTableDialog, setShowDeleteTableDialog] = useState(false);
    const [showDeleteRecordsDialog, setShowDeleteRecordsDialog] = useState(false);
    const [columnPendingDelete, setColumnPendingDelete] = useState<string | null>(null);
    const [showAddColumnDialog, setShowAddColumnDialog] = useState(false);
    const [currentView, setCurrentView] = useState<ViewType>('grid');
    const [queryPageTokens, setQueryPageTokens] = useState<Array<string | null>>([null]);
    const limit = 50;
    const usesStructuredQuery = filters.length > 0 || sortField !== null;

    const tableRef = useRef<HTMLTableElement>(null);
    const { widthFor, setColumnWidth, resetColumnWidth, clampWidth } = useTableColumnWidths(
        podId,
        tableName
    );

    useTableShortcuts({
        onNewRecord: () => {
            if (canWriteRecords) setShowRecordEditor(true);
        },
        onFilter: () => setShowFilters(true),
        enabled: !showRecordEditor && !showRecordDetail && !showFilters,
    });

    const { data: table, isLoading: tableLoading } = useQuery({
        ...tableQueryOptions(podId, tableName),
    });

    const { data: recordsData, isLoading: recordsLoading, isFetching: recordsFetching } = useQuery({
        ...tableRecordsQueryOptions(podId, datastoreName, tableName, {
            page,
            limit,
            sortField,
            sortOrder,
            filters,
            pageToken: queryPageTokens[page] ?? null,
        }),
        enabled: !!tableName,
    });

    const { data: tablesData } = useQuery({
        ...tablesQueryOptions(podId),
        enabled: showAddColumnDialog,
    });
    const availableTables: Table[] = tablesData?.items || [];
    const canWriteRecords = resourceAllows(table, 'datastore.record.write', canWriteRecordsFallback);
    const canUpdateTable = resourceAllows(table, 'datastore.table.update', canUpdateTableFallback);
    const canDeleteTable = resourceAllows(table, 'datastore.table.delete', canDeleteTableFallback);

    const updateTableVisibilityMutation = useMutation({
        mutationFn: async (visibility: ResourceVisibilityValue) => {
            const response = await getLemmaClient(podId).tables.update(tableName, { visibility });
            return normalizeTable(response as unknown as Record<string, unknown>);
        },
        onSuccess: (updatedTable) => {
            queryClient.setQueryData(['table', podId, tableName], updatedTable);
            queryClient.invalidateQueries({ queryKey: ['tables', podId] });
            toast.success('Table sharing updated');
        },
        onError: () => {
            toast.error('Failed to update table sharing');
        },
    });

    const updateCellMutation = useMutation({
        mutationFn: ({ recordId, columnName, value }: { recordId: string; columnName: string; value: unknown }) => {
            if (!canWriteRecords) throw new Error('You do not have permission to edit records.');
            const data = { [columnName]: value };
            return getLemmaClient(podId).records.update(tableName, recordId, data);
        },
        onMutate: async ({ recordId, columnName, value }) => {
            await queryClient.cancelQueries({ queryKey: ['records', podId, datastoreName, tableName] });
            const previousData = queryClient.getQueryData(['records', podId, datastoreName, tableName, page, sortField, sortOrder, filters]);

            queryClient.setQueryData(['records', podId, datastoreName, tableName, page, sortField, sortOrder, filters], (old: unknown) => {
                const existing = old as { items: TableRecord[] } | undefined;
                if (!existing) return existing;
                return {
                    ...existing,
                    items: existing.items.map((record) =>
                        record[table?.primary_key_column || 'id'] === recordId
                            ? { ...record, [columnName]: value }
                            : record
                    ),
                };
            });

            return { previousData };
        },
        onError: (err, variables, context) => {
            if (context?.previousData) {
                queryClient.setQueryData(
                    ['records', podId, datastoreName, tableName, page, sortField, sortOrder, filters],
                    context.previousData
                );
            }
        },
        onSettled: () => {
            queryClient.invalidateQueries({ queryKey: ['records', podId, datastoreName, tableName] });
        },
    });

    const createRecordMutation = useMutation({
        mutationFn: (data: Record<string, unknown>) =>
            canWriteRecords ? getLemmaClient(
                podId
            ).records.create(
                tableName,
                sanitizeRecordPayload(table?.columns || [], data, { omitAutoComputed: true })
            ) : Promise.reject(new Error('You do not have permission to create records.')),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['records', podId, datastoreName, tableName] });
            setShowRecordEditor(false);
        },
    });

    const updateRecordMutation = useMutation({
        mutationFn: ({ id, data }: { id: string; data: Record<string, unknown> }) =>
            canWriteRecords ? getLemmaClient(
                podId
            ).records.update(
                tableName,
                id,
                sanitizeRecordPayload(table?.columns || [], data, { omitAutoComputed: true })
            ) : Promise.reject(new Error('You do not have permission to edit records.')),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['records', podId, datastoreName, tableName] });
            setShowRecordEditor(false);
            setEditingRecord(null);
        },
    });

    const deleteRecordsMutation = useMutation({
        mutationFn: async (recordIds: string[]) => {
            if (!canWriteRecords) throw new Error('You do not have permission to delete records.');
            await Promise.all(
                recordIds.map((id) => getLemmaClient(podId).records.delete(tableName, id))
            );
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['records', podId, datastoreName, tableName] });
            setSelectedRows(new Set());
            setShowDeleteRecordsDialog(false);
            toast.success('Records deleted');
        },
        onError: () => {
            toast.error('Failed to delete records');
        },
    });

    const removeColumnMutation = useMutation({
        mutationFn: (columnName: string) =>
            canUpdateTable
                ? getLemmaClient(podId).tables.columns.remove(tableName, columnName)
                : Promise.reject(new Error('You do not have permission to edit table schema.')),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['table', podId, tableName] });
            queryClient.invalidateQueries({ queryKey: ['records', podId, datastoreName, tableName] });
            queryClient.invalidateQueries({ queryKey: ['tables', podId] });
            setColumnPendingDelete(null);
            toast.success('Column deleted');
        },
        onError: () => {
            toast.error('Failed to delete column');
        },
    });

    const records = (recordsData?.items as TableRecord[] | undefined) || [];
    const orderedColumns = table ? getDisplayColumns(table.columns, table.primary_key_column) : [];
    // Select column (when writable) + the data columns + the slack column + the
    // trailing rail that holds "add column" in the head and "expand" in a row.
    const gridColumnCount = orderedColumns.length + 2 + (canWriteRecords ? 1 : 0);
    // A definite width is what makes `table-layout: fixed` mean anything: CSS
    // hands the fixed algorithm only to tables that have one, so the old
    // `min-w-full` (a *minimum*, leaving width auto) quietly bought the
    // automatic algorithm instead — every column as wide as its longest cell.
    const gridWidth =
        (canWriteRecords ? SELECT_COLUMN_WIDTH : 0) +
        orderedColumns.reduce((total, column) => total + widthFor(column), 0) +
        ACTIONS_COLUMN_WIDTH;
    const getRecordId = (record: TableRecord): string =>
        String(record[table?.primary_key_column || 'id'] ?? '');

    /**
     * Drag feedback, written straight to the DOM. React owns the width once the
     * reader lets go; while they are still dragging, a state update per pointer
     * move would re-render every cell on the page to move one edge.
     *
     * The commit path writes here too, so the element and the state always agree
     * on where the drag ended — React skips a DOM write when the width it
     * renders matches the one it rendered last, which after a drag that returns
     * to where it started is exactly the width the element must not keep.
     */
    const paintColumnWidth = useCallback(
        (columnName: string, width: number) => {
            const grid = tableRef.current;
            if (!grid) return;

            const columns = Array.from(grid.querySelectorAll<HTMLTableColElement>('col[data-column]'));
            const target = columns.find((column) => column.dataset.column === columnName);
            if (!target) return;

            target.style.width = `${clampWidth(width)}px`;
            grid.style.width = `${
                (canWriteRecords ? SELECT_COLUMN_WIDTH : 0) +
                ACTIONS_COLUMN_WIDTH +
                columns.reduce((total, column) => total + (Number.parseFloat(column.style.width) || 0), 0)
            }px`;
        },
        [canWriteRecords, clampWidth]
    );

    const previewColumnWidth = useCallback(
        (columnName: string, width: number) => {
            if (tableRef.current) tableRef.current.dataset.resizing = 'true';
            paintColumnWidth(columnName, width);
        },
        [paintColumnWidth]
    );

    const commitColumnWidth = useCallback(
        (columnName: string, width: number) => {
            if (tableRef.current) delete tableRef.current.dataset.resizing;
            paintColumnWidth(columnName, width);
            setColumnWidth(columnName, width);
        },
        [paintColumnWidth, setColumnWidth]
    );

    const restoreColumnWidth = useCallback(
        (column: Column) => {
            if (tableRef.current) delete tableRef.current.dataset.resizing;
            paintColumnWidth(column.name, defaultColumnWidth(column));
            resetColumnWidth(column.name);
        },
        [paintColumnWidth, resetColumnWidth]
    );

    const handleSort = (columnName: string) => {
        setPage(0);
        setQueryPageTokens([null]);
        if (sortField === columnName) {
            setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
        } else {
            setSortField(columnName);
            setSortOrder('asc');
        }
    };

    const handleSelectAll = () => {
        if (selectedRows.size === records.length) {
            setSelectedRows(new Set());
        } else {
            setSelectedRows(new Set(records.map((record) => getRecordId(record))));
        }
    };

    const handleSelectRow = (id: string) => {
        const next = new Set(selectedRows);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        setSelectedRows(next);
    };

    if (tableLoading) {
        return <DatastoreTableSkeleton />;
    }

    if (!table) {
        return (
            <div className="surface-card py-12 text-center">
                <h2 className="font-display mb-2 text-2xl font-semibold text-[var(--text-primary)]">Table not found</h2>
                <p className="mb-4 text-[var(--text-secondary)]">The table &quot;{tableName}&quot; could not be loaded</p>
                <Button variant="quiet" onClick={() => router.back()}>Go Back</Button>
            </div>
        );
    }

    return (
        <div className={embedded ? 'datastore-table-workbench lemma-workbench-panel data-table-workbench relative flex h-full min-h-0 flex-col overflow-hidden' : 'h-full flex flex-col bg-transparent'}>
            {/* Embedded: the workbench CSS owns this row's surface — it is canvas
                chrome with a hairline, not a filled card. Standalone keeps the
                sticky filled bar, because there it *is* the page header. */}
            <div className={embedded ? 'data-table-toolbar shrink-0' : 'sticky top-0 z-20 shrink-0 border-b border-[color:var(--row-border)] bg-[var(--card-bg)] backdrop-blur-sm'}>
                <div className={embedded ? 'flex w-full items-center justify-between gap-2' : 'w-full px-4 py-2 flex items-center justify-between'}>
                    {headerLeft ? (
                        <div className="min-w-0 flex-1">
                            {typeof headerLeft === 'function'
                                ? headerLeft({ table, totalRecords: recordsData?.total || 0 })
                                : headerLeft}
                        </div>
                    ) : (
                        <div className="flex items-center gap-3">
                            <TableIcon className="h-4 w-4 text-[var(--text-tertiary)]" />
                            <div className="flex items-center gap-3">
                                <h1 className={embedded ? 'text-sm font-medium text-[var(--text-primary)]' : 'text-base font-medium text-[var(--text-primary)]'}>
                                    {table.name}
                                </h1>
                                <span className="chip chip-sm">
                                    {recordsData?.total || 0} records
                                </span>
                                <ResourceVisibilityBadge visibility={table.visibility} resourceLabel="tables" />
                            </div>
                        </div>
                    )}

                    <div className="flex items-center gap-1.5">
                        {headerRightPrefix}
                        {canUpdateTable ? (
                            <ResourceShareButton
                                value={table.visibility}
                                podId={podId}
                                resourceType="datastore_table"
                                resourceId={table.name}
                                resourceLabel="tables"
                                resourceName={table.name}
                                shareUrl={typeof window === 'undefined'
                                    ? undefined
                                    : buildResourceShareUrl(
                                        `${window.location.pathname}${window.location.search}${window.location.hash}`,
                                        window.location.origin,
                                    ) ?? undefined}
                                onChange={async (visibility) => {
                                    await updateTableVisibilityMutation.mutateAsync(visibility);
                                }}
                                buttonClassName="datastore-table-toolbar-button h-8 rounded px-2 text-xs"
                            />
                        ) : null}
                        <ViewSwitcher currentView={currentView} onViewChange={setCurrentView} />
                        <div className="mx-0.5 h-5 w-px bg-[color:color-mix(in_srgb,var(--border-subtle)_55%,transparent)]" />
                        <Button variant="quiet" size="sm" onClick={() => setShowFilters(true)} className="datastore-table-toolbar-button h-8 gap-2 rounded px-2 text-xs text-[var(--text-secondary)] hover:bg-[var(--surface-2)]">
                            <Filter className="h-3.5 w-3.5 text-[var(--text-tertiary)]" />
                            Filter {filters.length > 0 && `(${filters.length})`}
                        </Button>
                        {canDeleteTable ? <Button
                            variant="quiet"
                            size="icon"
                            onClick={() => setShowDeleteTableDialog(true)}
                            className="h-8 w-8 rounded text-[var(--text-tertiary)] hover:bg-[var(--surface-2)] hover:text-[var(--state-error)]"
                            title="Delete Table"
                        >
                            <Trash2 className="w-3.5 h-3.5" />
                        </Button> : null}
                        {/* design.md §8: a toolbar create is not the primary —
                            the records are what the user came for. The violet
                            fill belongs to the empty state, where creating one
                            really is the only thing to do. */}
                        {canWriteRecords ? <Button variant="secondary" size="sm" onClick={() => setShowRecordEditor(true)} className="h-8 text-xs">
                            <Plus className="w-3.5 h-3.5" />
                            New record
                        </Button> : null}
                    </div>
                </div>
            </div>

            {canWriteRecords && selectedRows.size > 0 && (
                <div className="glass-panel absolute left-1/2 top-20 z-30 flex -translate-x-1/2 animate-in items-center gap-4 rounded-full px-5 py-2.5 shadow-[var(--shadow-lg)] fade-in slide-in-from-top-4">
                    <span className="text-sm font-medium text-[var(--text-primary)]">{selectedRows.size} selected</span>
                    <div className="h-4 w-px bg-[var(--row-border)]" />
                    <div className="flex items-center gap-1">
                        <Button
                            variant="quiet"
                            size="sm"
                            className="h-8 rounded-full"
                            onClick={() => {
                                const firstRowId = Array.from(selectedRows)[0];
                                const record = records.find((row) => getRecordId(row) === firstRowId);
                                if (record) {
                                    setEditingRecord(record);
                                    setShowRecordEditor(true);
                                }
                            }}
                        >
                            <Edit className="w-3.5 h-3.5 mr-1.5" />
                            Edit
                        </Button>
                        <Button
                            variant="quiet"
                            size="sm"
                            className="hover-state-error h-8 rounded-full text-[var(--state-error)]"
                            onClick={() => setShowDeleteRecordsDialog(true)}
                        >
                            <Trash2 className="w-3.5 h-3.5 mr-1.5" />
                            Delete
                        </Button>
                    </div>
                </div>
            )}

            <div className={embedded ? 'data-table-viewport relative flex-1 overflow-hidden bg-[var(--row-bg)]' : 'relative flex-1 overflow-auto p-2'}>
                {currentView === 'grid' ? (
                    <div className={embedded ? 'data-table-grid-frame h-full' : 'h-full'}>
                        <div className="h-full overflow-auto bg-transparent">
                            {/* eslint-disable no-restricted-syntax -- A column width is
                                the runtime geometry the rule allows for: it is a number the
                                reader chose by dragging, kept per table, and a stylesheet has
                                no way to name it. */}
                            <table ref={tableRef} className="data-table-grid" style={{ width: gridWidth }}>
                                {/* Every column's width is declared once, here.
                                    The unsized column near the end is slack: it
                                    swallows whatever a narrow table leaves over,
                                    so two columns stay two columns instead of
                                    stretching to fill the pane, and the rows
                                    still rule all the way to the frame. */}
                                <colgroup>
                                    {canWriteRecords ? <col style={{ width: SELECT_COLUMN_WIDTH }} /> : null}
                                    {orderedColumns.map((column: Column) => (
                                        <col
                                            key={column.name}
                                            data-column={column.name}
                                            style={{ width: widthFor(column) }}
                                        />
                                    ))}
                                    <col />
                                    <col style={{ width: ACTIONS_COLUMN_WIDTH }} />
                                </colgroup>
                                {/* eslint-enable no-restricted-syntax */}
                                <thead className="data-table-head sticky top-0 z-10 border-b border-[color:var(--row-border)] bg-[var(--card-bg)] backdrop-blur-md">
                                    <tr>
                                        {canWriteRecords ? <th className="px-2.5 py-2 text-center">
                                            <input
                                                type="checkbox"
                                                checked={selectedRows.size === records.length && records.length > 0}
                                                onChange={handleSelectAll}
                                                className="h-4 w-4 rounded border-[color:var(--field-border)] text-[var(--action-primary)] focus:ring-[var(--action-primary-soft)] focus:ring-offset-0"
                                            />
                                        </th> : null}
                                        {orderedColumns.map((column: Column) => (
                                            <th
                                                key={column.name}
                                                scope="col"
                                                className="group relative px-3 py-2 text-left text-xs font-normal tracking-wide text-[var(--text-tertiary)]"
                                            >
                                                <div className="flex items-center justify-between gap-1">
                                                    {/* The name is what has to survive a narrow
                                                        column, so it is the only part allowed to
                                                        take the room: the type is a glyph, the
                                                        sort a bare arrow, and the whole label
                                                        clips rather than pushing the column wider. */}
                                                    <button
                                                        type="button"
                                                        onClick={() => handleSort(column.name)}
                                                        title={`${column.name} · ${String(column.type).toLowerCase()}`}
                                                        className="flex min-w-0 flex-1 select-none items-center gap-1.5 text-left"
                                                    >
                                                        <ColumnTypeIcon type={column.type} />
                                                        <span className="truncate text-[var(--text-secondary)]">{column.name}</span>
                                                        {sortField === column.name && (
                                                            sortOrder === 'asc'
                                                                ? <ArrowUp className="size-3 shrink-0 text-[var(--action-primary)]" aria-hidden="true" />
                                                                : <ArrowDown className="size-3 shrink-0 text-[var(--action-primary)]" aria-hidden="true" />
                                                        )}
                                                    </button>

                                                    {canUpdateTable && column.name !== table.primary_key_column && (
                                                        <DropdownMenu>
                                                            <DropdownMenuTrigger asChild>
                                                                {/* No negative margin: it used to hang into the
                                                                    cell's right padding, which is now where the
                                                                    resize grip lives. */}
                                                                <Button variant="quiet" size="icon" className="h-6 w-6 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                                                                    <MoreVertical className="h-3 w-3 text-[var(--text-tertiary)]" />
                                                                </Button>
                                                            </DropdownMenuTrigger>
                                                            <DropdownMenuContent align="end">
                                                                <DropdownMenuItem
                                                                    className="focus-state-error text-[var(--state-error)] focus:text-[var(--state-error)]"
                                                                    onClick={() => setColumnPendingDelete(column.name)}
                                                                >
                                                                    <Trash2 className="w-4 h-4 mr-2" />
                                                                    Delete Column
                                                                </DropdownMenuItem>
                                                            </DropdownMenuContent>
                                                        </DropdownMenu>
                                                    )}
                                                </div>

                                                <ColumnResizeHandle
                                                    columnName={column.name}
                                                    width={widthFor(column)}
                                                    onPreview={previewColumnWidth}
                                                    onCommit={commitColumnWidth}
                                                    onReset={() => restoreColumnWidth(column)}
                                                />
                                            </th>
                                        ))}
                                        <th aria-hidden="true" />
                                        <th className="px-2.5 py-2">
                                            {canUpdateTable ? (
                                                <Button
                                                    variant="quiet"
                                                    size="sm"
                                                    onClick={() => setShowAddColumnDialog(true)}
                                                    className="h-7 w-7 rounded-md bg-[var(--bg-subtle)] p-0 text-[var(--text-secondary)] hover:bg-[var(--bg-muted)] hover:text-[var(--text-primary)]"
                                                    title="Add Column"
                                                >
                                                    <Plus className="w-4 h-4" />
                                                </Button>
                                            ) : null}
                                        </th>
                                    </tr>
                                </thead>
                                {/* Paging and filtering keep the rows they already
                                    have and dim them. Swapping real records for
                                    placeholders on every page turn was a downgrade
                                    dressed up as feedback. */}
                                <tbody
                                    className="divide-y divide-[color:color-mix(in_srgb,var(--border-subtle)_42%,transparent)]"
                                    data-refreshing={recordsFetching && !recordsLoading ? 'true' : undefined}
                                >
                                    {/* Rows, not a caption. The version this replaces
                                        collapsed a page-height body to one line of
                                        "Loading records…" and then expanded it again
                                        — two height changes for one fetch. A fixed
                                        run of row-shaped placeholders keeps the grid,
                                        the scrollbar, and the footer exactly where
                                        the records will put them. */}
                                    {recordsLoading ? (
                                        <TableRowsSkeleton rows={12} columns={gridColumnCount} />
                                    ) : records.length === 0 ? (
                                        <tr>
                                            {/* The columns are already drawn above this
                                                row, so the pane is not blank — it is a
                                                table waiting for a row. Say that, and
                                                give it the button, instead of dropping
                                                one grey sentence into the middle. */}
                                            <td colSpan={gridColumnCount} className="px-6 py-14 text-center">
                                                <p className="text-sm font-medium text-[var(--text-primary)]">
                                                    {filters.length > 0 ? 'No records match these filters' : 'No records yet'}
                                                </p>
                                                <p className="mx-auto mt-1 max-w-sm text-xs leading-5 text-[var(--text-tertiary)]">
                                                    {filters.length > 0
                                                        ? 'Clear a filter to see the rest of this table.'
                                                        : `${table.name} has its columns. Add the first row, or let an agent or workflow write into it.`}
                                                </p>
                                                {filters.length > 0 ? (
                                                    <Button
                                                        variant="secondary"
                                                        size="sm"
                                                        className="mt-4 gap-1.5"
                                                        onClick={() => setShowFilters(true)}
                                                    >
                                                        <Filter className="h-3.5 w-3.5" />
                                                        Edit filters
                                                    </Button>
                                                ) : canWriteRecords ? (
                                                    <Button
                                                        variant="primary"
                                                        size="sm"
                                                        className="mt-4 gap-1.5"
                                                        onClick={() => setShowRecordEditor(true)}
                                                    >
                                                        <Plus className="h-3.5 w-3.5" />
                                                        New record
                                                    </Button>
                                                ) : null}
                                            </td>
                                        </tr>
                                    ) : (
                                        records.map((record) => {
                                            const recordId = getRecordId(record);
                                            return (
                                                <tr
                                                    key={recordId}
                                                    className={`group transition-colors duration-75 hover:bg-[var(--row-bg-hover)] ${selectedRows.has(recordId) ? 'row-selected-delight' : ''}`}
                                                >
                                                    {canWriteRecords ? <td className="px-2.5 py-1 text-center">
                                                        <input
                                                            type="checkbox"
                                                            checked={selectedRows.has(recordId)}
                                                            onChange={() => handleSelectRow(recordId)}
                                                            className="h-4 w-4 rounded border-[color:var(--field-border)] text-[var(--action-primary)] opacity-0 transition-opacity focus:ring-[var(--action-primary-soft)] focus:ring-offset-0 group-hover:opacity-100 data-[checked=true]:opacity-100"
                                                            data-checked={selectedRows.has(recordId)}
                                                        />
                                                    </td> : null}
                                                    {orderedColumns.map((column: Column) => (
                                                        <td key={column.name} className="px-1.5 py-1">
                                                            {canWriteRecords ? <EditableCell
                                                                value={record[column.name]}
                                                                fieldType={column.type}
                                                                enumOptions={column.options}
                                                                onSave={async (newValue) => {
                                                                    await updateCellMutation.mutateAsync({
                                                                        recordId,
                                                                        columnName: column.name,
                                                                        value: newValue,
                                                                    });
                                                                }}
                                                            /> : (
                                                                <span className="block truncate px-2 py-1.5 text-sm text-[var(--text-secondary)]">
                                                                    {record[column.name] == null ? '' : String(record[column.name])}
                                                                </span>
                                                            )}
                                                        </td>
                                                    ))}
                                                    <td aria-hidden="true" />
                                                    <td className="px-2.5 py-1">
                                                        <button
                                                            type="button"
                                                            className="datastore-table-expand-button rounded-md p-1.5 text-[var(--text-tertiary)] opacity-0 transition-[opacity,background-color,color] hover:bg-[var(--row-bg)] hover:text-[var(--action-primary)] group-hover:opacity-100"
                                                            title="Expand record"
                                                            onClick={() => {
                                                                setDetailRecord(record);
                                                                setShowRecordDetail(true);
                                                            }}
                                                        >
                                                            <Maximize2 className="w-3.5 h-3.5" />
                                                        </button>
                                                    </td>
                                                </tr>
                                            );
                                        })
                                    )}
                                </tbody>
                            </table>
                        </div>
                    </div>
                ) : currentView === 'list' ? (
                    <ListView
                        records={records}
                        table={table}
                        onRecordClick={(record) => {
                            setDetailRecord(record);
                            setShowRecordDetail(true);
                        }}
                        selectedRecords={selectedRows}
                        onSelectRecord={handleSelectRow}
                    />
                ) : null}
            </div>

            {currentView === 'grid' && (
                <div className="data-table-footer shrink-0 border-t border-[color:var(--row-border)] bg-[var(--card-bg)]">
                    <div className="flex items-center justify-between">
                        <div className="text-sm text-[var(--text-secondary)]">
                            {/* An empty table used to report "Showing 1 to 0 of 0
                                records" — a range that counts backwards. */}
                            {records.length === 0
                                ? 'No records'
                                : `Showing ${page * limit + 1} to ${page * limit + records.length} of ${recordsData?.total || records.length} records`}
                        </div>
                        <div className="flex items-center gap-2">
                            <Button
                                variant="quiet"
                                size="sm"
                                className="bg-[var(--bg-subtle)] text-[var(--text-secondary)] hover:bg-[var(--bg-canvas)] hover:text-[var(--text-primary)]"
                                onClick={() => setPage(Math.max(0, page - 1))}
                                disabled={page === 0}
                            >
                                Previous
                            </Button>
                            <Button
                                variant="quiet"
                                size="sm"
                                className="bg-[var(--bg-subtle)] text-[var(--text-secondary)] hover:bg-[var(--bg-canvas)] hover:text-[var(--text-primary)]"
                                onClick={() => {
                                    if (usesStructuredQuery) {
                                        const nextToken = recordsData?.next_page_token ?? null;
                                        if (!nextToken) return;
                                        setQueryPageTokens((previous) => {
                                            const next = [...previous];
                                            next[page + 1] = nextToken;
                                            return next;
                                        });
                                    }
                                    setPage(page + 1);
                                }}
                                disabled={
                                    usesStructuredQuery
                                        ? !recordsData?.next_page_token
                                        : page * limit + records.length >= (recordsData?.total || 0)
                                }
                            >
                                Next
                            </Button>
                        </div>
                    </div>
                </div>
            )}

            <DestructiveConfirmationDialog
                open={showDeleteTableDialog}
                onOpenChange={setShowDeleteTableDialog}
                title="Delete table"
                description={`Delete "${table.name}"? This removes the table schema and its data.`}
                resourceName={table.name}
                consequences={[
                    'All records in this table will be deleted.',
                    'Views and workflows that depend on this table may stop working.',
                    'This action cannot be undone.',
                ]}
                confirmLabel="Delete table"
                pendingLabel="Deleting table..."
                isPending={deleteTableMutation.isPending}
                onConfirm={() => {
                    deleteTableMutation.mutate(
                        { podId, datastoreName, tableName },
                        {
                            onSuccess: () => {
                                toast.success('Table deleted');
                                setShowDeleteTableDialog(false);
                                const tablesQueryKey = ['tables', podId] as const;
                                queryClient.setQueryData(
                                    tablesQueryKey,
                                    (previous: unknown) => {
                                        const cached = previous as {
                                            items?: Array<{ name: string }>;
                                            total?: number;
                                        } | undefined;
                                        if (!cached?.items) return previous;

                                        const remainingItems = cached.items.filter(
                                            (item) => item.name !== tableName
                                        );

                                        return {
                                            ...cached,
                                            items: remainingItems,
                                            total:
                                                typeof cached.total === 'number'
                                                    ? Math.max(0, remainingItems.length)
                                                    : cached.total,
                                        };
                                    }
                                );
                                queryClient.invalidateQueries({ queryKey: tablesQueryKey });

                                const nextTable = (
                                    queryClient.getQueryData(tablesQueryKey) as
                                        | { items?: Array<{ name: string }> }
                                        | undefined
                                )?.items?.[0]?.name;

                                if (onTableDeleted) onTableDeleted();
                                else if (nextTable) router.push(`/pod/${podId}/data?datastore=${encodeURIComponent(datastoreName)}&tab=${encodeURIComponent(nextTable)}`);
                                else router.push(`/pod/${podId}/data?datastore=${encodeURIComponent(datastoreName)}`);
                            },
                            onError: () => {
                                toast.error('Failed to delete table');
                            },
                        }
                    );
                }}
            />
            <DestructiveConfirmationDialog
                open={showDeleteRecordsDialog}
                onOpenChange={setShowDeleteRecordsDialog}
                title="Delete records"
                description={`Delete ${selectedRows.size} selected record${selectedRows.size === 1 ? '' : 's'} from "${table.name}"?`}
                resourceName={`${selectedRows.size} records`}
                confirmationText=""
                consequences={[
                    'Selected records will be removed from this table.',
                    'This action cannot be undone.',
                ]}
                confirmLabel="Delete records"
                pendingLabel="Deleting records..."
                isPending={deleteRecordsMutation.isPending}
                onConfirm={() => deleteRecordsMutation.mutate(Array.from(selectedRows))}
            />
            <DestructiveConfirmationDialog
                open={Boolean(columnPendingDelete)}
                onOpenChange={(open) => {
                    if (!open) setColumnPendingDelete(null);
                }}
                title="Delete column"
                description={`Delete column "${columnPendingDelete ?? ''}" from "${table.name}"?`}
                resourceName={columnPendingDelete ?? 'column'}
                confirmationText=""
                consequences={[
                    'All values in this column will be removed from every record.',
                    'This action cannot be undone.',
                ]}
                confirmLabel="Delete column"
                pendingLabel="Deleting column..."
                isPending={removeColumnMutation.isPending}
                onConfirm={() => {
                    if (columnPendingDelete) removeColumnMutation.mutate(columnPendingDelete);
                }}
            />

            {showFilters && table && (
                <FilterBuilder
                    columns={table.columns}
                    filters={filters}
                    onFiltersChange={(newFilters) => {
                        setPage(0);
                        setQueryPageTokens([null]);
                        setFilters(newFilters);
                        setShowFilters(false);
                    }}
                    onClose={() => setShowFilters(false)}
                />
            )}

            {canWriteRecords && showRecordEditor && table && (
                <RecordEditor
                    key={editingRecord ? getRecordId(editingRecord) : 'new'}
                    columns={table.columns}
                    record={editingRecord ?? undefined}
                    onSave={(data) => {
                        if (editingRecord) {
                            updateRecordMutation.mutate({
                                id: getRecordId(editingRecord),
                                data,
                            });
                        } else {
                            createRecordMutation.mutate(data);
                        }
                    }}
                    onClose={() => {
                        setShowRecordEditor(false);
                        setEditingRecord(null);
                    }}
                    podId={podId}
                    datastoreName={datastoreName}
                />
            )}

            {canUpdateTable && showAddColumnDialog && (
                <AddColumnDialog
                    podId={podId}
                    datastoreName={datastoreName}
                    tableName={tableName}
                    availableTables={availableTables}
                    onClose={() => setShowAddColumnDialog(false)}
                />
            )}

            {showRecordDetail && table && detailRecord && (
                <RecordDetail
                    key={getRecordId(detailRecord)}
                    record={detailRecord}
                    table={table}
                    onClose={() => {
                        setShowRecordDetail(false);
                        setDetailRecord(null);
                    }}
                    onUpdate={(data) => {
                        updateRecordMutation.mutate({
                            id: getRecordId(detailRecord),
                            data,
                        });
                        setShowRecordDetail(false);
                        setDetailRecord(null);
                    }}
                    onDelete={() => {
                        if (!canWriteRecords) return;
                        deleteRecordMutation.mutate(
                            {
                                podId,
                                datastoreName,
                                tableName,
                                recordId: getRecordId(detailRecord),
                            },
                            {
                                onSuccess: () => {
                                    setShowRecordDetail(false);
                                    setDetailRecord(null);
                                },
                            }
                        );
                    }}
                    canWrite={canWriteRecords}
                    onNext={() => {
                        const currentIndex = records.findIndex((row) => getRecordId(row) === getRecordId(detailRecord));
                        if (currentIndex < records.length - 1) {
                            setDetailRecord(records[currentIndex + 1]);
                        }
                    }}
                    onPrevious={() => {
                        const currentIndex = records.findIndex((row) => getRecordId(row) === getRecordId(detailRecord));
                        if (currentIndex > 0) {
                            setDetailRecord(records[currentIndex - 1]);
                        }
                    }}
                />
            )}
        </div>
    );
}
