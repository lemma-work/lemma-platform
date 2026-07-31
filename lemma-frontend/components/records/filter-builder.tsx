'use client';

import { useState } from 'react';
import { Plus, Trash2 } from '@/components/ui/icons';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import type { Column } from '@/lib/types';
import type { FilterRule } from '@/lib/types/app';

interface FilterBuilderProps {
    columns: Column[];
    filters: FilterRule[];
    onFiltersChange: (filters: FilterRule[]) => void;
    onClose: () => void;
}

const OPERATORS: Array<{ value: FilterRule['operator']; label: string }> = [
    { value: 'eq', label: 'is' },
    { value: 'ne', label: 'is not' },
    { value: 'gt', label: '>' },
    { value: 'gte', label: '>=' },
    { value: 'lt', label: '<' },
    { value: 'lte', label: '<=' },
    { value: 'contains', label: 'contains' },
    { value: 'startsWith', label: 'starts with' },
    { value: 'endsWith', label: 'ends with' },
    { value: 'in', label: 'in' },
];

const DEFAULT_OPERATOR: FilterRule['operator'] = 'eq';

function createFilter(columns: Column[]): FilterRule {
    return {
        field: columns[0]?.name || '',
        operator: DEFAULT_OPERATOR,
        value: '',
    };
}

function isOperator(value: string): value is FilterRule['operator'] {
    return OPERATORS.some((operator) => operator.value === value);
}

function valueToInput(value: unknown): string {
    if (value === null || value === undefined) return '';
    if (typeof value === 'string') return value;
    return String(value);
}

export function FilterBuilder({ columns, filters, onFiltersChange, onClose }: FilterBuilderProps) {
    const [localFilters, setLocalFilters] = useState<FilterRule[]>(
        filters.length > 0 ? filters : [createFilter(columns)],
    );

    const addFilter = () => {
        setLocalFilters((previous) => [...previous, createFilter(columns)]);
    };

    const removeFilter = (index: number) => {
        setLocalFilters((previous) => previous.filter((_, filterIndex) => filterIndex !== index));
    };

    const updateFilter = <K extends keyof FilterRule>(index: number, key: K, value: FilterRule[K]) => {
        setLocalFilters((previous) => {
            const updated = [...previous];
            updated[index] = { ...updated[index], [key]: value };
            return updated;
        });
    };

    const applyFilters = () => {
        onFiltersChange(localFilters.filter((filter) => valueToInput(filter.value).trim() !== ''));
        onClose();
    };

    const inputClass =
        'w-full rounded-md border border-[color:var(--row-border)] bg-[var(--surface-1)] px-3 py-2 text-sm text-[var(--text-primary)] transition-gentle placeholder:text-[var(--text-tertiary)] hover:border-[color:var(--border-strong)] focus-ring';

    return (
        <Dialog open={true} onOpenChange={(open) => !open && onClose()}>
            <DialogContent className="max-w-2xl gap-0 overflow-hidden border-[color:var(--border-subtle)] bg-[var(--surface-1)] p-0">
                <div className="border-b border-[color:var(--border-subtle)] px-6 py-5">
                    <DialogHeader>
                        <DialogTitle className="font-display text-xl font-semibold text-[var(--text-primary)]">
                            Filter Records
                        </DialogTitle>
                        <DialogDescription className="mt-1 type-eyebrow-medium">
                            Refine your view by adding conditions
                        </DialogDescription>
                    </DialogHeader>
                </div>

                <div className="max-h-[60vh] space-y-4 overflow-y-auto p-6">
                    {localFilters.map((filter, index) => (
                        <div key={`filter-${index}`} className="group flex items-center gap-2">
                            <div className="grid flex-1 grid-cols-12 gap-2">
                                <div className="col-span-4">
                                    <select
                                        value={filter.field}
                                        onChange={(event) => updateFilter(index, 'field', event.target.value)}
                                        className={`filter-builder-field ${inputClass}`}
                                    >
                                        {columns.map((column) => (
                                            <option key={column.name} value={column.name}>
                                                {column.name}
                                            </option>
                                        ))}
                                    </select>
                                </div>

                                <div className="col-span-3">
                                    <select
                                        value={filter.operator}
                                        onChange={(event) => {
                                            const nextOperator = event.target.value;
                                            if (isOperator(nextOperator)) {
                                                updateFilter(index, 'operator', nextOperator);
                                            }
                                        }}
                                        className={`filter-builder-field ${inputClass}`}
                                    >
                                        {OPERATORS.map((operator) => (
                                            <option key={operator.value} value={operator.value}>
                                                {operator.label}
                                            </option>
                                        ))}
                                    </select>
                                </div>

                                <div className="col-span-5">
                                    <input
                                        type="text"
                                        value={valueToInput(filter.value)}
                                        onChange={(event) => updateFilter(index, 'value', event.target.value)}
                                        placeholder="Value"
                                        className={`filter-builder-field ${inputClass}`}
                                    />
                                </div>
                            </div>

                            <button
                                type="button"
                                onClick={() => removeFilter(index)}
                                className="filter-builder-remove-button hover-state-error rounded-lg p-2 text-[var(--text-tertiary)] transition-gentle hover:text-[var(--state-error)]"
                            >
                                <Trash2 className="h-4 w-4" />
                            </button>
                        </div>
                    ))}

                    <Button
                        variant="ghost"
                        size="sm"
                        onClick={addFilter}
                        className="mt-2 w-full border border-dashed border-[color:var(--row-border)] text-[var(--text-secondary)] hover:bg-[var(--bg-subtle)] hover:text-[var(--text-primary)]"
                    >
                        <Plus className="mr-2 h-4 w-4" />
                        Add Condition
                    </Button>
                </div>

                <div className="flex items-center justify-end gap-3 border-t border-[color:var(--border-subtle)] bg-[var(--bg-subtle)] px-6 py-5">
                    <Button variant="ghost" onClick={onClose}>
                        Cancel
                    </Button>
                    <Button onClick={applyFilters}>Apply Filters</Button>
                </div>
            </DialogContent>
        </Dialog>
    );
}
