'use client';

import { useState } from 'react';
import { Calendar, Check, ChevronLeft, ChevronRight, Clock, Trash2 } from '@/components/ui/icons';
import { Button } from '@/components/ui/button';
import { DestructiveConfirmationDialog } from '@/components/shared/destructive-confirmation-dialog';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { cn } from '@/lib/utils';
import type { Table } from '@/lib/types';

interface RecordDetailProps {
    record: Record<string, unknown>;
    table: Table;
    onClose: () => void;
    onUpdate: (data: Record<string, unknown>) => void;
    onDelete: () => void;
    onNext?: () => void;
    onPrevious?: () => void;
    canWrite?: boolean;
}

const TYPE_BADGE_STYLES: Record<string, string> = {
    TEXT: 'chip-muted',
    INTEGER: 'state-badge-success',
    FLOAT: 'state-badge-info',
    BOOLEAN: 'state-badge-brand',
    DATE: 'state-badge-warning',
    DATETIME: 'state-badge-warning',
    ENUM: 'chip-muted',
    JSON: 'chip-muted',
    LINK: 'state-badge-brand',
};

function formatDate(value: unknown): string {
    if (typeof value !== 'string' || !value.trim()) {
        return '—';
    }

    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return '—';
    }

    return date.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    });
}

function stringifyValue(value: unknown): string {
    if (value === null || value === undefined) return '';
    return String(value);
}

export function RecordDetail({
    record,
    table,
    onClose,
    onUpdate,
    onDelete,
    onNext,
    onPrevious,
    canWrite = true,
}: RecordDetailProps) {
    const [formData, setFormData] = useState<Record<string, unknown>>(record);
    const [hasChanges, setHasChanges] = useState(false);
    const [showDeleteDialog, setShowDeleteDialog] = useState(false);

    const handleChange = (columnName: string, value: unknown) => {
        if (!canWrite) return;
        setFormData((prev) => ({ ...prev, [columnName]: value }));
        setHasChanges(true);
    };

    const handleSave = () => {
        if (!canWrite) return;
        onUpdate(formData);
        setHasChanges(false);
    };

    const inputClass =
        'w-full rounded-md border border-[color:var(--row-border)] bg-[var(--card-bg)] px-3 py-2.5 text-sm text-[var(--text-primary)] transition-gentle placeholder:text-[var(--text-tertiary)] hover:border-[color:var(--border-strong)] focus-ring';

    const headerTitleField =
        table.columns.find((column) => column.name !== table.primary_key_column)?.name || table.primary_key_column;
    const headerTitle = formData[headerTitleField] ?? record[headerTitleField] ?? 'Record Details';

    return (
        <Sheet open={true} onOpenChange={(open) => !open && onClose()}>
            <SheetContent className="flex w-full max-w-md flex-col gap-0 border-l border-[color:var(--border-subtle)] bg-[var(--card-bg)] p-0 shadow-[var(--shadow-lg)] sm:max-w-lg">
                <div className="flex items-center justify-between border-b border-[color:var(--border-subtle)] px-5 py-4">
                    <div className="flex min-w-0 flex-1 items-center gap-3">
                        <SheetHeader className="min-w-0 text-left">
                            <SheetTitle className="truncate font-display text-lg font-semibold text-[var(--text-primary)]">
                                {String(headerTitle)}
                            </SheetTitle>
                            <div className="text-xs text-[var(--text-tertiary)]">{table.name}</div>
                        </SheetHeader>
                        {hasChanges ? (
                            <span className="chip chip-pill chip-sm state-badge-warning type-micro-label shrink-0">
                                Unsaved
                            </span>
                        ) : null}
                    </div>

                    <div className="ml-3 flex items-center gap-0.5">
                        {onPrevious ? (
                            <Button
                                variant="ghost"
                                size="icon"
                                onClick={onPrevious}
                                className="h-8 w-8 rounded-lg text-[var(--text-tertiary)] hover:bg-[var(--bg-subtle)] hover:text-[var(--text-secondary)]"
                            >
                                <ChevronLeft className="h-4 w-4" />
                            </Button>
                        ) : null}
                        {onNext ? (
                            <Button
                                variant="ghost"
                                size="icon"
                                onClick={onNext}
                                className="h-8 w-8 rounded-lg text-[var(--text-tertiary)] hover:bg-[var(--bg-subtle)] hover:text-[var(--text-secondary)]"
                            >
                                <ChevronRight className="h-4 w-4" />
                            </Button>
                        ) : null}
                    </div>
                </div>

                <div className="flex-1 space-y-4 overflow-y-auto px-5 py-5">
                    {table.columns.map((column) => {
                        const fieldType = String(column.type).toUpperCase();
                        const typeBadgeClass = TYPE_BADGE_STYLES[fieldType] || TYPE_BADGE_STYLES.TEXT;
                        const fieldValue = formData[column.name];

                        return (
                            <div key={column.name} className="space-y-1.5">
                                <label className="flex items-center gap-2 type-eyebrow">
                                    <span>{column.name}</span>
                                    {column.required ? <span className="text-[var(--action-primary)]">*</span> : null}
                                    <span className={cn('chip chip-pill chip-sm normal-case', typeBadgeClass)}>
                                        {fieldType.toLowerCase()}
                                    </span>
                                </label>

                                {fieldType === 'BOOLEAN' ? (
                                    <div className="flex h-10 items-center">
                                        <button
                                            type="button"
                                            onClick={() => handleChange(column.name, !Boolean(fieldValue))}
                                            disabled={!canWrite}
                                            className={cn(
                                                'record-detail-boolean-button flex h-5 w-5 items-center justify-center rounded-md border-2 transition-gentle',
                                                Boolean(fieldValue)
                                                    ? 'border-[var(--action-primary)] bg-[var(--action-primary)] text-[var(--text-on-brand)]'
                                                    : 'border-[color:var(--row-border)] bg-[var(--card-bg)] hover:border-[color:var(--border-strong)]',
                                            )}
                                        >
                                            {Boolean(fieldValue) ? (
                                                <Check className="size-3" weight="bold" aria-hidden="true" />
                                            ) : null}
                                        </button>
                                        <span className="ml-2 text-sm text-[var(--text-secondary)]">{Boolean(fieldValue) ? 'Yes' : 'No'}</span>
                                    </div>
                                ) : null}

                                {fieldType === 'ENUM' && column.options && Array.isArray(column.options) ? (
                                    <select
                                        value={stringifyValue(fieldValue)}
                                        onChange={(event) => handleChange(column.name, event.target.value)}
                                        disabled={!canWrite}
                                        className={`record-detail-field ${inputClass} cursor-pointer`}
                                    >
                                        <option value="">Select option...</option>
                                        {column.options.map((option) => (
                                            <option key={option} value={option}>
                                                {option}
                                            </option>
                                        ))}
                                    </select>
                                ) : null}

                                {fieldType === 'DATE' || fieldType === 'DATETIME' ? (
                                    <input
                                        type={fieldType === 'DATE' ? 'date' : 'datetime-local'}
                                        value={stringifyValue(fieldValue)}
                                        onChange={(event) => handleChange(column.name, event.target.value)}
                                        readOnly={!canWrite}
                                        className={`record-detail-field ${inputClass}`}
                                    />
                                ) : null}

                                {fieldType === 'INTEGER' || fieldType === 'FLOAT' ? (
                                    <input
                                        type="number"
                                        step={fieldType === 'FLOAT' ? '0.01' : '1'}
                                        value={typeof fieldValue === 'number' || typeof fieldValue === 'string' ? fieldValue : ''}
                                        onChange={(event) =>
                                            handleChange(column.name, event.target.value ? Number(event.target.value) : null)
                                        }
                                        readOnly={!canWrite}
                                        className={`record-detail-field ${inputClass} tabular-nums`}
                                    />
                                ) : null}

                                {fieldType === 'JSON' ? (
                                    <textarea
                                        value={
                                            typeof fieldValue === 'string'
                                                ? fieldValue
                                                : JSON.stringify(fieldValue ?? {}, null, 2)
                                        }
                                        onChange={(event) => handleChange(column.name, event.target.value)}
                                        readOnly={!canWrite}
                                        rows={4}
                                        className={`record-detail-field ${inputClass} font-mono text-xs`}
                                    />
                                ) : null}

                                {!['BOOLEAN', 'ENUM', 'DATE', 'DATETIME', 'INTEGER', 'FLOAT', 'JSON'].includes(fieldType) ? (
                                    <input
                                        type="text"
                                        value={stringifyValue(fieldValue)}
                                        onChange={(event) => handleChange(column.name, event.target.value)}
                                        readOnly={!canWrite}
                                        className={`record-detail-field ${inputClass}`}
                                    />
                                ) : null}

                                {column.description ? (
                                    <p className="text-xs text-[var(--text-tertiary)]">{column.description}</p>
                                ) : null}
                            </div>
                        );
                    })}

                    <div className="mt-5 border-t border-[color:var(--border-subtle)] pt-5">
                        <div className="flex items-center gap-4 text-xs text-[var(--text-tertiary)]">
                            <div className="flex items-center gap-1.5">
                                <Calendar className="h-3.5 w-3.5" />
                                <span>Created {formatDate(record.created_at)}</span>
                            </div>
                            <div className="flex items-center gap-1.5">
                                <Clock className="h-3.5 w-3.5" />
                                <span>Updated {formatDate(record.updated_at)}</span>
                            </div>
                        </div>
                    </div>
                </div>

                <div className="flex items-center justify-between border-t border-[color:var(--border-subtle)] bg-[var(--bg-subtle)] px-5 py-4">
                    {canWrite ? <Button
                        variant="ghost"
                        size="sm"
                        className="hover-state-error gap-1.5 text-[var(--state-error)] hover:text-[var(--state-error)]"
                        onClick={() => setShowDeleteDialog(true)}
                    >
                        <Trash2 className="h-3.5 w-3.5" />
                        Delete
                    </Button> : <span />}

                    <div className="flex items-center gap-2">
                        <Button variant="ghost" onClick={onClose}>
                            Cancel
                        </Button>
                        {canWrite ? <Button onClick={handleSave} disabled={!hasChanges}>
                            {hasChanges ? 'Save Changes' : 'Saved'}
                        </Button> : null}
                    </div>
                </div>
                {canWrite ? <DestructiveConfirmationDialog
                    open={showDeleteDialog}
                    onOpenChange={setShowDeleteDialog}
                    title="Delete record"
                    description={`Delete this record from "${table.name}"?`}
                    resourceName="record"
                    confirmationText=""
                    consequences={[
                        'This record will be removed from the table.',
                        'This action cannot be undone.',
                    ]}
                    confirmLabel="Delete record"
                    onConfirm={onDelete}
                /> : null}
            </SheetContent>
        </Sheet>
    );
}
