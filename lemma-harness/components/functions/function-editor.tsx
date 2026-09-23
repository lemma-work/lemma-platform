'use client';

import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import Editor from '@monaco-editor/react';
import { Function as FunctionType } from '@/lib/types';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { FunctionSquare } from '@/components/ui/icons';
import { FunctionAccessDialog } from '@/components/functions/function-access-dialog';
import { FunctionContractDialog } from '@/components/functions/function-contract-dialog';
import { Nothing, WiringRow } from '@/components/pod/wiring-row';
import { ResourceHeroTitle } from '@/components/pod/resource-layout';
import { useTheme } from 'next-themes';
import { ResourceIcon } from '@/components/shared/resource-icon';
import { ResourceIconUploader } from '@/components/shared/resource-icon-uploader';
import { ResourceShareButton, getResourceVisibilityCopy, type ResourceVisibilityValue } from '@/components/shared/resource-visibility';

interface FunctionEditorProps {
    podId: string;
    functionData: FunctionType;
    onUpdate: (data: Partial<FunctionType>) => void;
    /** True only while creating, where the name is still the writer's to pick. */
    isNameEditable?: boolean;
    shareUrl?: string;
    onShareVisibilityChange?: (visibility: ResourceVisibilityValue) => void | Promise<void>;
}

type ConfigSchemaProperty = {
    type?: string;
    title?: string;
    description?: string;
    default?: unknown;
    anyOf?: Array<{ type?: string } | null>;
};

function resolveSchemaFieldType(field: ConfigSchemaProperty): string {
    if (field.type) return field.type;
    if (Array.isArray(field.anyOf)) {
        const nonNullType = field.anyOf
            .find((option) => option && option.type && option.type !== 'null')
            ?.type;
        if (nonNullType) return nonNullType;
    }
    return 'string';
}

export function FunctionEditor({
    podId,
    functionData,
    onUpdate,
    isNameEditable = false,
    shareUrl,
    onShareVisibilityChange,
}: FunctionEditorProps) {
    const [title, setTitle] = useState(functionData.name);
    const [description, setDescription] = useState(functionData.description || '');
    const [code, setCode] = useState(functionData.code || '');
    const [isPictureOpen, setIsPictureOpen] = useState(false);
    const [isAccessOpen, setIsAccessOpen] = useState(false);
    const [isContractOpen, setIsContractOpen] = useState(false);

    const { resolvedTheme } = useTheme();
    const mounted = useSyncExternalStore(
        () => () => { },
        () => true,
        () => false
    );

    const codeUpdateTimerRef = useRef<NodeJS.Timeout | null>(null);

    useEffect(() => {
        if (functionData.name !== title) setTitle(functionData.name);
        if ((functionData.description || '') !== description) setDescription(functionData.description || '');
        if (!codeUpdateTimerRef.current && (functionData.code || '') !== code) {
            setCode(functionData.code || '');
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [functionData]);

    useEffect(() => {
        return () => {
            if (codeUpdateTimerRef.current) {
                clearTimeout(codeUpdateTimerRef.current);
            }
        };
    }, []);

    const handleBlur = (field: keyof FunctionType, value: string) => {
        if (value !== functionData[field as keyof FunctionType]) {
            onUpdate({ [field]: value });
        }
    };

    const handleCodeChange = (value: string | undefined) => {
        if (value === undefined) return;

        setCode(value);

        if (codeUpdateTimerRef.current) {
            clearTimeout(codeUpdateTimerRef.current);
        }

        codeUpdateTimerRef.current = setTimeout(() => {
            codeUpdateTimerRef.current = null;
            onUpdate({ code: value });
        }, 1000);
    };

    const handleConfigChange = (key: string, value: unknown) => {
        const nextConfig = { ...(functionData.config || {}) };

        if (value === undefined || value === null || value === '') {
            delete nextConfig[key];
        } else {
            nextConfig[key] = value;
        }

        onUpdate({
            config: Object.keys(nextConfig).length > 0 ? nextConfig : null,
        });
    };


    const monacoTheme = mounted && resolvedTheme === 'dark' ? 'vs-dark' : 'vs-light';
    const configSchema = (functionData.config_schema || { type: 'object', properties: {} }) as {
        properties?: Record<string, ConfigSchemaProperty>;
        required?: string[];
    };
    const configProperties = configSchema.properties || {};
    const requiredConfigFields = new Set(Array.isArray(configSchema.required) ? configSchema.required : []);
    const configFieldCount = Object.keys(configProperties).length;

    const takes = Object.keys(
        (functionData.input_schema as { properties?: Record<string, unknown> } | undefined)?.properties || {},
    );
    const returns = Object.keys(
        (functionData.output_schema as { properties?: Record<string, unknown> } | undefined)?.properties || {},
    );
    const accessSummary = [
        functionData.accessible_connectors?.length
            ? `${functionData.accessible_connectors.length} connector${functionData.accessible_connectors.length === 1 ? '' : 's'}`
            : null,
        functionData.accessible_tables?.length
            ? `${functionData.accessible_tables.length} table${functionData.accessible_tables.length === 1 ? '' : 's'}`
            : null,
        functionData.accessible_folders?.length
            ? `${functionData.accessible_folders.length} folder${functionData.accessible_folders.length === 1 ? '' : 's'}`
            : null,
    ].filter(Boolean).join(' · ');
    const visibility = getResourceVisibilityCopy(functionData.visibility, 'functions');
    const VisibilityIcon = visibility.icon;

    return (
        <div className="resource-page-scroll h-full min-h-0">
            <div className="resource-page-column">
                <section className="resource-card">
                    <header className="agent-identity">
                        <button
                            type="button"
                            className="agent-identity-avatar"
                            onClick={() => setIsPictureOpen(true)}
                            aria-label="Change display picture"
                            title="Change display picture"
                        >
                            <ResourceIcon
                                iconUrl={functionData.icon_url}
                                alt=""
                                label={title || functionData.name}
                                className="h-full w-full rounded-xl"
                                identitySeed={functionData.id || functionData.name}
                                identityKind="mark"
                                identityGlyph={FunctionSquare}
                                identitySize={32}
                                fallback={<FunctionSquare className="h-4 w-4 text-[var(--text-secondary)]" />}
                            />
                        </button>

                        <div className="agent-identity-body">
                            <div className="agent-identity-titles">
                                {isNameEditable ? (
                                    <input
                                        value={title}
                                        onChange={(event) => setTitle(event.target.value)}
                                        onBlur={() => handleBlur('name', title)}
                                        placeholder="Untitled function"
                                        className="agent-identity-name-input"
                                    />
                                ) : (
                                    <>
                                        <ResourceHeroTitle className="agent-identity-name">
                                            {title || 'Untitled function'}
                                        </ResourceHeroTitle>
                                        {/* The stored name is what every workflow step and
                                            tool call refers to, so it reads as a fact. */}
                                        <span className="agent-identity-slug" title="Identifier">{functionData.name}</span>
                                    </>
                                )}
                            </div>

                            <div className="agent-identity-description">
                                <span aria-hidden>{description || 'What does this function do?'}&nbsp;</span>
                                <textarea
                                    className="agent-identity-description-field"
                                    value={description}
                                    onChange={(event) => setDescription(event.target.value)}
                                    onBlur={() => handleBlur('description', description)}
                                    placeholder="What does this function do?"
                                    rows={1}
                                />
                            </div>
                        </div>

                        <div className="agent-identity-chips">
                            <div className="agent-identity-chip-slot">
                                <ResourceShareButton
                                    value={functionData.visibility}
                                    podId={podId}
                                    resourceType="function"
                                    resourceId={functionData.id}
                                    resourceLabel="functions"
                                    resourceName={functionData.name}
                                    shareUrl={shareUrl}
                                    onChange={async (next) => {
                                        if (onShareVisibilityChange) await onShareVisibilityChange(next);
                                        onUpdate({ visibility: next });
                                    }}
                                    trigger={({ openShare, disabled }) => (
                                        <button
                                            type="button"
                                            className="agent-identity-chip"
                                            onClick={openShare}
                                            disabled={disabled}
                                            title={visibility.description}
                                        >
                                            <VisibilityIcon className="h-3.5 w-3.5 shrink-0" />
                                            <span className="truncate">{visibility.label}</span>
                                        </button>
                                    )}
                                />
                            </div>
                        </div>
                    </header>

                    {/* Grants and the contract are one line each with one verb, the
                        way an agent states them — not two sprawling forms. */}
                    <div className="agent-wiring">
                        <WiringRow
                            label="Can use"
                            action={(
                                <Button type="button" variant="secondary" size="sm" onClick={() => setIsAccessOpen(true)}>
                                    Manage
                                </Button>
                            )}
                        >
                            {accessSummary
                                ? <span className="agent-wiring-text">{accessSummary}</span>
                                : <Nothing>Nothing — it runs on its inputs alone.</Nothing>}
                        </WiringRow>

                        <WiringRow
                            label="Takes"
                            action={(
                                <Button type="button" variant="secondary" size="sm" onClick={() => setIsContractOpen(true)}>
                                    Edit
                                </Button>
                            )}
                        >
                            {takes.length === 0 && returns.length === 0 ? (
                                <Nothing>No contract declared yet.</Nothing>
                            ) : (
                                <span className="agent-wiring-text">
                                    {takes.length > 0 ? takes.join(', ') : 'nothing'}
                                    <span className="agent-wiring-arrow"> → </span>
                                    {returns.length > 0 ? returns.join(', ') : 'nothing'}
                                </span>
                            )}
                        </WiringRow>
                    </div>
                </section>

                {/* Only when the backend actually declares config fields — an empty
                    "0 defined" card is a box around nothing. */}
                {configFieldCount > 0 ? (
                    <section className="resource-card">
                        <p className="resource-card-eyebrow">Config values</p>
                                    <div className="space-y-3">
                                        {Object.entries(configProperties).map(([key, rawField]) => {
                                            const field = rawField || {};
                                            const fieldType = resolveSchemaFieldType(field);
                                            const label = field.title || key;
                                            const description = field.description || '';
                                            const currentValue = functionData.config?.[key] ?? field.default;

                                            return (
                                                <div key={key} className="space-y-1.5">
                                                    <label className="text-xs font-semibold uppercase tracking-wider text-[var(--text-tertiary)]">
                                                        {label}
                                                        {requiredConfigFields.has(key) && <span className="ml-0.5 text-[var(--state-error)]">*</span>}
                                                    </label>
                                                    {description && (
                                                        <p className="text-xs text-[var(--text-tertiary)]">{description}</p>
                                                    )}

                                                    {fieldType === 'boolean' ? (
                                                        <label className="flex items-center gap-2 rounded-md bg-[color:color-mix(in_srgb,var(--surface-2)_36%,transparent)] px-3 py-2 text-sm text-[var(--text-secondary)]">
                                                            <Checkbox
                                                                checked={Boolean(currentValue)}
                                                                onCheckedChange={(checked) => handleConfigChange(key, Boolean(checked))}
                                                            />
                                                            <span>{label}</span>
                                                        </label>
                                                    ) : fieldType === 'number' || fieldType === 'integer' ? (
                                                        <Input
                                                            type="number"
                                                            value={currentValue === undefined || currentValue === null ? '' : String(currentValue)}
                                                            onChange={(e) => {
                                                                const nextValue = e.target.value;
                                                                handleConfigChange(key, nextValue === '' ? undefined : Number(nextValue));
                                                            }}
                                                            placeholder={field.default === undefined || field.default === null ? '' : String(field.default)}
                                                            className="bg-[var(--bg-canvas)]"
                                                        />
                                                    ) : fieldType === 'object' || fieldType === 'array' ? (
                                                        <Textarea
                                                            key={`${key}:${JSON.stringify(currentValue ?? '')}`}
                                                            defaultValue={
                                                                currentValue === undefined || currentValue === null
                                                                    ? ''
                                                                    : JSON.stringify(currentValue, null, 2)
                                                            }
                                                            placeholder={fieldType === 'array' ? '[]' : '{}'}
                                                            className="min-h-[104px] bg-[var(--bg-canvas)] font-mono text-xs"
                                                            onBlur={(e) => {
                                                                const nextValue = e.target.value.trim();
                                                                if (!nextValue) {
                                                                    handleConfigChange(key, undefined);
                                                                    return;
                                                                }

                                                                try {
                                                                    handleConfigChange(key, JSON.parse(nextValue));
                                                                } catch {
                                                                    // Ignore invalid JSON until the user fixes it.
                                                                }
                                                            }}
                                                        />
                                                    ) : (
                                                        <Input
                                                            type="text"
                                                            value={currentValue === undefined || currentValue === null ? '' : String(currentValue)}
                                                            onChange={(e) => handleConfigChange(key, e.target.value)}
                                                            placeholder={field.default === undefined || field.default === null ? description : String(field.default)}
                                                            className="bg-[var(--bg-canvas)]"
                                                        />
                                                    )}
                                                </div>
                                            );
                                        })}
                                    </div>
                    </section>
                ) : null}

                {/* The code is to a function what instructions are to an agent: the
                    body of the page, not a tab beside it. */}
                <section className="resource-card function-code-card">
                    <p className="resource-card-eyebrow">Code</p>
                    <div className="function-code-shell">
                        <Editor
                            height="100%"
                            defaultLanguage="python"
                            value={code}
                            onChange={handleCodeChange}
                            theme={monacoTheme}
                            options={{
                                minimap: { enabled: false },
                                fontSize: 13,
                                lineNumbers: 'on',
                                scrollBeyondLastLine: false,
                                automaticLayout: true,
                                tabSize: 2,
                                wordWrap: 'on',
                                padding: { top: 8 },
                            }}
                        />
                    </div>
                </section>
            </div>

            <Dialog open={isPictureOpen} onOpenChange={setIsPictureOpen}>
                <DialogContent className="max-w-md">
                    <DialogHeader>
                        <DialogTitle>Display picture</DialogTitle>
                        <DialogDescription>Choose a small visual marker for this function.</DialogDescription>
                    </DialogHeader>
                    <ResourceIconUploader
                        kind="function"
                        name={title || functionData.name || 'Function'}
                        value={functionData.icon_url}
                        onChange={(iconUrl) => onUpdate({ icon_url: iconUrl || undefined })}
                    />
                </DialogContent>
            </Dialog>

            <FunctionAccessDialog
                open={isAccessOpen}
                onOpenChange={setIsAccessOpen}
                podId={podId}
                functionData={functionData}
                onUpdate={onUpdate}
            />
            <FunctionContractDialog
                open={isContractOpen}
                onOpenChange={setIsContractOpen}
                functionData={functionData}
                onUpdate={onUpdate}
            />
        </div>
    );
}
