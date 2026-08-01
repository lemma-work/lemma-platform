'use client';

import type { Column } from '@/lib/types';
import { EmptyState } from '@/components/shared/empty-state';
import { cn } from '@/lib/utils';
import { getDisplayColumns } from '@/lib/utils/datastore-records';

type TableRecord = Record<string, unknown>;

interface ListViewProps {
    records: TableRecord[];
    table: { columns: Column[]; primary_key_column: string };
    onRecordClick: (record: TableRecord) => void;
    selectedRecords: Set<string>;
    onSelectRecord: (id: string) => void;
}

export function ListView({ records, table, onRecordClick, selectedRecords, onSelectRecord }: ListViewProps) {
    if (records.length === 0) {
        return (
            <EmptyState
                variant="region"
                title="No records yet"
                description="Create a record when there is real pod data to track."
                className="my-4"
            />
        );
    }

    const displayColumns = getDisplayColumns(table.columns, table.primary_key_column);
    const primaryField = displayColumns[0] || table.columns[0];
    const secondaryFields = displayColumns.slice(1, 4);

    return (
        <div className="lemma-index-list p-2">
            {records.map((record, index) => {
                const recordId = String(record[table.primary_key_column] ?? `row-${index}`);
                const isSelected = selectedRecords.has(recordId);

                return (
                    <div
                        key={recordId}
                        onClick={() => onRecordClick(record)}
                        className={cn(
                            'lemma-index-row cursor-pointer px-3 py-3',
                            isSelected && 'lemma-index-row-selected',
                        )}
                    >
                        <div className="flex items-start gap-3">
                            <div className="pt-0.5">
                                <input
                                    type="checkbox"
                                    checked={isSelected}
                                    onChange={(event) => {
                                        event.stopPropagation();
                                        onSelectRecord(recordId);
                                    }}
                                    onClick={(event) => event.stopPropagation()}
                                    className="h-4 w-4 rounded border-[color:var(--row-border)] text-[var(--action-primary)] focus:ring-[var(--action-primary-soft)] focus:ring-offset-0"
                                />
                            </div>
                            <div className="min-w-0 flex-1">
                                <h3 className="truncate text-sm font-normal text-[var(--text-primary)]">
                                    {String(record[primaryField.name] ?? '') || (
                                        <span className="text-[var(--text-tertiary)]">Untitled</span>
                                    )}
                                </h3>

                                {secondaryFields.length > 0 ? (
                                    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
                                        {secondaryFields.map((field) => (
                                            <div key={field.name} className="text-xs">
                                                <span className="text-[var(--text-tertiary)]">{field.name}:</span>{' '}
                                                <span className="font-normal text-[var(--text-secondary)]">
                                                    {record[field.name] !== null && record[field.name] !== undefined
                                                        ? String(record[field.name])
                                                        : '—'}
                                                </span>
                                            </div>
                                        ))}
                                    </div>
                                ) : null}
                            </div>
                        </div>
                    </div>
                );
            })}
        </div>
    );
}
