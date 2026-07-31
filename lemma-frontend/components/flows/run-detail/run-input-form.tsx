import { useEffect, useMemo, useState } from 'react';
import { Loader2, XCircle } from '@/components/ui/icons';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';
import { WorkflowNode } from '@/lib/types';
import { formDefaults, isRecord } from '../run-format';

export function RunInputForm({
    nodeId,
    nodes,
    schema: schemaOverride,
    nextNodeLabel,
    onSubmitInput,
    variant = 'boxed',
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
            console.error('Failed to submit input:', err);
            setError(err instanceof Error ? err.message : 'Failed to submit input');
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
            <div className="pb-3">
                <h4 className="text-base font-semibold text-[var(--text-primary)]">Input required</h4>
                <p className="text-sm text-[var(--text-secondary)]">
                    {nextNodeLabel ? `Submit the required values to continue to ${nextNodeLabel}.` : 'Submit the required values to continue this run.'}
                </p>
            </div>
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
                        <Label className="type-eyebrow">
                            {key} {isRequired ? <span className="text-[var(--state-error)]">*</span> : null}
                        </Label>
                        {options ? (
                            <select
                                required={isRequired}
                                value={fieldValue !== undefined && fieldValue !== null ? String(fieldValue) : ''}
                                onChange={(e) => setField(key, e.target.value)}
                                className="flex h-9 w-full rounded-md border border-[var(--border-subtle)] bg-[var(--surface-1)] px-3 text-sm text-[var(--text-primary)] outline-none focus:border-[color:var(--field-border-focus)]"
                            >
                                <option value="" disabled>
                                    {description || 'Select an option'}
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
                                placeholder={description}
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
                                placeholder={description}
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
                <Button type="submit" size="sm" className="gap-2" disabled={isSubmitting}>
                    {isSubmitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                    {nextNodeLabel ? `Continue to ${nextNodeLabel}` : 'Submit and continue'}
                </Button>
            </form>
        </div>
    );
}
