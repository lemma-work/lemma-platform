'use client';

import { useState } from 'react';
import { ChevronDown, GripVertical, Plus, Sparkles, Trash2 } from '@/components/ui/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { ResourceFeedbackBanner, getResourceErrorMessage } from '@/components/shared/resource-feedback';
import { ResourceVisibilitySelect } from '@/components/shared/resource-visibility';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { cn } from '@/lib/utils';
import type { Table } from '@/lib/types';
import { EnumOptionManager } from './enum-option-manager';
import { ExpressionBuilder } from './expression-builder';

interface TableBuilderProps {
    podId: string;
    datastoreName: string;
    onClose: () => void;
    onSuccess: (tableName?: string) => void;
}

interface ForeignKeySelection {
    table: string;
    column: string;
}

interface ColumnDef {
    name: string;
    type: string;
    required: boolean;
    unique: boolean;
    description: string;
    default?: unknown;
    foreignKey?: ForeignKeySelection;
    options?: string[];
    auto?: boolean;
    computed?: boolean;
    expression?: string;
}

interface CreateTablePayload {
    name: string;
    primary_key_column: string;
    visibility?: string | null;
    enable_rls: boolean;
    columns: Array<{
        name: string;
        type: string;
        description?: string;
        required?: boolean;
        unique?: boolean;
        default?: unknown;
        foreign_key?: {
            references: string;
        };
        options?: string[];
        auto?: boolean;
        computed?: boolean;
        expression?: string;
    }>;
}

const FIELD_TYPES = [
    { value: 'TEXT', label: 'Text', icon: 'TXT' },
    { value: 'INTEGER', label: 'Number', icon: '123' },
    { value: 'FLOAT', label: 'Decimal', icon: '0.1' },
    { value: 'BOOLEAN', label: 'Checkbox', icon: 'ON' },
    { value: 'DATE', label: 'Date', icon: 'DAY' },
    { value: 'DATETIME', label: 'Date & Time', icon: 'TIME' },
    { value: 'ENUM', label: 'Select', icon: 'TAG' },
    { value: 'JSON', label: 'JSON', icon: '{}' },
    { value: 'LINK', label: 'Link to Table', icon: 'REF' },
];

function createEmptyColumn(): ColumnDef {
    return {
        name: '',
        type: 'TEXT',
        required: false,
        unique: false,
        description: '',
        computed: false,
        auto: false,
    };
}

export function TableBuilder({ podId, onClose, onSuccess }: TableBuilderProps) {
    const queryClient = useQueryClient();
    const [tableName, setTableName] = useState('');
    const [primaryKey, setPrimaryKey] = useState('id');
    const [visibility, setVisibility] = useState('POD');
    const [enableRls, setEnableRls] = useState(true);
    const [columns, setColumns] = useState<ColumnDef[]>([
        {
            name: 'id',
            type: 'TEXT',
            required: true,
            unique: true,
            description: 'Unique identifier',
            computed: false,
            auto: false,
        },
        {
            name: 'name',
            type: 'TEXT',
            required: true,
            unique: false,
            description: '',
            computed: false,
            auto: false,
        },
    ]);

    const effectivePrimaryKey =
        columns.find((column) => column.name === primaryKey)?.name || columns[0]?.name || '';

    const { data: tablesData } = useQuery({
        queryKey: ['tables', podId],
        queryFn: async () => {
            const response = await getLemmaClient(podId).tables.list({ limit: 100 });
            return {
                ...response,
                items: (response.items || []).map((item) => {
                    const raw = item as unknown as Record<string, unknown>;
                    return {
                        name: String(raw.name || raw.name || ''),
                        primary_key_column: String(raw.primary_key_column || 'id'),
                        columns: Array.isArray(raw.columns) ? raw.columns : [],
                    } as Table;
                }),
            };
        },
    });
    const availableTables: Table[] = tablesData?.items || [];

    const createTableMutation = useMutation({
        mutationFn: (data: CreateTablePayload) =>
            getLemmaClient(podId).tables.create(data as unknown as never) as Promise<{ name?: string }>,
        onSuccess: (createdTable) => {
            queryClient.invalidateQueries({ queryKey: ['tables', podId] });
            onSuccess(createdTable?.name || createdTable?.name || tableName);
            onClose();
        },
    });

    const updateColumn = <K extends keyof ColumnDef>(index: number, field: K, value: ColumnDef[K]) => {
        setColumns((prev) => {
            const updated = [...prev];
            updated[index] = { ...updated[index], [field]: value };
            return updated;
        });
    };

    const addColumn = () => {
        setColumns((prev) => [...prev, createEmptyColumn()]);
    };

    const removeColumn = (index: number) => {
        setColumns((prev) => prev.filter((_, i) => i !== index));
    };

    const handleSubmit = (event: React.FormEvent) => {
        event.preventDefault();

        const payload: CreateTablePayload = {
            name: tableName,
            primary_key_column: effectivePrimaryKey,
            visibility,
            enable_rls: enableRls,
            columns: columns.map((column) => {
                let mappedType = column.type;
                let foreignKey: { references: string } | undefined;

                if (column.type === 'LINK' && column.foreignKey?.table && column.foreignKey?.column) {
                    const referencedTable = availableTables.find((table) => table.name === column.foreignKey?.table);
                    const referencedColumn = referencedTable?.columns.find(
                        (tableColumn) => tableColumn.name === column.foreignKey?.column,
                    );
                    mappedType = referencedColumn ? String(referencedColumn.type) : 'TEXT';

                    foreignKey = {
                        references: `${column.foreignKey.table}(${column.foreignKey.column})`,
                    };
                }

                return {
                    name: column.name.trim(),
                    type: mappedType,
                    description: column.description.trim() || undefined,
                    required: column.required && !column.computed,
                    unique: column.unique,
                    default: column.auto || column.computed ? undefined : column.default,
                    foreign_key: foreignKey,
                    options: column.type === 'ENUM' ? (column.options || []).filter(Boolean) : undefined,
                    auto: column.auto || undefined,
                    computed: column.computed || undefined,
                    expression: column.computed ? column.expression?.trim() || undefined : undefined,
                };
            }),
        };

        createTableMutation.mutate(payload);
    };

    const inputClass =
        'w-full rounded-md border border-[var(--field-border)] bg-[var(--field-bg)] px-3 py-2 text-sm text-[var(--text-primary)] transition-gentle placeholder:text-[var(--text-tertiary)] hover:border-[var(--field-border-hover)] focus-ring';
    const selectClass = `${inputClass} cursor-pointer`;

    return (
        <Sheet open={true} onOpenChange={(open) => !open && onClose()}>
            <SheetContent className="flex w-full max-w-2xl flex-col gap-0 border-l border-[var(--border-subtle)] bg-[var(--card-bg)] p-0 shadow-[var(--shadow-lg)] sm:max-w-2xl">
                <div className="border-b border-[color:var(--border-subtle)] px-6 py-5">
                    <SheetHeader>
                        <SheetTitle className="font-display text-xl font-semibold text-[var(--text-primary)]">
                            Create Table
                        </SheetTitle>
                        <SheetDescription>
                            Define your table structure and fields.
                        </SheetDescription>
                    </SheetHeader>
                </div>

                <form onSubmit={handleSubmit} className="flex min-h-0 flex-1 flex-col">
                    <div className="flex-1 space-y-6 overflow-y-auto px-6 py-5">
                        {createTableMutation.error ? (
                            <ResourceFeedbackBanner
                                tone="error"
                                title="Table was not created"
                                description={getResourceErrorMessage(createTableMutation.error, 'Failed to create table')}
                            />
                        ) : null}

                        <div className="space-y-2">
                            <label className="flex items-center gap-1.5 type-eyebrow">
                                Table Name
                                <span className="text-[var(--action-primary)]">*</span>
                            </label>
                            <input
                                type="text"
                                value={tableName}
                                onChange={(event) => setTableName(event.target.value.replace(/\s+/g, '_'))}
                                placeholder="e.g., customers"
                                required
                                className="table-builder-field w-full rounded-lg border border-[var(--field-border)] bg-[var(--field-bg)] px-4 py-3 text-base font-medium text-[var(--text-primary)] transition-gentle placeholder:text-[var(--text-tertiary)] hover:border-[var(--field-border-hover)] focus-ring"
                            />
                        </div>

                        <div className="space-y-2">
                            <label className="type-eyebrow">
                                Primary Key Column
                            </label>
                            <select
                                value={effectivePrimaryKey}
                                onChange={(event) => setPrimaryKey(event.target.value)}
                                className={`table-builder-field ${selectClass}`}
                            >
                                {columns.map((column, index) => (
                                    <option key={`${column.name}-${index}`} value={column.name}>
                                        {column.name || '(Unnamed column)'}
                                    </option>
                                ))}
                            </select>
                        </div>

                        <ResourceVisibilitySelect
                            value={visibility}
                            resourceLabel="tables"
                            resourceName={tableName || 'New table'}
                            onChange={setVisibility}
                        />

                        <div className="space-y-2">
                            <label className="type-eyebrow">
                                Row access
                            </label>
                            <div className="grid gap-2 sm:grid-cols-2">
                                <button
                                    type="button"
                                    onClick={() => setEnableRls(true)}
                                    aria-pressed={enableRls}
                                    className={cn(
                                        'table-builder-rls-option rounded-lg border p-3 text-left transition-gentle focus-ring',
                                        enableRls
                                            ? 'border-[var(--action-primary)] bg-[var(--action-primary-soft)]'
                                            : 'border-[var(--field-border)] bg-[var(--field-bg)] hover:border-[var(--field-border-hover)]',
                                    )}
                                >
                                    <span className="block text-sm font-medium text-[var(--text-primary)]">
                                        Private rows
                                    </span>
                                    <span className="mt-0.5 block text-xs text-[var(--text-tertiary)]">
                                        Each member sees and edits only their own rows. Admins see all.
                                    </span>
                                </button>
                                <button
                                    type="button"
                                    onClick={() => setEnableRls(false)}
                                    aria-pressed={!enableRls}
                                    className={cn(
                                        'table-builder-rls-option rounded-lg border p-3 text-left transition-gentle focus-ring',
                                        !enableRls
                                            ? 'border-[var(--action-primary)] bg-[var(--action-primary-soft)]'
                                            : 'border-[var(--field-border)] bg-[var(--field-bg)] hover:border-[var(--field-border-hover)]',
                                    )}
                                >
                                    <span className="block text-sm font-medium text-[var(--text-primary)]">
                                        Shared rows
                                    </span>
                                    <span className="mt-0.5 block text-xs text-[var(--text-tertiary)]">
                                        All members see and edit every row in this table.
                                    </span>
                                </button>
                            </div>
                        </div>

                        <div className="space-y-3">
                            <div className="flex items-center justify-between">
                                <label className="type-eyebrow">
                                    Columns <span className="font-normal text-[var(--text-tertiary)]">({columns.length})</span>
                                </label>
                                <Button
                                    type="button"
                                    size="sm"
                                    variant="ghost"
                                    onClick={addColumn}
                                    className="h-8 gap-1 text-xs text-[var(--action-primary)] hover:bg-[var(--surface-2)] hover:text-[var(--action-primary)]"
                                >
                                    <Plus className="h-3.5 w-3.5" />
                                    Add Column
                                </Button>
                            </div>

                            <div className="space-y-2">
                                {columns.map((column, index) => (
                                    <div
                                        key={`${column.name}-${index}`}
                                        className="group/column rounded-lg border border-[var(--row-border)] bg-[var(--row-bg)] p-3 transition-gentle hover:border-[var(--card-border)]"
                                    >
                                        <div className="flex items-center gap-2">
                                            <div className="flex w-5 shrink-0 justify-center">
                                                <GripVertical className="h-3.5 w-3.5 cursor-grab text-[var(--text-tertiary)]" />
                                            </div>

                                            <input
                                                type="text"
                                                value={column.name}
                                                onChange={(event) => updateColumn(index, 'name', event.target.value)}
                                                placeholder="column_name"
                                                required
                                                className="table-builder-field min-w-0 flex-1 rounded-md border border-[var(--field-border)] bg-[var(--field-bg)] px-2.5 py-1.5 text-sm text-[var(--text-primary)] transition-gentle placeholder:text-[var(--text-tertiary)] hover:border-[var(--field-border-hover)] focus-ring"
                                            />

                                            <div className="relative">
                                                <select
                                                    value={column.type}
                                                    onChange={(event) => updateColumn(index, 'type', event.target.value)}
                                                    className="table-builder-field w-36 cursor-pointer appearance-none rounded-md border border-[var(--field-border)] bg-[var(--field-bg)] px-2.5 py-1.5 pr-6 text-xs font-medium text-[var(--text-secondary)] transition-gentle hover:border-[var(--field-border-hover)] focus-ring"
                                                >
                                                    {FIELD_TYPES.map((type) => (
                                                        <option key={type.value} value={type.value}>
                                                            {type.icon} {type.label}
                                                        </option>
                                                    ))}
                                                </select>
                                                <ChevronDown className="pointer-events-none absolute right-1.5 top-1/2 h-3 w-3 -translate-y-1/2 text-[var(--text-tertiary)]" />
                                            </div>

                                            <button
                                                type="button"
                                                onClick={() => updateColumn(index, 'required', !column.required)}
                                                className={cn(
                                                    'chip chip-sm type-micro-label transition-gentle',
                                                    column.required
                                                        ? 'tone-action-chip'
                                                        : 'border-[var(--border-subtle)] bg-[var(--field-bg)] text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]',
                                                )}
                                            >
                                                Req
                                            </button>

                                            {index > 0 ? (
                                                <button
                                                    type="button"
                                                    onClick={() => removeColumn(index)}
                                                    className="table-builder-remove-button hover-state-error rounded-md p-1.5 text-[var(--text-tertiary)] opacity-0 transition-gentle hover:text-[var(--state-error)] group-hover/column:opacity-100"
                                                >
                                                    <Trash2 className="h-3.5 w-3.5" />
                                                </button>
                                            ) : null}
                                        </div>

                                        <div className="ml-7 mt-2">
                                            <input
                                                type="text"
                                                value={column.description}
                                                onChange={(event) => updateColumn(index, 'description', event.target.value)}
                                                placeholder="Description (optional)"
                                                className="inline-edit-field w-full rounded-md border border-transparent bg-transparent px-2.5 py-1 text-xs text-[var(--text-secondary)] transition-gentle placeholder:text-[var(--text-tertiary)] hover:border-[var(--border-subtle)] focus:border-[var(--field-border-focus)] focus:bg-[var(--field-bg)] focus-ring"
                                            />
                                        </div>

                                        {column.type === 'LINK' ? (
                                            <div className="ml-7 mt-2 grid grid-cols-2 gap-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-1)] p-2.5">
                                                <div>
                                                    <label className="mb-1 block type-eyebrow">
                                                        Table
                                                    </label>
                                                    <select
                                                        value={column.foreignKey?.table || ''}
                                                        onChange={(event) => {
                                                            updateColumn(index, 'foreignKey', {
                                                                table: event.target.value,
                                                                column: '',
                                                            });
                                                        }}
                                                        className="table-builder-field w-full rounded-md border border-[var(--field-border)] bg-[var(--field-bg)] px-2 py-1.5 text-xs text-[var(--text-primary)] transition-gentle hover:border-[var(--field-border-hover)] focus-ring"
                                                    >
                                                        <option value="">Select...</option>
                                                        {availableTables.map((table) => (
                                                            <option key={table.name} value={table.name}>
                                                                {table.name}
                                                            </option>
                                                        ))}
                                                    </select>
                                                </div>
                                                <div>
                                                    <label className="mb-1 block type-eyebrow">
                                                        Field
                                                    </label>
                                                    <select
                                                        value={column.foreignKey?.column || ''}
                                                        onChange={(event) => {
                                                            updateColumn(index, 'foreignKey', {
                                                                table: column.foreignKey?.table || '',
                                                                column: event.target.value,
                                                            });
                                                        }}
                                                        className="table-builder-field w-full rounded-md border border-[var(--field-border)] bg-[var(--field-bg)] px-2 py-1.5 text-xs text-[var(--text-primary)] transition-gentle hover:border-[var(--field-border-hover)] focus-ring disabled:cursor-not-allowed disabled:opacity-60"
                                                        disabled={!column.foreignKey?.table}
                                                    >
                                                        <option value="">Select...</option>
                                                        {availableTables
                                                            .find((table) => table.name === column.foreignKey?.table)
                                                            ?.columns.map((tableColumn) => (
                                                                <option key={tableColumn.name} value={tableColumn.name}>
                                                                    {tableColumn.name}
                                                                </option>
                                                            ))}
                                                    </select>
                                                </div>
                                            </div>
                                        ) : null}

                                        {column.type === 'ENUM' ? (
                                            <div className="ml-7 mt-2">
                                                <EnumOptionManager
                                                    options={column.options || []}
                                                    onChange={(options) => updateColumn(index, 'options', options)}
                                                />
                                            </div>
                                        ) : null}

                                        {!column.computed && ['INTEGER', 'SERIAL', 'UUID', 'DATETIME'].includes(column.type) ? (
                                            <div className="ml-7 mt-2">
                                                <label className="inline-flex cursor-pointer items-center gap-1.5 text-xs text-[var(--text-secondary)] transition-gentle hover:text-[var(--text-primary)]">
                                                    <input
                                                        type="checkbox"
                                                        checked={Boolean(column.auto)}
                                                        onChange={(event) => updateColumn(index, 'auto', event.target.checked)}
                                                        className="h-3 w-3 rounded border-[var(--field-border)] text-[var(--action-primary)]"
                                                    />
                                                    <span>Auto-generate</span>
                                                </label>
                                            </div>
                                        ) : null}

                                        {!column.auto && column.type !== 'LINK' && column.type !== 'ENUM' ? (
                                            <div className="ml-7 mt-2">
                                                <label className="inline-flex cursor-pointer items-center gap-1.5 text-xs text-[var(--text-secondary)] transition-gentle hover:text-[var(--text-primary)]">
                                                    <input
                                                        type="checkbox"
                                                        checked={Boolean(column.computed)}
                                                        onChange={(event) => {
                                                            updateColumn(index, 'computed', event.target.checked);
                                                            if (event.target.checked) {
                                                                updateColumn(index, 'required', false);
                                                            }
                                                        }}
                                                        className="h-3 w-3 rounded border-[var(--field-border)] text-[var(--action-primary)]"
                                                    />
                                                    <Sparkles className="h-3 w-3 text-[var(--action-primary)]" />
                                                    <span>Computed (SQL)</span>
                                                </label>

                                                {column.computed ? (
                                                    <div className="mt-2">
                                                        <ExpressionBuilder
                                                            availableFields={columns.map((tableColumn) => tableColumn.name)}
                                                            value={column.expression || ''}
                                                            onChange={(expression) => updateColumn(index, 'expression', expression)}
                                                        />
                                                    </div>
                                                ) : null}
                                            </div>
                                        ) : null}
                                    </div>
                                ))}
                            </div>
                        </div>
                    </div>

                    <div className="flex items-center justify-end gap-2 border-t border-[color:var(--border-subtle)] bg-[var(--bg-subtle)] px-6 py-4">
                        <Button variant="ghost" onClick={onClose}>
                            Cancel
                        </Button>
                        <Button type="submit" disabled={createTableMutation.isPending}>
                            {createTableMutation.isPending ? 'Creating...' : 'Create Table'}
                        </Button>
                    </div>
                </form>
            </SheetContent>
        </Sheet>
    );
}
