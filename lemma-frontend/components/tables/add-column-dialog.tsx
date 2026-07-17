'use client';

import { useState } from 'react';
import { Sparkles } from '@/components/ui/icons';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import type { Table } from '@/lib/types';
import { EnumOptionManager } from './enum-option-manager';
import { ExpressionBuilder } from './expression-builder';

interface AddColumnDialogProps {
    podId: string;
    datastoreName: string;
    tableName: string;
    onClose: () => void;
    availableTables: Table[];
}

interface AddColumnState {
    name: string;
    type: string;
    required: boolean;
    unique: boolean;
    description: string;
    default?: unknown;
    foreignKey?: {
        table: string;
        column: string;
    };
    options: string[];
    auto: boolean;
    computed: boolean;
    expression: string;
}

interface AddColumnPayload {
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
    { value: 'LINK', label: 'Link', icon: 'REF' },
];

export function AddColumnDialog({ podId, datastoreName, tableName, onClose, availableTables }: AddColumnDialogProps) {
    const queryClient = useQueryClient();
    const [column, setColumn] = useState<AddColumnState>({
        name: '',
        type: 'TEXT',
        required: false,
        unique: false,
        description: '',
        default: undefined,
        foreignKey: undefined,
        options: [],
        auto: false,
        computed: false,
        expression: '',
    });

    const addColumnMutation = useMutation({
        mutationFn: (data: AddColumnPayload) =>
            getLemmaClient(podId).tables.columns.add(tableName, data as unknown as never),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['table', podId, datastoreName, tableName] });
            queryClient.invalidateQueries({ queryKey: ['records', podId, datastoreName, tableName] });
            onClose();
        },
    });

    const updateColumn = <K extends keyof AddColumnState>(field: K, value: AddColumnState[K]) => {
        setColumn((prev) => ({ ...prev, [field]: value }));
    };

    const handleSubmit = (event: React.FormEvent) => {
        event.preventDefault();

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

        const payload: AddColumnPayload = {
            name: column.name.trim(),
            type: mappedType,
            description: column.description.trim() || undefined,
            required: column.required && !column.computed,
            unique: column.unique,
            default: column.auto || column.computed ? undefined : column.default,
            foreign_key: foreignKey,
            options: column.type === 'ENUM' ? column.options.filter(Boolean) : undefined,
            auto: column.auto || undefined,
            computed: column.computed || undefined,
            expression: column.computed ? column.expression.trim() || undefined : undefined,
        };

        addColumnMutation.mutate(payload);
    };

    const inputClass =
        'w-full rounded-md border border-[color:var(--field-border)] bg-[var(--field-bg)] px-3 py-2.5 text-sm text-[var(--text-primary)] transition-gentle placeholder:text-[var(--text-tertiary)] hover:border-[color:var(--field-border-hover)] focus-ring';

    const expressionFields =
        availableTables.find((table) => table.name === tableName)?.columns.map((tableColumn) => tableColumn.name) || [];

    return (
        <Dialog open={true} onOpenChange={(open) => !open && onClose()}>
            <DialogContent className="max-h-[85vh] overflow-y-auto border-[color:var(--card-border-subtle)] bg-[var(--card-bg)] p-0 sm:max-w-lg">
                <DialogHeader className="border-b border-[color:var(--border-subtle)] px-5 pb-3 pt-5">
                    <DialogTitle className="font-display text-lg font-semibold text-[var(--text-primary)]">
                        Add New Column
                    </DialogTitle>
                </DialogHeader>

                <form onSubmit={handleSubmit} className="space-y-4 p-5">
                    <div className="space-y-1.5">
                        <label className="type-eyebrow">Name</label>
                        <input
                            type="text"
                            value={column.name}
                            onChange={(event) => updateColumn('name', event.target.value)}
                            placeholder="column_name"
                            required
                            autoFocus
                            className={`add-column-field ${inputClass}`}
                        />
                    </div>

                    <div className="space-y-1.5">
                        <label className="type-eyebrow">Type</label>
                        <div className="grid grid-cols-3 gap-1.5">
                            {FIELD_TYPES.map((type) => (
                                <button
                                    key={type.value}
                                    type="button"
                                    onClick={() => updateColumn('type', type.value)}
                                    className={
                                        column.type === type.value
                                            ? 'tone-action-chip flex items-center gap-1.5 rounded-lg border px-2.5 py-2 text-xs font-medium transition-gentle'
                                            : 'flex items-center gap-1.5 rounded-lg border border-[color:var(--row-border)] bg-[var(--row-bg)] px-2.5 py-2 text-xs font-medium text-[var(--text-secondary)] transition-gentle hover:border-[color:var(--field-border-hover)] hover:bg-[var(--row-bg-hover)]'
                                    }
                                >
                                    <span className="font-mono text-xs">{type.icon}</span>
                                    <span>{type.label}</span>
                                </button>
                            ))}
                        </div>
                    </div>

                    <div className="space-y-1.5">
                        <label className="type-eyebrow">
                            Description
                        </label>
                        <input
                            type="text"
                            value={column.description}
                            onChange={(event) => updateColumn('description', event.target.value)}
                            placeholder="Optional description"
                            className={`add-column-field ${inputClass}`}
                        />
                    </div>

                    <div className="flex items-center gap-4">
                        <label className="inline-flex cursor-pointer select-none items-center gap-2 text-sm text-[var(--text-secondary)]">
                            <input
                                type="checkbox"
                                checked={column.required}
                                onChange={(event) => updateColumn('required', event.target.checked)}
                                className="h-4 w-4 rounded border-[color:var(--field-border)] text-[var(--action-primary)]"
                            />
                            <span>Required</span>
                        </label>
                    </div>

                    {column.type === 'LINK' ? (
                        <div className="space-y-3 rounded-lg border border-[color:var(--border-subtle)] bg-[var(--bg-subtle)] p-3">
                            <label className="type-eyebrow">
                                Link Configuration
                            </label>
                            <div className="grid grid-cols-2 gap-2">
                                <div className="space-y-1">
                                    <label className="text-xs font-medium text-[var(--text-tertiary)]">Table</label>
                                    <select
                                        value={column.foreignKey?.table || ''}
                                        className="add-column-field w-full rounded-md border border-[color:var(--field-border)] bg-[var(--field-bg)] px-2.5 py-2 text-sm text-[var(--text-primary)] transition-gentle hover:border-[color:var(--field-border-hover)] focus-ring"
                                        onChange={(event) => {
                                            updateColumn('foreignKey', {
                                                table: event.target.value,
                                                column: '',
                                            });
                                        }}
                                    >
                                        <option value="">Select table...</option>
                                        {availableTables.map((table) => (
                                            <option key={table.name} value={table.name}>
                                                {table.name}
                                            </option>
                                        ))}
                                    </select>
                                </div>
                                <div className="space-y-1">
                                    <label className="text-xs font-medium text-[var(--text-tertiary)]">Field</label>
                                    <select
                                        value={column.foreignKey?.column || ''}
                                        className="add-column-field w-full rounded-md border border-[color:var(--field-border)] bg-[var(--field-bg)] px-2.5 py-2 text-sm text-[var(--text-primary)] transition-gentle hover:border-[color:var(--field-border-hover)] focus-ring disabled:cursor-not-allowed disabled:opacity-60"
                                        onChange={(event) => {
                                            updateColumn('foreignKey', {
                                                table: column.foreignKey?.table || '',
                                                column: event.target.value,
                                            });
                                        }}
                                        disabled={!column.foreignKey?.table}
                                    >
                                        <option value="">Select field...</option>
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
                        </div>
                    ) : null}

                    {column.type === 'ENUM' ? (
                        <div className="space-y-1.5">
                            <label className="type-eyebrow">Options</label>
                            <EnumOptionManager
                                options={column.options}
                                onChange={(options) => updateColumn('options', options)}
                            />
                        </div>
                    ) : null}

                    {['INTEGER', 'SERIAL', 'UUID', 'DATETIME'].includes(column.type) && !column.computed ? (
                        <label className="inline-flex cursor-pointer select-none items-center gap-2 text-sm text-[var(--text-secondary)]">
                            <input
                                type="checkbox"
                                checked={column.auto}
                                onChange={(event) => updateColumn('auto', event.target.checked)}
                                className="h-4 w-4 rounded border-[color:var(--field-border)] text-[var(--action-primary)]"
                            />
                            <span>Auto-generate value</span>
                            <span className="text-xs text-[var(--text-tertiary)]">(System managed)</span>
                        </label>
                    ) : null}

                    {!column.auto && column.type !== 'LINK' && column.type !== 'ENUM' ? (
                        <div className="space-y-2">
                            <label className="inline-flex cursor-pointer select-none items-center gap-2 text-sm text-[var(--text-secondary)]">
                                <input
                                    type="checkbox"
                                    checked={column.computed}
                                    onChange={(event) => {
                                        updateColumn('computed', event.target.checked);
                                        if (event.target.checked) {
                                            updateColumn('required', false);
                                        }
                                    }}
                                    className="h-4 w-4 rounded border-[color:var(--field-border)] text-[var(--action-primary)]"
                                />
                                <Sparkles className="h-3.5 w-3.5 text-[var(--action-primary)]" />
                                <span>Computed field</span>
                                <span className="text-xs text-[var(--text-tertiary)]">(SQL expression)</span>
                            </label>
                            {column.computed ? (
                                <div className="pl-6">
                                    <ExpressionBuilder
                                        availableFields={expressionFields}
                                        value={column.expression}
                                        onChange={(expression) => updateColumn('expression', expression)}
                                    />
                                </div>
                            ) : null}
                        </div>
                    ) : null}

                    <DialogFooter className="-mx-5 -mb-5 border-t border-[color:var(--border-subtle)] bg-[var(--bg-subtle)] px-5 pb-5 pt-3">
                        <Button type="button" variant="ghost" onClick={onClose}>
                            Cancel
                        </Button>
                        <Button type="submit" disabled={addColumnMutation.isPending}>
                            {addColumnMutation.isPending ? 'Adding...' : 'Add Column'}
                        </Button>
                    </DialogFooter>
                </form>
            </DialogContent>
        </Dialog>
    );
}
