'use client';

import { useEffect, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Plus, X } from '@/components/ui/icons';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { buildSchemaFormFields, type JsonSchemaLike, type SchemaFormField } from 'lemma-sdk';
import type { SchemaValues } from './connector-utils';

/**
 * A free-form map of string headers — `extra_headers` on an MCP install,
 * `default_headers` on an OpenAPI one.
 *
 * The schema builder folds every object into a `json` field, which renders as a
 * textarea asking a person to hand-write `{"Authorization": "Bearer …"}` with
 * the braces in the right places. These are the only object-shaped fields the
 * connector forms have, and they are always this shape, so they get rows.
 */
const isStringMapSchema = (schema: JsonSchemaLike | undefined): boolean => {
    if (!schema || schema.type !== 'object') return false;
    const properties = schema.properties;
    if (properties && Object.keys(properties).length > 0) return false;
    const additional = schema.additionalProperties;
    return Boolean(
        additional && typeof additional === 'object' && (additional as JsonSchemaLike).type === 'string',
    );
};

/** Reads a header map back from whatever the form is holding — object or JSON text. */
const toEntries = (value: unknown): Array<[string, string]> => {
    let parsed: unknown = value;
    if (typeof value === 'string') {
        if (!value.trim()) return [];
        try {
            parsed = JSON.parse(value);
        } catch {
            return [];
        }
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return [];
    return Object.entries(parsed as Record<string, unknown>).map(([key, item]) => [
        key,
        typeof item === 'string' ? item : String(item ?? ''),
    ]);
};

export function SchemaFields({
    schema,
    values,
    onChange,
    emptyMessage = 'No configurable fields are required for this provider.',
    autoFocusFirst = false,
    followSchemaOrder = false,
}: {
    schema: JsonSchemaLike | null;
    values: SchemaValues;
    onChange: (values: SchemaValues) => void;
    emptyMessage?: string;
    autoFocusFirst?: boolean;
    /**
     * Render fields in the order the schema declares them.
     *
     * `buildSchemaFormFields` otherwise sorts by label, which for a connection
     * form puts Database above Host — alphabetical, and backwards from how
     * anyone reads a connection string. Opt-in so the other connector forms keep
     * the ordering they already ship.
     */
    followSchemaOrder?: boolean;
}) {
    const declaredOrder =
        followSchemaOrder && schema?.properties ? Object.keys(schema.properties) : null;
    const fields = buildSchemaFormFields(
        schema,
        declaredOrder ? { 'ui:order': declaredOrder } : undefined,
    );

    if (fields.length === 0) {
        return (
            <div className="surface-panel-muted p-3 text-sm text-[var(--text-secondary)]">
                {emptyMessage}
            </div>
        );
    }

    const updateField = (name: string, value: unknown) => {
        onChange({ ...values, [name]: value });
    };

    return (
        <div className="space-y-3">
            {fields.map((field, index) => (
                <SchemaField
                    key={field.name}
                    field={field}
                    value={values[field.name]}
                    onChange={(value) => updateField(field.name, value)}
                    autoFocus={autoFocusFirst && index === 0}
                />
            ))}
        </div>
    );
}

function SchemaField({
    field,
    value,
    onChange,
    autoFocus = false,
}: {
    field: SchemaFormField;
    value: unknown;
    onChange: (value: unknown) => void;
    autoFocus?: boolean;
}) {
    const fieldId = `connector-schema-${field.name}`;
    const label = `${field.label}${field.required ? ' *' : ''}`;
    const stringValue = typeof value === 'string' ? value : value == null ? '' : String(value);

    if (field.kind === 'boolean') {
        return (
            <Label htmlFor={fieldId} className="flex cursor-pointer items-start gap-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-1)] p-3">
                <Checkbox
                    id={fieldId}
                    autoFocus={autoFocus}
                    checked={Boolean(value)}
                    onCheckedChange={(checked) => onChange(Boolean(checked))}
                    className="mt-0.5"
                />
                <span className="grid gap-1">
                    <span className="text-sm font-medium text-[var(--text-primary)]">{label}</span>
                    {field.description ? (
                        <span className="text-xs leading-5 text-[var(--text-secondary)]">{field.description}</span>
                    ) : null}
                </span>
            </Label>
        );
    }

    if (field.kind === 'json' && isStringMapSchema(field.schema)) {
        return <StringMapField field={field} label={label} value={value} onChange={onChange} />;
    }

    return (
        <div className="space-y-1.5">
            <Label htmlFor={fieldId}>{label}</Label>
            {field.kind === 'select' ? (
                <Select value={stringValue} onValueChange={onChange}>
                    <SelectTrigger id={fieldId} autoFocus={autoFocus}>
                        <SelectValue placeholder={`Select ${field.label}`} />
                    </SelectTrigger>
                    <SelectContent>
                        {field.options.map((option) => (
                            <SelectItem key={option.value} value={option.value}>
                                {option.label}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            ) : field.kind === 'textarea' || field.kind === 'json' ? (
                <Textarea
                    id={fieldId}
                    name={fieldId}
                    autoFocus={autoFocus}
                    autoComplete="off"
                    data-1p-ignore
                    data-lpignore="true"
                    className="form-field-control-flat min-h-28 p-3"
                    value={stringValue}
                    onChange={(event) => onChange(event.target.value)}
                    spellCheck={field.kind !== 'json'}
                />
            ) : (
                <Input
                    id={fieldId}
                    name={fieldId}
                    autoFocus={autoFocus}
                    // API keys / tokens are not login credentials — "new-password" stops
                    // Chrome from treating this as a login and autofilling the saved
                    // username into another text field on the page (e.g. the search box).
                    autoComplete={field.format === 'password' ? 'new-password' : 'off'}
                    data-1p-ignore
                    data-lpignore="true"
                    type={field.kind === 'number' ? 'number' : field.kind === 'email' ? 'email' : field.format === 'password' ? 'password' : 'text'}
                    value={stringValue}
                    onChange={(event) => onChange(event.target.value)}
                />
            )}
            {field.description ? (
                <p className="text-xs leading-5 text-[var(--text-tertiary)]">{field.description}</p>
            ) : null}
        </div>
    );
}

function StringMapField({
    field,
    label,
    value,
    onChange,
}: {
    field: SchemaFormField;
    label: string;
    value: unknown;
    onChange: (value: unknown) => void;
}) {
    // Rows are local so a half-typed header — a key with no value yet, or two
    // rows briefly sharing a name — survives the keystroke instead of being
    // collapsed away by the object round-trip.
    const [rows, setRows] = useState<Array<{ key: string; value: string }>>(() =>
        toEntries(value).map(([key, item]) => ({ key, value: item })),
    );

    const asObject = (entries: Array<{ key: string; value: string }>) =>
        Object.fromEntries(
            entries
                .map((row) => [row.key.trim(), row.value] as const)
                .filter(([key]) => key.length > 0),
        );

    // Re-sync when the value arrives from outside, which on the edit path it
    // does: the dialog hydrates its config in an effect that runs after this
    // subtree has mounted, so initialising once showed NO headers for an
    // install that has them. Untouched they survived, because the parent still
    // held them — but adding a single header committed only the visible rows
    // and silently dropped every existing one.
    //
    // Compared by content, not by reference, so our own echo does not come
    // back as an outside change and collapse a row the user is mid-way through
    // typing: a row with a blank key contributes nothing to the object, so
    // what we hold and what we last emitted still agree.
    const incoming = JSON.stringify(toEntries(value));
    const settled = JSON.stringify(Object.entries(asObject(rows)));
    useEffect(() => {
        if (incoming === settled) return;
        setRows(toEntries(value).map(([key, item]) => ({ key, value: item })));
        // `incoming` stands in for `value`: it is its content, which is what
        // decides whether this is genuinely new.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [incoming]);

    const commit = (next: Array<{ key: string; value: string }>) => {
        setRows(next);
        onChange(asObject(next));
    };

    const updateRow = (index: number, patch: Partial<{ key: string; value: string }>) =>
        commit(rows.map((row, position) => (position === index ? { ...row, ...patch } : row)));

    return (
        <div className="space-y-1.5">
            <Label>{label}</Label>
            <div className="space-y-2">
                {rows.map((row, index) => (
                    <div key={index} className="flex items-center gap-2">
                        <Input
                            aria-label={`${field.label} name`}
                            autoComplete="off"
                            data-1p-ignore
                            data-lpignore="true"
                            placeholder="Header"
                            className="flex-1"
                            value={row.key}
                            onChange={(event) => updateRow(index, { key: event.target.value })}
                        />
                        <Input
                            aria-label={`${field.label} value`}
                            autoComplete="off"
                            data-1p-ignore
                            data-lpignore="true"
                            placeholder="Value"
                            className="flex-1"
                            value={row.value}
                            onChange={(event) => updateRow(index, { value: event.target.value })}
                        />
                        <Button
                            type="button"
                            variant="quiet"
                            size="icon"
                            className="h-8 w-8 shrink-0"
                            aria-label={`Remove ${row.key || 'header'}`}
                            onClick={() => commit(rows.filter((_, position) => position !== index))}
                        >
                            <X className="h-3.5 w-3.5" />
                        </Button>
                    </div>
                ))}
                <Button
                    type="button"
                    variant="quiet"
                    size="sm"
                    className="h-8 px-2 text-xs text-[var(--text-tertiary)]"
                    onClick={() => setRows([...rows, { key: '', value: '' }])}
                >
                    <Plus className="mr-1.5 h-3.5 w-3.5" />
                    Add header
                </Button>
            </div>
            {field.description ? (
                <p className="text-xs leading-5 text-[var(--text-tertiary)]">{field.description}</p>
            ) : null}
        </div>
    );
}
