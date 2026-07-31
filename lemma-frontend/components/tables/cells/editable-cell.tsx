'use client';

import { useEffect, useRef, useState } from 'react';
import { Check, Loader2 } from '@/components/ui/icons';
import { getEnumColor } from '@/lib/utils/enum-color-utils';

interface EditableCellProps {
    value: unknown;
    fieldType: string;
    onSave: (value: unknown) => Promise<void>;
    onCancel?: () => void;
    enumOptions?: string[];
}

const inputBaseClass =
    'min-h-7 w-full rounded-md border border-[color:var(--row-border)] bg-[var(--surface-1)] px-1.5 py-1 text-sm text-[var(--text-primary)] transition-gentle placeholder:text-[var(--text-tertiary)] hover:bg-[var(--bg-subtle)] focus-ring';

function stringifyEditableValue(value: unknown): string {
    if (value === null || value === undefined) return '';
    if (typeof value === 'object') {
        return JSON.stringify(value);
    }
    return String(value);
}

function toNumberInput(value: unknown): string {
    if (value === null || value === undefined || value === '') return '';
    if (typeof value === 'number') return String(value);
    if (typeof value === 'string') return value;
    return '';
}

function toDateInput(value: unknown): string {
    if (typeof value !== 'string') return '';
    return value;
}

function SavingIndicator() {
    return (
        <div className="absolute right-2 top-1/2 -translate-y-1/2">
            <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--action-primary)]" />
        </div>
    );
}

export function EditableTextCell({ value, onSave, onCancel }: Omit<EditableCellProps, 'fieldType' | 'enumOptions'>) {
    const [isEditing, setIsEditing] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [editValue, setEditValue] = useState(() => stringifyEditableValue(value));
    const inputRef = useRef<HTMLInputElement>(null);

    const originalValue = stringifyEditableValue(value);

    useEffect(() => {
        setEditValue(originalValue);
    }, [originalValue]);

    useEffect(() => {
        if (!isEditing || !inputRef.current) return;
        inputRef.current.focus();
        inputRef.current.select();
    }, [isEditing]);

    const handleSave = async () => {
        if (editValue === originalValue) {
            setIsEditing(false);
            return;
        }

        setIsSaving(true);
        try {
            await onSave(editValue);
            setIsEditing(false);
        } catch (error) {
            console.error('Failed to save:', error);
            setEditValue(originalValue);
        } finally {
            setIsSaving(false);
        }
    };

    if (!isEditing) {
        return (
            <div
                className="flex min-h-7 cursor-pointer items-center rounded-md px-1.5 py-1 transition-gentle hover:bg-[var(--bg-subtle)]"
                onClick={() => setIsEditing(true)}
            >
                <span className="w-full truncate text-sm text-[var(--text-secondary)]">
                    {originalValue || <span className="text-[var(--text-tertiary)]">—</span>}
                </span>
            </div>
        );
    }

    return (
        <div className="relative px-1 py-1">
            <input
                ref={inputRef}
                type="text"
                value={editValue}
                onChange={(event) => setEditValue(event.target.value)}
                onBlur={handleSave}
                onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                        event.preventDefault();
                        handleSave();
                    }
                    if (event.key === 'Escape') {
                        setEditValue(originalValue);
                        setIsEditing(false);
                        onCancel?.();
                    }
                }}
                disabled={isSaving}
                className={`editable-cell-field ${inputBaseClass}`}
            />
            {isSaving ? <SavingIndicator /> : null}
        </div>
    );
}

export function EditableNumberCell({ value, onSave, onCancel }: Omit<EditableCellProps, 'fieldType' | 'enumOptions'>) {
    const [isEditing, setIsEditing] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [editValue, setEditValue] = useState(() => toNumberInput(value));
    const inputRef = useRef<HTMLInputElement>(null);

    const originalValue = toNumberInput(value);

    useEffect(() => {
        setEditValue(originalValue);
    }, [originalValue]);

    useEffect(() => {
        if (!isEditing || !inputRef.current) return;
        inputRef.current.focus();
        inputRef.current.select();
    }, [isEditing]);

    const handleSave = async () => {
        const normalizedInput = editValue.trim();
        const nextValue = normalizedInput === '' ? null : Number(normalizedInput);
        const currentValue = originalValue.trim() === '' ? null : Number(originalValue);

        if (
            (nextValue === null && currentValue === null) ||
            (typeof nextValue === 'number' && typeof currentValue === 'number' && nextValue === currentValue)
        ) {
            setIsEditing(false);
            return;
        }

        setIsSaving(true);
        try {
            await onSave(nextValue);
            setIsEditing(false);
        } catch (error) {
            console.error('Failed to save:', error);
            setEditValue(originalValue);
        } finally {
            setIsSaving(false);
        }
    };

    if (!isEditing) {
        return (
            <div
                className="flex min-h-7 cursor-pointer items-center rounded-md px-1.5 py-1 transition-gentle hover:bg-[var(--bg-subtle)]"
                onClick={() => setIsEditing(true)}
            >
                <span className="w-full tabular-nums text-sm text-[var(--text-primary)]">
                    {originalValue || <span className="text-[var(--text-tertiary)]">—</span>}
                </span>
            </div>
        );
    }

    return (
        <div className="relative px-1 py-1">
            <input
                ref={inputRef}
                type="number"
                value={editValue}
                onChange={(event) => setEditValue(event.target.value)}
                onBlur={handleSave}
                onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                        event.preventDefault();
                        handleSave();
                    }
                    if (event.key === 'Escape') {
                        setEditValue(originalValue);
                        setIsEditing(false);
                        onCancel?.();
                    }
                }}
                disabled={isSaving}
                className={`editable-cell-field ${inputBaseClass} tabular-nums`}
            />
            {isSaving ? <SavingIndicator /> : null}
        </div>
    );
}

export function EditableDateCell({ value, onSave, onCancel }: Omit<EditableCellProps, 'fieldType' | 'enumOptions'>) {
    const [isEditing, setIsEditing] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [editValue, setEditValue] = useState(() => toDateInput(value));
    const inputRef = useRef<HTMLInputElement>(null);

    const originalValue = toDateInput(value);

    useEffect(() => {
        setEditValue(originalValue);
    }, [originalValue]);

    useEffect(() => {
        if (!isEditing || !inputRef.current) return;
        inputRef.current.focus();
    }, [isEditing]);

    const handleSave = async () => {
        if (editValue === originalValue) {
            setIsEditing(false);
            return;
        }

        setIsSaving(true);
        try {
            await onSave(editValue);
            setIsEditing(false);
        } catch (error) {
            console.error('Failed to save:', error);
            setEditValue(originalValue);
        } finally {
            setIsSaving(false);
        }
    };

    const displayValue = (() => {
        if (!originalValue) return '';
        const parsed = new Date(originalValue);
        if (Number.isNaN(parsed.getTime())) return originalValue;
        return parsed.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    })();

    if (!isEditing) {
        return (
            <div
                className="flex min-h-7 cursor-pointer items-center rounded-md px-1.5 py-1 transition-gentle hover:bg-[var(--bg-subtle)]"
                onClick={() => setIsEditing(true)}
            >
                <span className="w-full text-sm text-[var(--text-primary)]">
                    {displayValue || <span className="text-[var(--text-tertiary)]">—</span>}
                </span>
            </div>
        );
    }

    return (
        <div className="relative px-1 py-1">
            <input
                ref={inputRef}
                type="date"
                value={editValue}
                onChange={(event) => setEditValue(event.target.value)}
                onBlur={handleSave}
                onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                        event.preventDefault();
                        handleSave();
                    }
                    if (event.key === 'Escape') {
                        setEditValue(originalValue);
                        setIsEditing(false);
                        onCancel?.();
                    }
                }}
                disabled={isSaving}
                className={`editable-cell-field ${inputBaseClass}`}
            />
            {isSaving ? <SavingIndicator /> : null}
        </div>
    );
}

export function EditableBooleanCell({ value, onSave }: Omit<EditableCellProps, 'fieldType' | 'onCancel' | 'enumOptions'>) {
    const [isSaving, setIsSaving] = useState(false);
    const normalizedValue = Boolean(value);

    const handleToggle = async () => {
        setIsSaving(true);
        try {
            await onSave(!normalizedValue);
        } catch (error) {
            console.error('Failed to save:', error);
        } finally {
            setIsSaving(false);
        }
    };

    return (
        <div className="flex min-h-7 items-center px-1.5 py-1">
            <button
                type="button"
                onClick={handleToggle}
                disabled={isSaving}
                className={
                    normalizedValue
                        ? 'editable-cell-boolean-button flex h-5 w-5 items-center justify-center rounded-md border-2 border-[var(--action-primary)] bg-[var(--action-primary)] text-[var(--text-on-brand)] shadow-[var(--shadow-xs)] transition-gentle'
                        : 'editable-cell-boolean-button flex h-5 w-5 items-center justify-center rounded-md border-2 border-[color:var(--row-border)] bg-[var(--surface-1)] transition-gentle hover:border-[color:var(--border-strong)]'
                }
            >
                {normalizedValue ? <Check className="h-3.5 w-3.5" strokeWidth={3} /> : null}
            </button>
        </div>
    );
}

export function EditableEnumCell({
    value,
    enumOptions,
    onSave,
    onCancel,
}: Omit<EditableCellProps, 'fieldType'> & { enumOptions: string[] }) {
    const [isEditing, setIsEditing] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [editValue, setEditValue] = useState(() => (value === null || value === undefined ? '' : String(value)));
    const selectRef = useRef<HTMLSelectElement>(null);

    const normalizedValue = value === null || value === undefined ? '' : String(value);

    useEffect(() => {
        setEditValue(normalizedValue);
    }, [normalizedValue]);

    useEffect(() => {
        if (!isEditing || !selectRef.current) return;
        selectRef.current.focus();
    }, [isEditing]);

    if (!isEditing) {
        const color = getEnumColor(normalizedValue, enumOptions);

        return (
            <div
                className="flex min-h-7 cursor-pointer items-center rounded-md px-1.5 py-1 transition-gentle hover:bg-[var(--bg-subtle)]"
                onClick={() => setIsEditing(true)}
            >
                {normalizedValue ? (
                    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium shadow-[var(--shadow-xs)] ${color.bg} ${color.text}`}>
                        {normalizedValue}
                    </span>
                ) : (
                    <span className="text-sm text-[var(--text-tertiary)]">—</span>
                )}
            </div>
        );
    }

    return (
        <div className="relative px-1 py-1">
            <select
                ref={selectRef}
                value={editValue}
                className={`editable-cell-field ${inputBaseClass} cursor-pointer`}
                onChange={(event) => {
                    const nextValue = event.target.value;
                    setEditValue(nextValue);
                    setIsSaving(true);
                    onSave(nextValue)
                        .then(() => {
                            setIsEditing(false);
                            setIsSaving(false);
                        })
                        .catch((error) => {
                            console.error('Failed to save:', error);
                            setEditValue(normalizedValue);
                            setIsSaving(false);
                        });
                }}
                onBlur={() => {
                    if (isSaving) return;
                    setIsEditing(false);
                    setEditValue(normalizedValue);
                }}
                onKeyDown={(event) => {
                    if (event.key !== 'Escape') return;
                    setEditValue(normalizedValue);
                    setIsEditing(false);
                    onCancel?.();
                }}
                disabled={isSaving}
            >
                <option value="">Select...</option>
                {enumOptions.map((option) => (
                    <option key={option} value={option}>
                        {option}
                    </option>
                ))}
            </select>
            {isSaving ? <SavingIndicator /> : null}
        </div>
    );
}

export function EditableCell({ value, fieldType, onSave, onCancel, enumOptions }: EditableCellProps) {
    const type = String(fieldType).toUpperCase();

    if (type === 'ENUM' && enumOptions && Array.isArray(enumOptions) && enumOptions.length > 0) {
        return <EditableEnumCell value={value} enumOptions={enumOptions} onSave={onSave} onCancel={onCancel} />;
    }

    switch (type) {
        case 'INTEGER':
        case 'FLOAT':
        case 'SERIAL':
            return <EditableNumberCell value={value} onSave={onSave} onCancel={onCancel} />;
        case 'BOOLEAN':
            return <EditableBooleanCell value={value} onSave={onSave} />;
        case 'DATE':
        case 'DATETIME':
            return <EditableDateCell value={value} onSave={onSave} onCancel={onCancel} />;
        case 'TEXT':
        case 'JSON':
        default:
            return <EditableTextCell value={value} onSave={onSave} onCancel={onCancel} />;
    }
}
