'use client';

import { useId, useState } from 'react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
    Sheet,
    SheetContent,
    SheetHeader,
    SheetTitle,
} from "@/components/ui/sheet";
import type { Column } from '@/lib/types';
import { useFileUpload } from '@/lib/hooks/use-files';
import { useDatastoreQuery, useTable, useCreateRecord } from '@/lib/hooks/use-datastores';
import { Check, Upload, X, FileIcon, Loader2 } from '@/components/ui/icons';
import { useQueryClient } from '@tanstack/react-query';
import { sanitizeRecordPayload } from '@/lib/utils/datastore-records';

type ExtendedColumn = Column;

interface RecordEditorProps {
    columns: ExtendedColumn[];
    record?: Record<string, unknown>;
    onSave: (data: Record<string, unknown>) => void;
    onClose: () => void;
    podId?: string;
    datastoreName?: string;
}

// Type badge colors
const TYPE_COLORS: Record<string, string> = {
    'TEXT': 'state-badge-info',
    'INTEGER': 'state-badge-success',
    'FLOAT': 'state-badge-success',
    'BOOLEAN': 'state-badge-info',
    'DATE': 'state-badge-warning',
    'DATETIME': 'state-badge-warning',
    'ENUM': 'state-badge-error',
    'JSON': 'chip-muted',
    'LINK': 'state-badge-info',
};

export function RecordEditor({ columns, record, onSave, onClose, podId, datastoreName }: RecordEditorProps) {
    const [formData, setFormData] = useState<Record<string, unknown>>(
        record || columns.reduce((acc, col) => {
            if (col.default !== undefined) {
                acc[col.name] = col.default;
            }
            return acc;
        }, {} as Record<string, unknown>)
    );

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();

        const filteredData = sanitizeRecordPayload(columns, formData, {
            omitAutoComputed: true,
        });

        onSave(filteredData);
    };

    return (
        <Sheet open={true} onOpenChange={(open) => !open && onClose()}>
            <SheetContent className="flex w-full max-w-md flex-col gap-0 border-l border-[color:var(--card-border-subtle)] bg-[var(--card-bg)] p-0 shadow-[var(--shadow-lg)] sm:max-w-lg">
                <div className="flex items-center justify-between border-b border-[color:var(--border-subtle)] px-5 py-4">
                    <SheetHeader>
                        <SheetTitle className="font-display text-lg font-semibold text-[var(--text-primary)]">
                            {record ? 'Edit Record' : 'New Record'}
                        </SheetTitle>
                    </SheetHeader>
                </div>

                <div className="flex-1 overflow-y-auto">
                    <form id="record-form" onSubmit={handleSubmit} className="px-5 py-5 space-y-4">
                        {columns
                            .filter(col => !col.auto && !col.computed) // Hide auto and computed fields
                            .map((column) => {
                                const typeColor = TYPE_COLORS[String(column.type).toUpperCase()] || TYPE_COLORS['TEXT'];
                                return (
                                    <div key={column.name} className="space-y-1.5">
                                        <label className="flex items-center gap-2 text-xs font-semibold text-[var(--text-tertiary)] uppercase tracking-wide">
                                            <span>{column.name}</span>
                                            {column.required && <span className="text-[var(--action-primary)]">*</span>}
                                            <span className={`text-xs px-1.5 py-0.5 rounded font-medium normal-case ${typeColor}`}>
                                                {String(column.type).toLowerCase()}
                                            </span>
                                        </label>

                                        <div>
                                            <FormField
                                                column={column}
                                                value={formData[column.name]}
                                                onChange={(val) => setFormData(prev => ({ ...prev, [column.name]: val }))}
                                                podId={podId}
                                                datastoreName={datastoreName}
                                            />
                                        </div>

                                        {column.description && <p className="text-xs text-[var(--text-tertiary)]">{column.description}</p>}
                                    </div>
                                );
                            })}
                    </form>
                </div>

                <div className="flex items-center justify-end gap-2 border-t border-[color:var(--border-subtle)] bg-[var(--bg-subtle)] px-5 py-4">
                    <Button variant="ghost" onClick={onClose}>
                        Cancel
                    </Button>
                    <Button type="submit" form="record-form">
                        {record ? 'Update Record' : 'Create Record'}
                    </Button>
                </div>
            </SheetContent>
        </Sheet>
    );
}

function FormField({
    column,
    value,
    onChange,
    podId,
    datastoreName,
}: {
    column: ExtendedColumn;
    value: unknown;
    onChange: (val: unknown) => void;
    podId?: string;
    datastoreName?: string;
}) {
    if (column.foreign_key) {
        if (podId && datastoreName) {
            return <ForeignKeyField column={column} value={value} onChange={onChange} podId={podId} datastoreName={datastoreName} />;
        }
    }

    const type = String(column.type).toUpperCase();
    const inputClass = "w-full rounded-md border border-[color:var(--field-border)] bg-[var(--field-bg)] px-3 py-2 text-sm text-[var(--text-primary)] transition-gentle placeholder:text-[var(--text-tertiary)] hover:border-[color:var(--field-border-hover)] focus-ring";

    // Handle ENUM type
    if (type === 'ENUM' && column.options && Array.isArray(column.options) && column.options.length > 0) {
        return (
            <select
                value={typeof value === 'string' || typeof value === 'number' ? value : ''}
                onChange={(e) => onChange(e.target.value)}
                className={`record-editor-field ${inputClass} cursor-pointer`}
            >
                <option value="">Select option...</option>
                {column.options.map((opt) => (
                    <option key={opt} value={opt}>
                        {opt}
                    </option>
                ))}
            </select>
        );
    }

    if (type === 'SELECT' && column.options) {
        return (
            <select
                value={typeof value === 'string' || typeof value === 'number' ? value : ''}
                onChange={(e) => onChange(e.target.value)}
                className={`record-editor-field ${inputClass} cursor-pointer`}
            >
                <option value="">Select option...</option>
                {column.options.map(opt => (
                    <option key={opt} value={opt}>{opt}</option>
                ))}
            </select>
        );
    }

    switch (type) {
        case 'TEXT':
        case 'UUID':
            return (
                <input
                    type="text"
                    value={String(value ?? '')}
                    onChange={(e) => onChange(e.target.value)}
                    className={`record-editor-field ${inputClass}`}
                    placeholder={`Enter ${column.name.toLowerCase()}...`}
                />
            );
        case 'INTEGER':
        case 'SERIAL':
            return (
                <input
                    type="number"
                    value={typeof value === 'number' ? value : ''}
                    onChange={(e) => onChange(e.target.value ? parseInt(e.target.value, 10) : null)}
                    className={`record-editor-field ${inputClass} tabular-nums`}
                    placeholder="0"
                />
            );
        case 'FLOAT':
            return (
                <input
                    type="number"
                    step="0.01"
                    value={typeof value === 'number' ? value : ''}
                    onChange={(e) => onChange(e.target.value ? parseFloat(e.target.value) : null)}
                    className={`record-editor-field ${inputClass} tabular-nums`}
                    placeholder="0.00"
                />
            );
        case 'BOOLEAN':
            return (
                <div className="flex items-center h-10">
                    <button
                        type="button"
                        onClick={() => onChange(!Boolean(value))}
                        className={`
                            record-editor-boolean-button flex h-5 w-5 items-center justify-center rounded-md border-2 transition-[background-color,border-color,color]
                            ${Boolean(value)
                                ? 'border-[var(--action-primary)] bg-[var(--action-primary)] text-[var(--text-on-brand)]'
                                : 'border-[color:var(--field-border)] bg-[var(--field-bg)] hover:border-[color:var(--field-border-hover)]'
                            }
                        `}
                    >
                        {Boolean(value) && (
                            <Check className="size-3" weight="bold" aria-hidden="true" />
                        )}
                    </button>
                    <span className="ml-2 text-sm text-[var(--text-secondary)]">{Boolean(value) ? 'Yes' : 'No'}</span>
                </div>
            );
        case 'DATE':
        case 'DATETIME':
        case 'TIMESTAMP':
            return (
                <input
                    type={type === 'DATE' ? 'date' : 'datetime-local'}
                    value={typeof value === 'string' || typeof value === 'number' ? value : ''}
                    onChange={(e) => onChange(e.target.value)}
                    className={`record-editor-field ${inputClass}`}
                />
            );
        case 'JSON':
            return (
                <textarea
                    value={typeof value === 'string' ? value : JSON.stringify(value ?? {}, null, 2)}
                    onChange={(e) => onChange(e.target.value)}
                    rows={4}
                    className={`record-editor-field ${inputClass} font-mono text-xs`}
                    placeholder="{}"
                />
            );
        case 'FILE':
        case 'ATTACHMENT':
            return (
                <FileField
                    value={value}
                    onChange={onChange}
                    podId={podId}
                />
            );
        default:
            // Auto-detect file fields by name if type is generic text
            if (['image', 'file', 'attachment', 'avatar', 'logo', 'document'].some(k => column.name.toLowerCase().includes(k))) {
                return (
                    <FileField
                        value={value}
                        onChange={onChange}
                        podId={podId}
                    />
                );
            }

            return (
                <input
                    type="text"
                    value={String(value ?? '')}
                    onChange={(e) => onChange(e.target.value)}
                    className={`record-editor-field ${inputClass}`}
                />
            );
    }
}

function ForeignKeyField({
    column,
    value,
    onChange,
    podId,
    datastoreName,
}: {
    column: ExtendedColumn;
    value: unknown;
    onChange: (val: unknown) => void;
    podId: string;
    datastoreName: string;
}) {
    const [isCreating, setIsCreating] = useState(false);
    const queryClient = useQueryClient();

    const ref = column.foreign_key?.references || '';
    // Format: "Table(Column)"
    const match = ref.match(/^(.+)\((.+)\)$/);
    const tableName = match ? match[1] : ref;
    const refCol = match ? match[2] : 'id';

    const { data: records, isLoading } = useDatastoreQuery(podId, datastoreName, tableName, undefined, undefined, 1, 100);
    const { data: refTable } = useTable(podId, datastoreName, tableName);
    const createRecordMutation = useCreateRecord();

    const inputClass = "w-full rounded-md border border-[color:var(--field-border)] bg-[var(--field-bg)] px-3 py-2 text-sm text-[var(--text-primary)] transition-gentle hover:border-[color:var(--field-border-hover)] focus-ring";

    if (isLoading) {
        return <div className="flex items-center gap-2 p-2 text-xs text-[var(--text-tertiary)]"><Loader2 className="h-3 w-3 animate-spin" /> Loading...</div>;
    }

    return (
        <>
            <select
                value={String(value ?? '')}
                onChange={(e) => {
                    if (e.target.value === '__create_new__') {
                        setIsCreating(true);
                    } else {
                        onChange(e.target.value);
                    }
                }}
                className={`record-editor-field ${inputClass} cursor-pointer`}
            >
                <option value="">Select {tableName}...</option>
                {records?.map((row) => {
                    const record = row as Record<string, unknown>;
                    const label = record.name || record.title || record.label || record.description || record[refCol] || record.id;
                    return <option key={String(record[refCol] || record.id)} value={String(record[refCol] ?? '')}>{String(label ?? '')}</option>
                })}
                <option value="__create_new__" className="font-semibold text-[var(--action-primary)]">+ Create New</option>
            </select>

            {isCreating && refTable && (
                <RecordEditor
                    columns={refTable.columns}
                    onSave={async (data) => {
                        try {
                            const result = await createRecordMutation.mutateAsync({
                                podId,
                                datastoreName,
                                tableName,
                                data
                            });
                            // Set the selected value to the referenced column of the new record
                            const newValue = (result as Record<string, unknown>)[refCol];
                            onChange(newValue);
                            setIsCreating(false);
                            // Invalidate to refresh the dropdown
                            queryClient.invalidateQueries({ queryKey: ['datastore-query', podId, datastoreName, tableName] });
                        } catch (e) {
                            console.error("Failed to create referenced record", e);
                            toast.error(e instanceof Error ? e.message : 'Failed to create record. Please try again.');
                        }
                    }}
                    onClose={() => setIsCreating(false)}
                    podId={podId}
                    datastoreName={datastoreName}
                />
            )}
        </>
    );
}

function FileField({ value, onChange, podId }: { value: unknown; onChange: (val: string) => void; podId?: string }) {
    const inputId = useId();
    const { upload, isUploading } = useFileUpload({
        podId: podId || '',
    });

    const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file || !podId) return;

        try {
            const url = await upload(file);
            onChange(url);
        } catch (error) {
            console.error('Failed to upload file', error);
            toast.error(error instanceof Error ? error.message : 'Failed to upload file. Please try again.');
        }
    };

    if (value) {
        const fileValue = String(value);
        return (
            <div className="flex items-center gap-2 rounded-lg border border-[color:var(--border-subtle)] bg-[var(--bg-subtle)] p-2.5">
                <div className="flex h-8 w-8 items-center justify-center rounded-md border border-[color:var(--chip-border)] bg-[var(--chip-bg)] shadow-[var(--shadow-xs)]">
                    <FileIcon className="h-4 w-4 text-[var(--action-primary)]" />
                </div>
                <div className="flex-1 min-w-0">
                    <p className="truncate text-xs font-medium text-[var(--text-secondary)]">{fileValue.split('/').pop()}</p>
                </div>
                <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="hover-state-error h-6 w-6 text-[var(--text-tertiary)] hover:text-[var(--state-error)]"
                    onClick={() => onChange('')}
                >
                    <X className="w-3 h-3" />
                </Button>
            </div>
        );
    }

    return (
        <div className="relative">
            <input
                type="file"
                className="hidden"
                id={inputId}
                onChange={handleFileChange}
                disabled={!podId || isUploading}
            />
            <label
                htmlFor={inputId}
                className={`flex w-full cursor-pointer items-center justify-center gap-2 rounded-lg border border-dashed border-[color:var(--row-border)] bg-[var(--row-bg)] px-4 py-3 text-sm text-[var(--text-secondary)] transition-gentle hover:bg-[var(--row-bg-hover)] hover:border-[color:var(--field-border-hover)] ${!podId ? 'cursor-not-allowed opacity-50' : ''}`}
            >
                {isUploading ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                    <Upload className="w-4 h-4" />
                )}
                {isUploading ? 'Uploading...' : 'Upload File'}
            </label>
        </div>
    );
}
