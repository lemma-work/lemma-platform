'use client';

import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { buildSchemaFormFields, type JsonSchemaLike, type SchemaFormField } from 'lemma-sdk';
import type { SchemaValues } from './connector-utils';

export function SchemaFields({
    schema,
    values,
    onChange,
    emptyMessage = 'No configurable fields are required for this provider.',
    autoFocusFirst = false,
}: {
    schema: JsonSchemaLike | null;
    values: SchemaValues;
    onChange: (values: SchemaValues) => void;
    emptyMessage?: string;
    autoFocusFirst?: boolean;
}) {
    const fields = buildSchemaFormFields(schema);

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
