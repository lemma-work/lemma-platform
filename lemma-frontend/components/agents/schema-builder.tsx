'use client';

import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';
import { Trash2, Plus } from '@/components/ui/icons';
import { QuietEmptyState } from '@/components/shared/empty-state';
import { cn } from '@/lib/utils';

interface SchemaBuilderProps {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    value: Record<string, any>;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    onChange: (schema: Record<string, any>) => void;
    readOnly?: boolean;
    variant?: 'basic' | 'config';
    onIncompleteChange?: (hasIncomplete: boolean) => void;
}

type SchemaType = 'string' | 'number' | 'boolean' | 'array' | 'object';

interface SchemaField {
    id: string;
    name: string;
    type: SchemaType;
    title: string;
    description: string;
    required: boolean;
    nullable: boolean;
    hasDefault: boolean;
    defaultMode: 'value' | 'null';
    defaultText: string;
    defaultBoolean: boolean;
    itemType?: SchemaType;
}

const createFieldId = () => Math.random().toString(36).slice(2, 11);

function isSchemaType(value: unknown): value is SchemaType {
    return value === 'string' || value === 'number' || value === 'boolean' || value === 'array' || value === 'object';
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function resolveSchemaType(schema: Record<string, any> | undefined): SchemaType {
    if (schema && isSchemaType(schema.type)) {
        return schema.type;
    }

    if (Array.isArray(schema?.anyOf)) {
        const candidate = schema.anyOf.find(
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            (option: any) => option && option.type && option.type !== 'null'
        )?.type;
        if (isSchemaType(candidate)) {
            return candidate;
        }
    }

    return 'string';
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function isNullableSchema(schema: Record<string, any> | undefined): boolean {
    return Array.isArray(schema?.anyOf) && schema.anyOf.some(
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (option: any) => option?.type === 'null'
    );
}

function serializeDefaultValue(type: SchemaType, value: unknown): string {
    if (value === undefined || value === null) return '';
    if (type === 'number') return typeof value === 'number' ? String(value) : '';
    if (type === 'string') return typeof value === 'string' ? value : String(value);
    return '';
}

function parseDefaultValue(field: SchemaField): string | number | boolean | null | undefined {
    if (!field.hasDefault) return undefined;
    if (field.defaultMode === 'null') return null;

    if (field.type === 'boolean') {
        return field.defaultBoolean;
    }

    if (field.type === 'number') {
        const trimmed = field.defaultText.trim();
        if (!trimmed) return undefined;
        const parsed = Number(trimmed);
        return Number.isNaN(parsed) ? undefined : parsed;
    }

    if (field.type === 'string') {
        return field.defaultText;
    }

    return undefined;
}

export function SchemaBuilder({ value, onChange, readOnly, variant = 'basic', onIncompleteChange }: SchemaBuilderProps) {
    const [fields, setFields] = useState<SchemaField[]>([]);
    const isInternalUpdate = useRef(false);

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const parseSchemaToFields = (schema: Record<string, any>, existingFields: SchemaField[] = []): SchemaField[] => {
        const parsedFields: SchemaField[] = [];
        const properties = schema?.properties || {};
        const required = schema?.required || [];

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        Object.entries(properties).forEach(([key, val]: [string, any]) => {
            const existingField = existingFields.find((f) => f.name === key);
            const fieldType = resolveSchemaType(val);
            const hasDefault = Object.prototype.hasOwnProperty.call(val, 'default');
            const defaultMode = hasDefault && val.default === null ? 'null' : 'value';

            parsedFields.push({
                id: existingField ? existingField.id : createFieldId(),
                name: key,
                type: fieldType,
                title: val.title || '',
                description: val.description || '',
                required: required.includes(key),
                nullable: isNullableSchema(val),
                hasDefault,
                defaultMode,
                defaultText: defaultMode === 'value' ? serializeDefaultValue(fieldType, val.default) : '',
                defaultBoolean: typeof val.default === 'boolean' ? val.default : false,
                itemType: fieldType === 'array' && isSchemaType(val.items?.type) ? val.items.type : undefined,
            });
        });

        return parsedFields;
    };

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const convertFieldsToSchema = (currentFields: SchemaField[]): Record<string, any> => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const schema: Record<string, any> = {
            type: 'object',
            properties: {},
            required: [] as string[],
        };

        currentFields.forEach((field) => {
            if (!field.name) return;

            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const fieldDef: Record<string, any> = {};

            if (variant === 'config' && field.nullable) {
                fieldDef.anyOf = [{ type: field.type }, { type: 'null' }];
            } else {
                fieldDef.type = field.type;
            }

            if (variant === 'config' && field.title?.trim()) {
                fieldDef.title = field.title.trim();
            }

            if (field.description?.trim()) {
                fieldDef.description = field.description;
            }

            if (field.type === 'array' && field.itemType) {
                fieldDef.items = { type: field.itemType };
            }

            if (variant === 'config') {
                const parsedDefault = parseDefaultValue(field);
                if (parsedDefault !== undefined) {
                    fieldDef.default = parsedDefault;
                }
            }

            schema.properties[field.name] = fieldDef;

            if (field.required) {
                schema.required.push(field.name);
            }
        });

        if (schema.required.length === 0) delete schema.required;
        return schema;
    };

    useEffect(() => {
        if (isInternalUpdate.current) {
            isInternalUpdate.current = false;
            return;
        }

        const currentSchemaFromFields = convertFieldsToSchema(fields);
        if (JSON.stringify(value) !== JSON.stringify(currentSchemaFromFields)) {
            setFields(parseSchemaToFields(value || {}, fields));
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [value]);

    useEffect(() => {
        onIncompleteChange?.(fields.some((field) => !field.name.trim()));
    }, [fields, onIncompleteChange]);

    useEffect(() => {
        return () => {
            onIncompleteChange?.(false);
        };
    }, [onIncompleteChange]);

    const updateSchema = (newFields: SchemaField[]) => {
        setFields(newFields);
        isInternalUpdate.current = true;
        onChange(convertFieldsToSchema(newFields));
    };

    const addField = () => {
        if (readOnly) return;
        const newField: SchemaField = {
            id: createFieldId(),
            name: '',
            type: 'string',
            title: '',
            description: '',
            required: false,
            nullable: false,
            hasDefault: false,
            defaultMode: 'value',
            defaultText: '',
            defaultBoolean: false,
        };
        updateSchema([...fields, newField]);
    };

    const removeField = (id: string) => {
        if (readOnly) return;
        updateSchema(fields.filter((f) => f.id !== id));
    };

    const updateField = (id: string, updates: Partial<SchemaField>) => {
        if (readOnly) return;
        updateSchema(fields.map((f) => (f.id === id ? { ...f, ...updates } : f)));
    };

    return (
        <div className="w-full space-y-2">
            {fields.length === 0 ? (
                <QuietEmptyState className="justify-center rounded-md bg-[color:color-mix(in_srgb,var(--surface-2)_32%,transparent)] px-3 py-4 text-center text-xs">
                    No fields yet
                </QuietEmptyState>
            ) : (
                <div className="space-y-2">
                    {fields.map((field) => (
                        <SchemaFieldRow
                            key={field.id}
                            field={field}
                            readOnly={readOnly}
                            variant={variant}
                            onUpdate={(updates) => updateField(field.id, updates)}
                            onRemove={() => removeField(field.id)}
                        />
                    ))}
                </div>
            )}

            {!readOnly && (
                <Button
                    onClick={addField}
                    variant="ghost"
                    size="sm"
                    className="surface-panel-dashed h-11 w-full text-sm font-medium text-[var(--text-secondary)] transition-gentle hover:scale-[1.002] hover:bg-[color:color-mix(in_srgb,var(--surface-2)_42%,transparent)] hover:text-[var(--text-primary)]"
                >
                    <Plus className="mr-1.5 h-3.5 w-3.5" />
                    Add field
                </Button>
            )}
        </div>
    );
}

function SchemaFieldRow({
    field,
    readOnly,
    variant,
    onUpdate,
    onRemove,
}: {
    field: SchemaField;
    readOnly?: boolean;
    variant: 'basic' | 'config';
    onUpdate: (updates: Partial<SchemaField>) => void;
    onRemove: () => void;
}) {
    return (
        <div className="schema-contract-row surface-panel group p-3">
            <div className="flex flex-wrap items-center gap-2">
                <input
                    placeholder="field_name"
                    value={field.name}
                    onChange={(e) => onUpdate({ name: e.target.value })}
                    className={cn(
                        'inline-edit-field min-w-[12rem] flex-1 rounded-lg px-2 py-1.5 font-mono text-base text-[var(--text-primary)] transition-gentle',
                        'focus-visible:bg-[var(--delight-soft)]',
                        !field.name && 'focus-visible:ring-2 focus-visible:ring-[var(--state-error)]'
                    )}
                    disabled={readOnly}
                />

                <Select
                    value={field.type}
                    onValueChange={(value) => onUpdate({
                        type: value as SchemaType,
                        itemType: value === 'array' ? field.itemType || 'string' : undefined,
                    })}
                    disabled={readOnly}
                >
                    <SelectTrigger className="h-9 w-[122px] rounded-full border-[color:color-mix(in_srgb,var(--border-subtle)_70%,transparent)] bg-[color:color-mix(in_srgb,var(--surface-2)_46%,transparent)] px-3 text-sm shadow-none">
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="string">String</SelectItem>
                        <SelectItem value="number">Number</SelectItem>
                        <SelectItem value="boolean">Boolean</SelectItem>
                        <SelectItem value="array">Array</SelectItem>
                        <SelectItem value="object">Object</SelectItem>
                    </SelectContent>
                </Select>

                <Button
                    variant="ghost"
                    size="sm"
                    className={cn(
                        'h-9 rounded-full px-3 text-xs font-medium transition-gentle hover:scale-[1.02]',
                        field.required
                            ? 'tone-action-chip'
                            : 'bg-[color:color-mix(in_srgb,var(--surface-2)_42%,transparent)] text-[var(--text-secondary)] hover:bg-[color:color-mix(in_srgb,var(--surface-2)_64%,transparent)]'
                    )}
                    onClick={() => !readOnly && onUpdate({ required: !field.required })}
                    disabled={readOnly}
                >
                    {field.required ? 'Required' : 'Optional'}
                </Button>

                <Button
                    variant="ghost"
                    size="icon"
                    className="hover-state-error h-8 w-8 rounded-full text-[var(--text-tertiary)] opacity-0 transition-gentle group-hover:opacity-100 focus-visible:opacity-100"
                    onClick={onRemove}
                    disabled={readOnly}
                >
                    <Trash2 className="h-4 w-4" />
                </Button>
            </div>

            {variant === 'config' && (
                <input
                    placeholder="Label (optional)"
                    value={field.title}
                    onChange={(e) => onUpdate({ title: e.target.value })}
                    className="inline-edit-field mt-2 h-7 w-full rounded-md px-2 text-xs transition-gentle focus-visible:bg-[color:color-mix(in_srgb,var(--surface-2)_42%,transparent)]"
                    disabled={readOnly}
                />
            )}

            {variant === 'config' && (
                <div className="mt-2 flex items-center gap-3 px-2 py-1">
                    <label className="flex items-center gap-2 text-xs text-[var(--text-secondary)]">
                        <Checkbox
                            checked={field.nullable}
                            onCheckedChange={(checked) => onUpdate({
                                nullable: Boolean(checked),
                                defaultMode: !checked && field.defaultMode === 'null' ? 'value' : field.defaultMode,
                            })}
                            disabled={readOnly}
                        />
                        Nullable
                    </label>

                    <label className="flex items-center gap-2 text-xs text-[var(--text-secondary)]">
                        <Checkbox
                            checked={field.hasDefault}
                            onCheckedChange={(checked) => onUpdate({ hasDefault: Boolean(checked) })}
                            disabled={readOnly}
                        />
                        Has default
                    </label>
                </div>
            )}

            <input
                placeholder="Add a short description..."
                value={field.description}
                onChange={(e) => onUpdate({ description: e.target.value })}
                className="inline-edit-field mt-2 h-7 w-full rounded-md px-2 text-sm transition-gentle focus-visible:bg-[color:color-mix(in_srgb,var(--surface-2)_42%,transparent)]"
                disabled={readOnly}
            />

            {variant === 'config' && field.hasDefault && (
                <div className="space-y-2 px-2">
                    {field.nullable && (
                        <div className="w-[160px]">
                            <Select
                                value={field.defaultMode}
                                onValueChange={(value) => onUpdate({ defaultMode: value as 'value' | 'null' })}
                                disabled={readOnly}
                            >
                                <SelectTrigger className="h-8 text-xs">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="value">Default value</SelectItem>
                                    <SelectItem value="null">Default null</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                    )}

                    {field.defaultMode === 'value' && field.type === 'boolean' && (
                        <label className="flex items-center gap-2 text-xs text-[var(--text-secondary)]">
                            <Checkbox
                                checked={field.defaultBoolean}
                                onCheckedChange={(checked) => onUpdate({ defaultBoolean: Boolean(checked) })}
                                disabled={readOnly}
                            />
                            Default checked
                        </label>
                    )}

                    {field.defaultMode === 'value' && (field.type === 'string' || field.type === 'number') && (
                        <Input
                            type={field.type === 'number' ? 'number' : 'text'}
                            placeholder="Default value"
                            value={field.defaultText}
                            onChange={(e) => onUpdate({ defaultText: e.target.value })}
                            className="h-8 text-xs"
                            disabled={readOnly}
                        />
                    )}

                    {field.defaultMode === 'value' && (field.type === 'array' || field.type === 'object') && (
                        <p className="text-xs text-[var(--text-tertiary)]">
                            Use JSON mode to set defaults for {field.type} fields.
                        </p>
                    )}
                </div>
            )}

            {field.type === 'array' && (
                <div className="flex items-center gap-2">
                    <span className="text-xs text-[var(--text-tertiary)]">Array item type</span>
                    <div className="w-[120px]">
                        <Select
                            value={field.itemType || 'string'}
                            onValueChange={(value) => onUpdate({ itemType: value as SchemaType })}
                            disabled={readOnly}
                        >
                            <SelectTrigger className="h-8 text-xs">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="string">String</SelectItem>
                                <SelectItem value="number">Number</SelectItem>
                                <SelectItem value="boolean">Boolean</SelectItem>
                                <SelectItem value="object">Object</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>
                </div>
            )}
        </div>
    );
}
