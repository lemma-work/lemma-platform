import { useEffect, useMemo, useState } from 'react';
import { XCircle } from '@/components/ui/icons';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';
import { WorkflowNode } from '@/lib/types';
import { formDefaults, isRecord } from '../run-format';
import { humanizeKey } from '@/components/lemma/assistant/assistant-format';
import { getResourceErrorMessage } from '@/components/shared/resource-feedback';
import { StepLoader } from '@/components/brand/loader';

/** Array and object properties used to fall through to a text input, so the
 * string it produced failed server-side jsonschema and came back as a raw
 * validator message. They get a JSON editor instead. */
function isStructuredField(property: Record<string, unknown>): boolean {
    if (Array.isArray(property.enum)) return false;
    return property.type === 'object' || property.type === 'array';
}

function StructuredField({
    value,
    required,
    onChange,
}: {
    value: unknown;
    required: boolean;
    onChange: (value: unknown) => void;
}) {
    const [draft, setDraft] = useState(() => (value === undefined ? '' : JSON.stringify(value, null, 2)));
    const [parseError, setParseError] = useState<string | null>(null);

    const handleChange = (next: string) => {
        setDraft(next);
        if (!next.trim()) {
            setParseError(null);
            onChange(undefined);
            return;
        }
        try {
            onChange(JSON.parse(next));
            setParseError(null);
        } catch (error) {
            // Keep the keystrokes; just refuse to submit half-typed JSON.
            setParseError(error instanceof Error ? error.message : 'Invalid JSON');
        }
    };

    return (
        <div className="space-y-1.5">
            <textarea
                required={required}
                value={draft}
                spellCheck={false}
                rows={5}
                onChange={(event) => handleChange(event.target.value)}
                className={cn(
                    'w-full rounded-md border bg-[var(--surface-1)] px-3 py-2 font-mono text-xs leading-5 text-[var(--text-primary)] outline-none',
                    parseError ? 'border-[color:var(--state-error)]' : 'border-[var(--border-subtle)] focus:border-[color:var(--field-border-focus)]'
                )}
            />
            {parseError ? (
                <p className="text-xs text-[var(--state-error)]">{parseError}</p>
            ) : null}
        </div>
    );
}

export function RunInputForm({
    nodeId,
    nodes,
    schema: schemaOverride,
    nextNodeLabel,
    onSubmitInput,
    variant = 'boxed',
    heading = true,
}: {
    nodeId: string;
    nodes: WorkflowNode[];
    // The resolved schema from the run's active wait. Preferred over the node's
    // static config, which is a template that may still hold typed bindings
    // ({"type":"expression","value":...}) resolved server-side at suspend,
    // never by the frontend.
    schema?: Record<string, unknown> | null;
    nextNodeLabel?: string | null;
    onSubmitInput: (nodeId: string, data: Record<string, unknown>) => Promise<void>;
    variant?: 'boxed' | 'flat';
    // Off where the surrounding block already said what this is. On a
    // notification card the question is stated in full directly above, and
    // "Input required" underneath it is the same sentence twice.
    heading?: boolean;
}) {
    const node = nodes.find((entry) => entry.id === nodeId);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const schema = (schemaOverride ?? (node?.config as Record<string, any>)?.input_schema) as Record<string, any> | undefined;
    const defaults = useMemo(() => formDefaults(schema), [schema]);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const [formData, setFormData] = useState<Record<string, any>>(defaults);
    const [touched, setTouched] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [isSubmitting, setIsSubmitting] = useState(false);

    // The resolved schema can arrive a render after the form mounts; keep
    // prefilled defaults in sync until the user starts editing.
    useEffect(() => {
        if (!touched) setFormData(defaults);
    }, [defaults, touched]);

    const setField = (key: string, value: unknown) => {
        setTouched(true);
        setFormData((prev) => ({ ...prev, [key]: value }));
    };

    const handleSubmit = async (event: React.FormEvent) => {
        event.preventDefault();
        setError(null);
        setIsSubmitting(true);
        try {
            await onSubmitInput(nodeId, formData);
        } catch (err) {
            // Inline rather than a toast: the fix is in this form, so the message
            // belongs next to it. getResourceErrorMessage unwraps the validator's
            // per-field details, which `err.message` alone throws away.
            setError(getResourceErrorMessage(err, 'Could not submit this form'));
        } finally {
            setIsSubmitting(false);
        }
    };

    if (!schema) return null;

    const properties = schema.properties || {};
    const isFlat = variant === 'flat';
    const required: string[] = Array.isArray(schema.required) ? schema.required : [];

    return (
        <div className={cn(isFlat ? 'max-w-3xl py-1' : 'state-surface-warning rounded-lg px-3 py-3')}>
            {heading ? (
                <div className="pb-3">
                    <h4 className="text-base font-semibold text-[var(--text-primary)]">Input required</h4>
                    <p className="text-sm text-[var(--text-secondary)]">
                        {nextNodeLabel ? `Submit the required values to continue to ${nextNodeLabel}.` : 'Submit the required values to continue this run.'}
                    </p>
                </div>
            ) : null}
            <form onSubmit={handleSubmit} className="space-y-4">
                {Object.entries(properties).map(([key, prop]) => {
                    const property = isRecord(prop) ? prop : {};
                    const isRequired = required.includes(key);
                    const description = typeof property.description === 'string' ? property.description : '';
                    const options = Array.isArray(property.enum) ? property.enum : null;
                    const optionLabels = Array.isArray(property.enumNames) ? property.enumNames : null;
                    const fieldValue = formData[key];

                    return (
                    <div key={key} className="space-y-1.5">
                        {/* The schema's own `title` is what the author wrote for
                            a reader; the key is what the engine binds against.
                            Showing the key made every form read like a database
                            column list. */}
                        <Label className="type-eyebrow">
                            {typeof property.title === 'string' && property.title.trim()
                                ? property.title
                                : humanizeKey(key)}
                            {isRequired ? <span className="ml-1 text-[var(--state-error)]">*</span> : null}
                        </Label>
                        {description ? (
                            // Help text, not a placeholder — a placeholder
                            // vanishes exactly when you start typing and need it.
                            <p className="text-xs leading-5 text-[var(--text-tertiary)]">{description}</p>
                        ) : null}
                        {isStructuredField(property) ? (
                            <StructuredField
                                value={fieldValue}
                                required={isRequired}
                                onChange={(next) => setField(key, next)}
                            />
                        ) : options ? (
                            <select
                                required={isRequired}
                                value={fieldValue !== undefined && fieldValue !== null ? String(fieldValue) : ''}
                                onChange={(e) => setField(key, e.target.value)}
                                className="flex h-9 w-full rounded-md border border-[var(--border-subtle)] bg-[var(--surface-1)] px-3 text-sm text-[var(--text-primary)] outline-none focus:border-[color:var(--field-border-focus)]"
                            >
                                <option value="" disabled>
                                    Select an option
                                </option>
                                {options.map((option, idx) => (
                                    <option key={String(option)} value={String(option)}>
                                        {optionLabels && typeof optionLabels[idx] === 'string' ? optionLabels[idx] : String(option)}
                                    </option>
                                ))}
                            </select>
                        ) : property.type === 'number' || property.type === 'integer' ? (
                            <Input
                                type="number"
                                required={isRequired}
                                value={typeof fieldValue === 'number' ? fieldValue : ''}
                                onChange={(e) => setField(key, e.target.value === '' ? undefined : Number(e.target.value))}
                            />
                        ) : property.type === 'boolean' || property.type === 'checkbox' ? (
                            <label className={cn('flex items-center gap-2 text-sm text-[var(--text-secondary)]', !isFlat && 'rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-1)] px-3 py-2')}>
                                <input
                                    type="checkbox"
                                    id={key}
                                    className="rounded border-[var(--card-border)]"
                                    checked={Boolean(fieldValue)}
                                    onChange={(e) => setField(key, e.target.checked)}
                                />
                                <span>{description || key}</span>
                            </label>
                        ) : (
                            <Input
                                type="text"
                                required={isRequired}
                                value={fieldValue !== undefined && fieldValue !== null ? String(fieldValue) : ''}
                                onChange={(e) => setField(key, e.target.value)}
                            />
                        )}
                    </div>
                    );
                })}
                {error ? (
                    <div className="state-surface-error flex items-center gap-2 rounded-lg px-3 py-2 text-sm">
                        <XCircle className="h-4 w-4" />
                        {error}
                    </div>
                ) : null}
                <Button variant="primary" type="submit" size="sm" className="gap-2" disabled={isSubmitting}>
                    {isSubmitting ? <StepLoader size="sm" /> : null}
                    {nextNodeLabel ? `Continue to ${nextNodeLabel}` : 'Submit and continue'}
                </Button>
            </form>
        </div>
    );
}
