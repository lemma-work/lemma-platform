import type { EditorInputBinding, InputFieldRequirement } from './flow-editor-types';

export function getSchemaProperties(schema: unknown): InputFieldRequirement[] {
    if (!schema || typeof schema !== 'object') return [];

    const schemaRecord = schema as {
        properties?: Record<string, { type?: string }>;
        required?: string[];
    };
    const properties = schemaRecord.properties || {};
    const required = new Set(Array.isArray(schemaRecord.required) ? schemaRecord.required : []);

    return Object.keys(properties).map((key) => ({
        key,
        required: required.has(key),
        type: properties[key]?.type,
    }));
}

export function normalizeEditorInputBinding(binding: unknown): EditorInputBinding | undefined {
    if (typeof binding === 'string') {
        return { type: 'expression', value: binding };
    }

    if (!binding || typeof binding !== 'object') {
        return undefined;
    }

    const record = binding as Record<string, unknown>;

    if ('$value' in record) {
        return {
            type: 'literal',
            value: record.$value,
        };
    }

    if ('$path' in record && typeof record.$path === 'string') {
        return {
            type: 'expression',
            value: record.$path,
        };
    }

    if (record.type === 'literal') {
        return {
            type: 'literal',
            value: record.value,
        };
    }

    if ((record.type === 'expression' || typeof record.type === 'undefined') && typeof record.value === 'string') {
        return {
            type: 'expression',
            value: record.value,
        };
    }

    return undefined;
}

export function normalizeEditorInputBindings(inputMapping: Record<string, unknown>): Record<string, EditorInputBinding> {
    return Object.fromEntries(
        Object.entries(inputMapping)
            .map(([key, value]) => {
                const normalized = normalizeEditorInputBinding(value);
                return normalized ? [key, normalized] : null;
            })
            .filter((entry): entry is [string, EditorInputBinding] => entry !== null)
    );
}

export function serializeInputBindingsForContract(inputs: Record<string, unknown>): Record<string, EditorInputBinding> {
    return Object.fromEntries(
        Object.entries(inputs)
            .map(([key, value]) => {
                const normalized = normalizeEditorInputBinding(value);
                return normalized ? [key, normalized] : null;
            })
            .filter((entry): entry is [string, EditorInputBinding] => entry !== null)
    );
}

export function getExpressionBindingValue(binding: unknown): string {
    const normalized = normalizeEditorInputBinding(binding);
    return normalized?.type === 'expression' ? normalized.value : '';
}

export function getLiteralBindingValue(binding: unknown): string {
    const normalized = normalizeEditorInputBinding(binding);
    if (normalized?.type !== 'literal') return '';

    if (typeof normalized.value === 'string') {
        return normalized.value;
    }

    if (
        typeof normalized.value === 'number'
        || typeof normalized.value === 'boolean'
        || normalized.value === null
    ) {
        return String(normalized.value);
    }

    try {
        return JSON.stringify(normalized.value);
    } catch {
        return String(normalized.value);
    }
}

export function collectPayloadPathsFromSchema(schema: unknown, prefix = 'payload'): string[] {
    if (!schema || typeof schema !== 'object') {
        return [];
    }

    const paths = new Set<string>([prefix]);
    const schemaRecord = schema as {
        properties?: Record<string, unknown>;
        items?: unknown;
    };
    const properties = schemaRecord.properties;

    if (properties && typeof properties === 'object') {
        Object.entries(properties).forEach(([key, value]) => {
            const childPath = `${prefix}.${key}`;
            paths.add(childPath);

            if (!value || typeof value !== 'object') return;

            const childSchema = value as { properties?: Record<string, unknown>; items?: unknown };
            if (childSchema.properties) {
                collectPayloadPathsFromSchema(childSchema, childPath).forEach((path) => paths.add(path));
                return;
            }

            if (childSchema.items && typeof childSchema.items === 'object') {
                collectPayloadPathsFromSchema(childSchema.items, childPath).forEach((path) => paths.add(path));
            }
        });
    }

    return Array.from(paths);
}
