'use client';

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import Editor from '@monaco-editor/react';
import { Function as FunctionType, FunctionRun, FunctionStatus } from '@/lib/types';
import { TableAccessMode } from '@/lib/types';
import { useFunctionRuns } from 'lemma-sdk/react';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { useInfiniteScroll } from '@/lib/hooks/use-infinite-scroll';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import {
    FunctionSquare,
    Loader2,
    MoreHorizontal,
    Code,
    Table as TableIcon,
    Play,
    AlertCircle,
    CheckCircle2,
    FileCode,
    Trash2,
    Copy,
    History,
    Settings,
    Clock3,
} from '@/components/ui/icons';
import { cn } from '@/lib/utils';
import { SchemaBuilder } from '@/components/agents/schema-builder';
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator } from '@/components/ui/dropdown-menu';
import { ConnectorsSelector, DatastoresSelector, FoldersSelector } from '@/components/pod/resource-selectors';
import { useTheme } from 'next-themes';
import { ResourceIcon } from '@/components/shared/resource-icon';
import { QuietEmptyState } from '@/components/shared/empty-state';
import { ResourceIconUploader } from '@/components/shared/resource-icon-uploader';
import { ResourceVisibilityBadge, ResourceVisibilitySelect, type ResourceVisibilityValue } from '@/components/shared/resource-visibility';

interface FunctionEditorProps {
    podId: string;
    functionData: FunctionType;
    panelTab: 'code' | 'config' | 'schemas' | 'runs';
    onPanelTabChange: (value: 'code' | 'config' | 'schemas' | 'runs') => void;
    onUpdate: (data: Partial<FunctionType>) => void;
    onSave?: () => void;
    onDelete?: () => void;
    onDuplicate?: () => void;
    isUpdating?: boolean;
    hasUnsavedChanges?: boolean;
    isTestPanelOpen?: boolean;
    onToggleTestPanel?: () => void;
    hideHeader?: boolean;
    isNameEditable?: boolean;
    shareUrl?: string;
    onShareVisibilityChange?: (visibility: ResourceVisibilityValue) => void | Promise<void>;
    onSelectRun?: (runId: string) => void;
}

const getRunStatusStyles = (status?: string) => {
    if (status === 'COMPLETED') {
        return 'state-badge-success';
    }
    if (status === 'FAILED') {
        return 'state-badge-error';
    }
    return 'state-badge-info';
};

const formatRunTime = (value: string) => {
    const date = new Date(value);
    return Number.isNaN(date.getTime())
        ? 'Unknown time'
        : date.toLocaleString([], {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        });
};

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
    panelTab,
    onPanelTabChange,
    onUpdate,
    onSave,
    onDelete,
    onDuplicate,
    isUpdating,
    hasUnsavedChanges,
    isTestPanelOpen,
    onToggleTestPanel,
    hideHeader,
    isNameEditable = false,
    shareUrl,
    onShareVisibilityChange,
    onSelectRun,
}: FunctionEditorProps) {
    const [title, setTitle] = useState(functionData.name);
    const [description, setDescription] = useState(functionData.description || '');
    const [code, setCode] = useState(functionData.code || '');
    const [schemaMode, setSchemaMode] = useState<'builder' | 'json'>('builder');
    const [schemaTab, setSchemaTab] = useState<'input' | 'output'>('input');

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

    const canLoadRuns = Boolean(functionData?.id && functionData?.created_at);
    const client = useMemo(() => getLemmaClient(podId), [podId]);
    const {
        runs,
        nextPageToken,
        isLoading: isLoadingRuns,
        isLoadingMore: isLoadingMoreRuns,
        loadMore: loadMoreRuns,
    } = useFunctionRuns({
        client,
        podId,
        functionName: functionData.name,
        enabled: canLoadRuns,
        limit: 20,
    });
    const sortedRuns = useMemo(() => {
        return [...(runs as FunctionRun[])].sort(
            (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
        );
    }, [runs]);
    const runsScrollRef = useRef<HTMLDivElement | null>(null);
    const runsSentinelRef = useInfiniteScroll({
        hasMore: Boolean(nextPageToken),
        isLoading: isLoadingRuns || isLoadingMoreRuns,
        onLoadMore: () => { void loadMoreRuns(); },
        rootRef: runsScrollRef,
    });

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

    const handleSchemaChange = (type: 'input_schema' | 'output_schema', newSchema: Record<string, unknown>) => {
        onUpdate({ [type]: newSchema });
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

    const getStatusDisplay = () => {
        switch (functionData.status) {
            case FunctionStatus.READY:
                return {
                    icon: CheckCircle2,
                    label: 'Ready',
                    color: 'state-badge-success',
                };
            case FunctionStatus.CODE_GENERATION:
                return {
                    icon: Loader2,
                    label: 'Generating...',
                    color: 'state-badge-info',
                };
            case FunctionStatus.ERROR:
                return {
                    icon: AlertCircle,
                    label: 'Error',
                    color: 'state-badge-error',
                };
            default:
                return {
                    icon: FileCode,
                    label: 'Draft',
                    color: 'chip-muted',
                };
        }
    };

    const status = getStatusDisplay();
    const StatusIcon = status.icon;

    const monacoTheme = mounted && resolvedTheme === 'dark' ? 'vs-dark' : 'vs-light';
    const linkedResourcesCount =
        (functionData.accessible_connectors?.length || 0) +
        (functionData.accessible_tables?.length || 0) +
        (functionData.accessible_folders?.length || 0);
    const configSchema = (functionData.config_schema || { type: 'object', properties: {} }) as {
        properties?: Record<string, ConfigSchemaProperty>;
        required?: string[];
    };
    const configProperties = configSchema.properties || {};
    const requiredConfigFields = new Set(Array.isArray(configSchema.required) ? configSchema.required : []);
    const configFieldCount = Object.keys(configProperties).length;

    return (
        <div className="flex h-full min-h-0 flex-col bg-transparent">
            {!hideHeader && (
                <div className="sticky top-0 z-10 flex h-14 items-center justify-between border-b border-[var(--border-subtle)] bg-[color:color-mix(in_srgb,var(--card-bg)_84%,transparent)] px-4 backdrop-blur-sm">
                    <div className="flex items-center gap-2 text-sm text-[var(--text-tertiary)]">
                        <ResourceIcon
                            iconUrl={functionData.icon_url}
                            alt={`${title || functionData.name} icon`}
                            label={title || functionData.name}
                            className="h-5 w-5 rounded bg-[var(--bg-muted)] border-0"
                            fallback={<FunctionSquare className="h-3 w-3 text-[var(--text-secondary)]" />}
                        />
                        <span className="text-[var(--text-tertiary)]">/</span>
                        <span className="max-w-[240px] truncate font-medium text-[var(--text-secondary)]">{title || 'Untitled Function'}</span>
                        <Badge className={cn('ml-1 h-5', status.color)}>
                            {status.label}
                        </Badge>
                        <ResourceVisibilityBadge visibility={functionData.visibility} resourceLabel="functions" className="ml-1 h-5" />
                        {isUpdating && <Loader2 className="ml-1 h-3 w-3 animate-spin text-[var(--text-tertiary)]" />}
                    </div>
                    <div className="flex items-center gap-2">
                        {onSave && (
                            <Button
                                size="sm"
                                onClick={onSave}
                                disabled={isUpdating || !hasUnsavedChanges}
                                className="h-7 px-3 text-xs font-medium"
                            >
                                {isUpdating ? 'Saving…' : hasUnsavedChanges ? 'Save' : 'Saved'}
                            </Button>
                        )}
                        {onToggleTestPanel && (
                            <Button
                                variant="secondary"
                                size="sm"
                                onClick={onToggleTestPanel}
                                className={cn(
                                    'h-7 gap-1.5 text-xs font-medium transition-colors',
                                    isTestPanelOpen
                                        ? 'border-[var(--button-secondary-border)] bg-[var(--button-secondary-bg-hover)] text-[var(--text-primary)]'
                                        : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                                )}
                            >
                                <Play className="h-3 w-3" />
                                {isTestPanelOpen ? 'Hide Test' : 'Test'}
                            </Button>
                        )}

                        <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                                <Button variant="ghost" size="icon" className="h-7 w-7 text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]">
                                    <MoreHorizontal className="h-4 w-4" />
                                </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end" className="w-48">
                                <DropdownMenuItem onClick={onDuplicate}>
                                    <Copy className="mr-2 h-4 w-4" />
                                    Duplicate
                                </DropdownMenuItem>
                                <DropdownMenuItem>
                                    <History className="mr-2 h-4 w-4" />
                                    Version History
                                </DropdownMenuItem>
                                <DropdownMenuItem>
                                    <Settings className="mr-2 h-4 w-4" />
                                    Settings
                                </DropdownMenuItem>
                                <DropdownMenuSeparator />
                                <DropdownMenuItem onClick={onDelete} className="text-[var(--state-error)] focus:text-[var(--state-error)]">
                                    <Trash2 className="mr-2 h-4 w-4" />
                                    Delete
                                </DropdownMenuItem>
                            </DropdownMenuContent>
                        </DropdownMenu>
                    </div>
                </div>
            )}

            <div className="flex-1 min-h-0 overflow-hidden">
                <Tabs value={panelTab} onValueChange={(value) => onPanelTabChange(value as typeof panelTab)} className="flex h-full min-h-0 flex-col">
                    <TabsContent value="code" className="mt-0 min-h-0 flex-1 overflow-hidden pt-0">
                        <div className="flex h-full flex-col">
                            <div className="flex-1">
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
                        </div>
                    </TabsContent>

                    <TabsContent value="config" className="mt-0 min-h-0 flex-1 overflow-y-auto px-6 py-6">
                        <div className="mx-auto grid max-w-3xl gap-5">
                            <section className="inspector-section">
                                <ResourceIconUploader
                                    kind="function"
                                    name={title || functionData.name || 'Function'}
                                    value={functionData.icon_url}
                                    onChange={(iconUrl) => onUpdate({ icon_url: iconUrl || undefined })}
                                />
                                {isNameEditable ? (
                                    <Input
                                        type="text"
                                        value={title}
                                        onChange={(e) => setTitle(e.target.value)}
                                        onBlur={() => handleBlur('name', title)}
                                        placeholder="Untitled Function"
                                        className="font-medium"
                                    />
                                ) : (
                                    <div className="form-field-control flex min-h-16 flex-col justify-center px-3 py-2.5">
                                        <div className="type-eyebrow">
                                            Identifier
                                        </div>
                                        <div className="mt-1 break-all text-sm font-medium text-[var(--text-primary)]">
                                            {functionData.name || 'Untitled Function'}
                                        </div>
                                    </div>
                                )}
                                <Textarea
                                    value={description}
                                    onChange={(e) => setDescription(e.target.value)}
                                    onBlur={() => handleBlur('description', description)}
                                    placeholder="Add description..."
                                    className="min-h-[104px] resize-y text-[var(--text-secondary)]"
                                />
                                <div className="resource-list-row">
                                    <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                                        <StatusIcon className={cn('h-3.5 w-3.5', status.label === 'Generating...' && 'animate-spin')} />
                                        <span>Status</span>
                                    </div>
                                    <Badge className={cn('function-editor-status-badge h-5 border text-xs font-medium', status.color)}>{status.label}</Badge>
                                </div>
                                <ResourceVisibilitySelect
                                    value={functionData.visibility}
                                    podId={podId}
                                    resourceType="function"
                                    resourceId={functionData.id}
                                    resourceLabel="functions"
                                    resourceName={functionData.name}
                                    shareUrl={shareUrl}
                                    onChange={async (visibility) => {
                                        if (onShareVisibilityChange) {
                                            await onShareVisibilityChange(visibility);
                                        }
                                        onUpdate({ visibility });
                                    }}
                                />
                            </section>

                            <section className="inspector-section">
                                <div className="inspector-section-header">
                                    <h3 className="inspector-section-title">Configuration</h3>
                                    <span className="inspector-section-meta">{linkedResourcesCount} linked</span>
                                </div>

                                <ConnectorsSelector
                                    podId={podId}
                                    selected={functionData.accessible_connectors || []}
                                    onChange={(configs) => onUpdate({ accessible_connectors: configs })}
                                />

                                <DatastoresSelector
                                    podId={podId}
                                    selected={(functionData.accessible_tables || []).map((entry) => entry.table_name)}
                                    modeByName={Object.fromEntries(
                                        (functionData.accessible_tables || []).map((entry) => [entry.table_name, entry.mode])
                                    )}
                                    onChange={(names) => {
                                        const modeByTable = new Map(
                                            (functionData.accessible_tables || []).map((entry) => [entry.table_name, entry.mode])
                                        );
                                        onUpdate({
                                            accessible_tables: names.map((table_name) => ({
                                                table_name,
                                                mode: modeByTable.get(table_name) ?? TableAccessMode.WRITE,
                                            })),
                                        });
                                    }}
                                    onModeChange={(name, mode) => {
                                        onUpdate({
                                            accessible_tables: (functionData.accessible_tables || []).map((entry) =>
                                                entry.table_name === name ? { ...entry, mode } : entry
                                            ),
                                        });
                                    }}
                                />

                                <FoldersSelector
                                    podId={podId}
                                    selected={functionData.accessible_folders || []}
                                    onChange={(folderIds) => onUpdate({ accessible_folders: folderIds })}
                                />
                            </section>

                            <section className="inspector-section">
                                <div className="inspector-section-header">
                                    <div>
                                        <h3 className="inspector-section-title">Config values</h3>
                                        <p className="inspector-section-meta">
                                            These fields are generated from backend-provided <code className="font-mono">config_schema</code>.
                                        </p>
                                    </div>
                                    <span className="inspector-section-meta">{configFieldCount} defined</span>
                                </div>

                                {configFieldCount === 0 ? (
                                    <QuietEmptyState className="py-1 text-xs">No config fields for this function.</QuietEmptyState>
                                ) : (
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
                                )}
                            </section>
                        </div>
                    </TabsContent>

                    <TabsContent value="schemas" className="mt-0 min-h-0 flex-1 overflow-y-auto px-6 py-6">
                        <div className="mx-auto max-w-4xl space-y-3">
                            <div className="flex items-center justify-between">
                                <h3 className="font-display flex items-center gap-1 text-sm font-semibold text-[var(--text-primary)]">
                                    <Code className="h-3.5 w-3.5" />
                                    IO Schemas
                                </h3>
                                <div className="segmented-control">
                                    <button
                                        onClick={() => setSchemaMode('builder')}
                                        className="segmented-control-item min-w-0 px-2"
                                        data-active={schemaMode === 'builder'}
                                        title="Visual Builder"
                                    >
                                        <TableIcon className="h-4 w-4" />
                                    </button>
                                    <button
                                        onClick={() => setSchemaMode('json')}
                                        className="segmented-control-item min-w-0 px-2"
                                        data-active={schemaMode === 'json'}
                                        title="JSON Editor"
                                    >
                                        <Code className="h-4 w-4" />
                                    </button>
                                </div>
                            </div>

                            <Tabs value={schemaTab} onValueChange={(value) => setSchemaTab(value as 'input' | 'output')}>
                                <TabsList>
                                    <TabsTrigger value="input">Input</TabsTrigger>
                                    <TabsTrigger value="output">Output</TabsTrigger>
                                </TabsList>

                                <TabsContent value="input" className="mt-3">
                                    {schemaMode === 'builder' ? (
                                        <SchemaBuilder
                                            value={functionData.input_schema || {}}
                                            onChange={(s) => handleSchemaChange('input_schema', s)}
                                        />
                                    ) : (
                                        <div className="h-72 overflow-hidden rounded-lg bg-[var(--bg-canvas)] shadow-[var(--shadow-xs)]">
                                            <Editor
                                                height="100%"
                                                defaultLanguage="json"
                                                theme={monacoTheme}
                                                value={JSON.stringify(functionData.input_schema || {}, null, 2)}
                                                onChange={(val) => {
                                                    try {
                                                        if (val) handleSchemaChange('input_schema', JSON.parse(val));
                                                    } catch {
                                                        // Ignore parse errors while typing
                                                    }
                                                }}
                                                options={{ minimap: { enabled: false }, fontSize: 12, wordWrap: 'on' }}
                                            />
                                        </div>
                                    )}
                                </TabsContent>

                                <TabsContent value="output" className="mt-3">
                                    {schemaMode === 'builder' ? (
                                        <SchemaBuilder
                                            value={functionData.output_schema || {}}
                                            onChange={(s) => handleSchemaChange('output_schema', s)}
                                        />
                                    ) : (
                                        <div className="h-72 overflow-hidden rounded-lg bg-[var(--bg-canvas)] shadow-[var(--shadow-xs)]">
                                            <Editor
                                                height="100%"
                                                defaultLanguage="json"
                                                theme={monacoTheme}
                                                value={JSON.stringify(functionData.output_schema || {}, null, 2)}
                                                onChange={(val) => {
                                                    try {
                                                        if (val) handleSchemaChange('output_schema', JSON.parse(val));
                                                    } catch {
                                                        // Ignore parse errors while typing
                                                    }
                                                }}
                                                options={{ minimap: { enabled: false }, fontSize: 12, wordWrap: 'on' }}
                                            />
                                        </div>
                                    )}
                                </TabsContent>
                            </Tabs>
                        </div>
                    </TabsContent>

                    <TabsContent value="runs" className="mt-0 min-h-0 flex-1 overflow-hidden p-0">
                        <div ref={runsScrollRef} className="h-full overflow-y-auto px-6 py-6">
                            <div className="mx-auto max-w-3xl space-y-3">
                                <div className="flex items-center justify-between">
                                    <h3 className="font-display flex items-center gap-1 text-sm font-semibold text-[var(--text-primary)]">
                                        <Clock3 className="h-3.5 w-3.5" />
                                        Runs
                                    </h3>
                                    {sortedRuns.length > 0 && (
                                        <span className="text-xs text-[var(--text-tertiary)]">{sortedRuns.length} loaded</span>
                                    )}
                                </div>

                                {!canLoadRuns ? (
                                    <QuietEmptyState className="py-1 text-xs">Create this function first to see run history.</QuietEmptyState>
                                ) : sortedRuns.length === 0 && isLoadingRuns ? (
                                    <div className="flex items-center justify-center gap-2 py-8 text-xs text-[var(--text-tertiary)]">
                                        <Loader2 className="h-4 w-4 animate-spin opacity-40" />
                                        Loading runs…
                                    </div>
                                ) : sortedRuns.length === 0 ? (
                                    <QuietEmptyState className="py-1 text-xs">No runs yet.</QuietEmptyState>
                                ) : (
                                    <div className="space-y-2">
                                        {sortedRuns.map((run) => (
                                            <button
                                                key={run.id}
                                                type="button"
                                                onClick={() => run.id && onSelectRun?.(run.id)}
                                                className="hover-border-intelligence w-full cursor-pointer rounded-lg border border-[color:var(--row-border)] bg-[var(--bg-canvas)] px-3 py-2 text-left shadow-[var(--shadow-xs)] transition-[background-color,border-color,box-shadow] hover:bg-[var(--row-bg-hover)]"
                                            >
                                                <div className="flex items-center justify-between gap-2">
                                                    <span className="font-mono text-xs text-[var(--text-secondary)]">#{run.id.slice(0, 8)}</span>
                                                    <Badge className={cn('h-5 type-micro-label', getRunStatusStyles(run.status))}>
                                                        {run.status}
                                                    </Badge>
                                                </div>
                                                <p className="mt-1 text-xs text-[var(--text-tertiary)]">{formatRunTime(run.created_at)}</p>
                                            </button>
                                        ))}

                                        <div ref={runsSentinelRef} aria-hidden className="h-px" />
                                        {isLoadingMoreRuns && (
                                            <div className="flex items-center justify-center py-3 text-[var(--text-tertiary)]">
                                                <Loader2 className="h-4 w-4 animate-spin opacity-40" />
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>
                        </div>
                    </TabsContent>
                </Tabs>
            </div>
        </div>
    );
}
