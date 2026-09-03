import type { SchemaValues } from '@/components/connectors/connector-utils';

/**
 * The trigger parameters worth sending, and where they go.
 *
 * A trigger's own parameters belong at the **top level** of a schedule's
 * config. The backend matches a delivery against that object directly, by JSONB
 * containment, so anything nested under a `trigger_config` key is invisible to
 * matching — which is why the modal used to send `trigger_config: {}` and
 * nothing was ever narrowed by anything.
 *
 * Empty is dropped rather than sent, and the distinction matters in both
 * directions. An empty `actions` list would read as "these actions and no
 * others" against nothing; a `repository_id` of `""` is compared against a
 * numeric repository id and matches no repository at all. Absent means "no
 * opinion", which is what an untouched optional field means.
 */
export function cleanTriggerConfig(values: SchemaValues): Record<string, unknown> {
    const cleaned: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(values)) {
        if (value === undefined || value === null) continue;
        if (typeof value === 'string' && !value.trim()) continue;
        if (Array.isArray(value) && value.length === 0) continue;
        cleaned[key] = value;
    }
    return cleaned;
}
