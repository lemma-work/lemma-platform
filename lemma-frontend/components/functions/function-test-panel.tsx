'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useFunctionSession, useFunctionRuns } from 'lemma-sdk/react';
import { useFunction } from '@/lib/hooks/use-functions';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { useInfiniteScroll } from '@/lib/hooks/use-infinite-scroll';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Play, Loader2, Clock, FunctionSquare, X, RotateCcw, FileJson, TerminalSquare, AlertCircle, CheckCircle2, ChevronRight } from '@/components/ui/icons';
import type { FunctionRun } from '@/lib/types';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';

interface FunctionTestPanelProps {
    podId: string;
    functionId: string;
    initialRunId?: string | null;
    openRunRequestKey?: number;
    onClose?: () => void;
}

type SchemaProperty = {
    description?: string;
    type?: string;
    title?: string;
};

type PreviewField = {
    key: string;
    label: string;
    value: string;
};

const getStatusStyles = (status?: string) => {
    if (status === 'COMPLETED') {
        return 'state-badge-success';
    }
    if (status === 'FAILED') {
        return 'state-badge-error';
    }
    return 'state-badge-info';
};

function isRecord(value: unknown): value is Record<string, unknown> {
    return !!value && typeof value === 'object' && !Array.isArray(value);
}

function hasRenderableValue(value: unknown): boolean {
    if (value === null || typeof value === 'undefined') return false;
    if (typeof value === 'string') return value.trim().length > 0;
    if (Array.isArray(value)) return value.length > 0;
    if (isRecord(value)) return Object.keys(value).length > 0;
    return true;
}

function formatFieldLabel(key: string): string {
    return key
        .replace(/[_-]+/g, ' ')
        .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
        .replace(/\s+/g, ' ')
        .trim()
        .replace(/\b\w/g, (char) => char.toUpperCase());
}

function truncateText(text: string, maxLength: number): string {
    const normalized = text.replace(/\s+/g, ' ').trim();
    if (normalized.length <= maxLength) return normalized;
    return `${normalized.slice(0, maxLength - 1).trim()}…`;
}

function formatPreviewValue(value: unknown): string {
    if (typeof value === 'string') return value.trim();
    if (typeof value === 'number' || typeof value === 'boolean') return String(value);
    if (Array.isArray(value)) {
        return value.map((item) => formatPreviewValue(item)).filter(Boolean).join(', ');
    }
    if (isRecord(value)) {
        try {
            return JSON.stringify(value);
        } catch {
            return '';
        }
    }
    return '';
}

function getSchemaOrderedKeys(schema: unknown): string[] {
    if (!isRecord(schema) || !isRecord(schema.properties)) return [];
    return Object.keys(schema.properties);
}

function getPreviewFields(data: unknown, schema?: unknown): PreviewField[] {
    if (!isRecord(data)) return [];

    const schemaKeys = getSchemaOrderedKeys(schema);
    const orderedKeys = schemaKeys.length > 0
        ? [...schemaKeys, ...Object.keys(data).filter((key) => !schemaKeys.includes(key))]
        : Object.keys(data);

    return orderedKeys
        .filter((key) => hasRenderableValue(data[key]))
        .map((key) => ({
            key,
            label: formatFieldLabel(key),
            value: formatPreviewValue(data[key]),
        }))
        .filter((field) => field.value.length > 0);
}

function getSchemaFieldType(field?: SchemaProperty): string {
    return String(field?.type || 'string').toLowerCase();
}

function coerceFieldValue(value: string | boolean, field?: SchemaProperty): unknown {
    const fieldType = getSchemaFieldType(field);
    if (fieldType === 'boolean') return Boolean(value);
    if (fieldType === 'number' || fieldType === 'integer') {
        if (typeof value === 'boolean' || value === '') return undefined;
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : value;
    }
    return typeof value === 'boolean' ? String(value) : value;
}

function formatRunStatus(status?: string) {
    return (status || 'UNKNOWN').replace(/[_-]+/g, ' ').toLowerCase();
}

function summarizeRunInput(inputData: unknown, schema?: unknown): {
    title: string;
    subtitle?: string;
    extras: PreviewField[];
} {
    const fields = getPreviewFields(inputData, schema);
    if (fields.length === 0) {
        return {
            title: 'No input provided',
            extras: [],
        };
    }

    const preferredPrimaryKeys = new Set(['title', 'topic', 'query', 'prompt', 'message', 'subject', 'name']);
    const preferredSecondaryKeys = new Set(['description', 'details', 'context']);
    const primaryField = fields.find((field) => preferredPrimaryKeys.has(field.key.toLowerCase())) || fields[0];
    const remainingFields = fields.filter((field) => field.key !== primaryField.key);
    const secondaryField = remainingFields.find((field) => preferredSecondaryKeys.has(field.key.toLowerCase())) || remainingFields[0];
    const extras = remainingFields.filter((field) => field.key !== secondaryField?.key).slice(0, 2);

    return {
        title: truncateText(
            preferredPrimaryKeys.has(primaryField.key.toLowerCase())
                ? primaryField.value
                : `${primaryField.label}: ${primaryField.value}`,
            72,
        ),
        subtitle: secondaryField
            ? truncateText(
                preferredSecondaryKeys.has(secondaryField.key.toLowerCase())
                    ? secondaryField.value
                    : `${secondaryField.label}: ${secondaryField.value}`,
                110,
            )
            : undefined,
        extras,
    };
}

function formatAbsoluteTime(value?: string | null): string | null {
    if (!value) return null;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return null;
    return date.toLocaleString([], {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    });
}

function formatRelativeTime(value?: string | null): string | null {
    if (!value) return null;
    const timestamp = new Date(value).getTime();
    if (Number.isNaN(timestamp)) return null;
    const diffMinutes = Math.round((timestamp - Date.now()) / (60 * 1000));
    const absMinutes = Math.abs(diffMinutes);
    if (absMinutes < 1) return 'just now';
    if (absMinutes < 60) return `${absMinutes}m ${diffMinutes < 0 ? 'ago' : 'from now'}`;
    const absHours = Math.round(absMinutes / 60);
    if (absHours < 24) return `${absHours}h ${diffMinutes < 0 ? 'ago' : 'from now'}`;
    const absDays = Math.round(absHours / 24);
    return `${absDays}d ${diffMinutes < 0 ? 'ago' : 'from now'}`;
}

function getRunDurationLabel(run: FunctionRun): string | null {
    const startedAt = run.started_at ? new Date(run.started_at).getTime() : Number.NaN;
    const completedAt = run.completed_at ? new Date(run.completed_at).getTime() : Number.NaN;
    if (!Number.isFinite(startedAt) || !Number.isFinite(completedAt) || completedAt <= startedAt) return null;
    const diffMs = completedAt - startedAt;
    if (diffMs < 1000) return `${diffMs}ms`;
    const seconds = Math.round(diffMs / 1000);
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    const remainderSeconds = seconds % 60;
    return `${minutes}m ${remainderSeconds}s`;
}

function renderStructuredValue(value: unknown) {
    if (value === null || typeof value === 'undefined') {
        return <span className="text-[var(--text-tertiary)]">Not provided</span>;
    }

    if (typeof value === 'boolean') return <span>{value ? 'Yes' : 'No'}</span>;
    if (typeof value === 'number') return <span>{Number.isInteger(value) ? value : value.toFixed(2).replace(/\.?0+$/, '')}</span>;

    if (typeof value === 'string') {
        const trimmed = value.trim();
        if (!trimmed) return <span className="text-[var(--text-tertiary)]">Not provided</span>;
        if (trimmed.includes('\n') || trimmed.length > 140) {
            return <div className="surface-panel-muted px-3 py-2.5 text-sm leading-6 whitespace-pre-wrap break-words">{trimmed}</div>;
        }
        return <span className="break-words">{trimmed}</span>;
    }

    if (Array.isArray(value)) {
        if (value.length === 0) return <span className="text-[var(--text-tertiary)]">No items</span>;
        const allPrimitive = value.every((item) => ['string', 'number', 'boolean'].includes(typeof item));
        if (allPrimitive) {
            return (
                <div className="flex flex-wrap gap-2">
                    {value.map((item, index) => (
                        <Badge key={`${String(item)}-${index}`} variant="default" className="function-test-value-badge text-xs">
                            {String(item)}
                        </Badge>
                    ))}
                </div>
            );
        }
    }

    return (
        <div className="surface-panel-muted p-3">
            <pre className="text-xs overflow-auto font-mono text-[var(--text-secondary)] whitespace-pre-wrap break-all">
                {JSON.stringify(value, null, 2)}
            </pre>
        </div>
    );
}

function StructuredDataSection({
    title,
    data,
    schema,
}: {
    title: string;
    data: Record<string, unknown>;
    schema?: Record<string, unknown>;
}) {
    const schemaProperties = isRecord(schema?.properties) ? schema.properties as Record<string, SchemaProperty> : {};
    const schemaKeys = Object.keys(schemaProperties);
    const orderedKeys = schemaKeys.length > 0
        ? [...schemaKeys, ...Object.keys(data).filter((key) => !schemaKeys.includes(key))]
        : Object.keys(data);

    const fields = orderedKeys
        .filter((key) => Object.prototype.hasOwnProperty.call(data, key) && hasRenderableValue(data[key]))
        .map((key) => ({
            key,
            value: data[key],
            schema: schemaProperties[key],
        }));

    if (fields.length === 0) return null;

    return (
        <section className="space-y-3">
            <div className="flex items-center justify-between">
                <label className="text-xs font-semibold text-[var(--text-secondary)]">{title}</label>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {fields.map(({ key, value, schema: fieldSchema }) => {
                    const isLongText = typeof value === 'string' && (value.includes('\n') || value.length > 140);
                    const isComplex = Array.isArray(value) || isRecord(value) || fieldSchema?.type === 'object';
                    return (
                        <div
                            key={key}
                            className={cn(
                                "surface-panel p-4",
                                (isLongText || isComplex) && "sm:col-span-2"
                            )}
                        >
                            <p className="type-eyebrow">
                                {fieldSchema?.title?.trim() || formatFieldLabel(key)}
                            </p>
                            {fieldSchema?.description && (
                                <p className="mt-1 text-xs text-[var(--text-tertiary)] leading-5">
                                    {fieldSchema.description}
                                </p>
                            )}
                            <div className="mt-2 text-sm text-[var(--text-primary)] leading-6">
                                {renderStructuredValue(value)}
                            </div>
                        </div>
                    );
                })}
            </div>
        </section>
    );
}

function LogsViewer({ logs }: { logs: string }) {
    const [isExpanded, setIsExpanded] = useState(false);
    const lines = useMemo(
        () => logs.split(/\r?\n/).filter((line, index, all) => line.length > 0 || (index > 0 && index < all.length - 1)),
        [logs]
    );
    const visibleLines = lines;

    const getLineTone = (line: string) => {
        const lower = line.toLowerCase();
        if (lower.includes('error') || lower.includes('traceback') || lower.includes('exception')) {
            return 'text-[var(--state-error)]';
        }
        if (lower.includes('warn')) {
            return 'text-[var(--state-warning)]';
        }
        if (lower.includes('success') || lower.includes('completed')) {
            return 'text-[var(--state-success)]';
        }
        return 'text-[var(--text-secondary)]';
    };

    return (
        <section className="space-y-3">
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                    <label className="text-xs font-medium text-[var(--text-secondary)]">Logs</label>
                    <span className="text-xs text-[var(--text-tertiary)]">{lines.length} lines</span>
                </div>
                <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setIsExpanded((prev) => !prev)}
                    className="h-6 px-2 text-xs text-[var(--text-tertiary)]"
                >
                    {isExpanded ? 'Hide logs' : 'View logs'}
                    <ChevronRight className={cn('ml-1 h-3 w-3 transition-transform', isExpanded && 'rotate-90')} />
                </Button>
            </div>

            {isExpanded ? (
            <div className="overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[color:color-mix(in_srgb,_var(--bg-canvas)_86%,_var(--bg-subtle))]">
                <div className="flex items-center gap-2 border-b border-[var(--border-subtle)] px-3 py-2 type-eyebrow-medium">
                    <TerminalSquare className="h-3.5 w-3.5" />
                    Execution Log
                </div>
                <div className="max-h-72 overflow-auto px-3 py-2 font-mono text-xs leading-6">
                    {visibleLines.length === 0 ? (
                        <div className="text-[var(--text-tertiary)]">No logs captured.</div>
                    ) : visibleLines.map((line, index) => (
                        <div key={`${index}-${line.slice(0, 12)}`} className="grid grid-cols-[40px_minmax(0,1fr)] gap-3">
                            <span className="select-none text-right text-[color:color-mix(in_srgb,_var(--text-tertiary)_70%,_transparent)]">{index + 1}</span>
                            <span className={cn("whitespace-pre-wrap break-words", getLineTone(line))}>{line}</span>
                        </div>
                    ))}
                </div>
            </div>
            ) : null}
        </section>
    );
}

function FunctionRunDetails({
    run,
    inputSchema,
    outputSchema,
    title,
}: {
    run: FunctionRun;
    inputSchema?: Record<string, unknown>;
    outputSchema?: Record<string, unknown>;
    title: string;
}) {
    return (
        <section className="space-y-4">
            <div className="surface-panel p-3">
                <div className="flex flex-wrap items-start justify-between gap-4">
                    <div className="space-y-1">
                        <p className="text-sm font-medium text-[var(--text-primary)]">{title}</p>
                        <div className="flex items-center gap-2 flex-wrap">
                            <RunStatusMarker status={run.status} />
                            {run.id && (
                                <span className="font-mono text-xs text-[var(--text-tertiary)]">#{run.id.slice(0, 8)}</span>
                            )}
                        </div>
                    </div>
                    <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs">
                        <div>
                            <p className="type-eyebrow-medium">Started</p>
                            <p className="mt-1 text-[var(--text-primary)]">{formatAbsoluteTime(run.started_at || run.created_at) || 'Unknown'}</p>
                        </div>
                        <div>
                            <p className="type-eyebrow-medium">Duration</p>
                            <p className="mt-1 text-[var(--text-primary)]">{getRunDurationLabel(run) || 'In progress'}</p>
                        </div>
                    </div>
                </div>
            </div>

            {isRecord(run.input_data) && (
                <StructuredDataSection
                    title="Run Input"
                    data={run.input_data}
                    schema={inputSchema}
                />
            )}

            {run.status === 'FAILED' ? (
                <section className="space-y-3">
                    <label className="text-xs font-medium text-[var(--text-secondary)]">Error</label>
                    <div className="state-surface-error rounded-lg p-3">
                        <div className="flex items-start gap-3">
                            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--state-error)]" />
                            <div className="text-sm leading-6 text-[var(--state-error)] whitespace-pre-wrap break-words">
                                {run.error || 'Function run failed'}
                            </div>
                        </div>
                    </div>
                </section>
            ) : isRecord(run.output_data) ? (
                <StructuredDataSection
                    title="Output"
                    data={run.output_data}
                    schema={outputSchema}
                />
            ) : (
                <section className="space-y-3">
                    <label className="text-xs font-semibold text-[var(--text-secondary)]">Output</label>
                    <div className="surface-panel-muted p-4">
                        <pre className="text-xs font-mono text-[var(--text-secondary)] whitespace-pre-wrap overflow-x-auto">
                            {JSON.stringify(run.output_data, null, 2)}
                        </pre>
                    </div>
                </section>
            )}

            {run.logs && <LogsViewer logs={run.logs} />}
        </section>
    );
}

function RunStatusMarker({ status }: { status?: string }) {
    const normalized = status?.trim().toUpperCase();
    const tone =
        normalized === 'FAILED'
            ? 'text-[var(--state-error)]'
            : normalized === 'COMPLETED'
                ? 'text-[var(--state-success)]'
                : 'text-[var(--state-info)]';

    return (
        <span className={cn('inline-flex items-center gap-1.5 text-xs font-normal capitalize', tone)}>
            <span className="h-1.5 w-1.5 rounded-full bg-current" />
            {formatRunStatus(status)}
        </span>
    );
}

export function FunctionTestPanel({ podId, functionId, initialRunId, openRunRequestKey, onClose }: FunctionTestPanelProps) {
    const { data: functionData } = useFunction(podId, functionId);
    const [inputData, setInputData] = useState('{}');
    const [formData, setFormData] = useState<Record<string, string>>({});
    const [activeTab, setActiveTab] = useState<'test' | 'history'>('test');
    const [currentRunId, setCurrentRunId] = useState<string | null>(null);
    const [currentRunSource, setCurrentRunSource] = useState<'new' | 'history' | null>(null);
    const [useRawJson, setUseRawJson] = useState(false);
    const [isSubmitting, setIsSubmitting] = useState(false);
    const pendingHistoryRefreshRunIdRef = useRef<string | null>(null);
    const handledOpenRunRequestRef = useRef<string | number | null>(null);
    const client = useMemo(() => getLemmaClient(podId), [podId]);

    const functionIdentifier = functionData?.name || functionId;
    const functionSession = useFunctionSession({
        client,
        podId,
        functionName: functionIdentifier,
        autoPoll: true,
        pollIntervalMs: 2000,
    });
    const {
        start: startFunctionRun,
        runId: functionSessionRunId,
        run: functionSessionRun,
        status: functionSessionStatus,
        setRunId: setFunctionSessionRunId,
    } = functionSession;

    const {
        runs,
        nextPageToken,
        isLoading: isLoadingRuns,
        isLoadingMore: isLoadingMoreRuns,
        loadMore: loadMoreRuns,
        refresh: refreshRunsList,
    } = useFunctionRuns({
        client,
        podId,
        functionName: functionIdentifier,
        enabled: Boolean(functionIdentifier),
        limit: 20,
    });

    const refreshRuns = useCallback(async () => {
        await refreshRunsList();
    }, [refreshRunsList]);

    const historyScrollRef = useRef<HTMLDivElement | null>(null);
    const historySentinelRef = useInfiniteScroll({
        hasMore: Boolean(nextPageToken),
        isLoading: isLoadingRuns || isLoadingMoreRuns,
        onLoadMore: () => { void loadMoreRuns(); },
        rootRef: historyScrollRef,
    });

    const schemaProperties = useMemo(
        () => (functionData?.input_schema?.properties || {}) as Record<string, SchemaProperty>,
        [functionData?.input_schema?.properties]
    );

    const hasSchema = Object.keys(schemaProperties).length > 0;

    useEffect(() => {
        if (activeTab !== 'history') return;
        void refreshRuns();
    }, [activeTab, refreshRuns]);

    useEffect(() => {
        setFunctionSessionRunId(currentRunId);
    }, [currentRunId, setFunctionSessionRunId]);

    useEffect(() => {
        if (!initialRunId) return;

        const requestKey = openRunRequestKey ?? initialRunId;
        if (handledOpenRunRequestRef.current === requestKey) return;
        handledOpenRunRequestRef.current = requestKey;

        setCurrentRunId(initialRunId);
        setCurrentRunSource('history');
        setActiveTab('test');
    }, [initialRunId, openRunRequestKey]);

    useEffect(() => {
        if (!functionSessionRunId) return;
        if (pendingHistoryRefreshRunIdRef.current !== functionSessionRunId) return;

        const normalizedStatus = functionSessionStatus?.trim().toUpperCase();
        if (!normalizedStatus || !['COMPLETED', 'FAILED', 'CANCELLED'].includes(normalizedStatus)) {
            return;
        }

        pendingHistoryRefreshRunIdRef.current = null;
        void refreshRuns();
    }, [functionSessionRunId, functionSessionStatus, refreshRuns]);

    const sortedRuns = useMemo(
        () => [...(runs as unknown as FunctionRun[])].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()),
        [runs]
    );

    const currentRun = currentRunId
        ? ((functionSessionRun?.id === currentRunId ? functionSessionRun : (runs as unknown as FunctionRun[]).find((run) => run.id === currentRunId)) || null)
        : null;

    const handleRun = async () => {
        setIsSubmitting(true);
        try {
            let parsedInput: Record<string, unknown> = {};

            if (hasSchema && !useRawJson) {
                parsedInput = Object.fromEntries(
                    Object.entries(schemaProperties).map(([key, field]) => [
                        key,
                        coerceFieldValue(formData[key] ?? '', field),
                    ]).filter(([, value]) => typeof value !== 'undefined')
                );
            } else {
                try {
                    parsedInput = JSON.parse(inputData);
                } catch {
                    toast.error('Invalid JSON input');
                    return;
                }
            }

            const run = await startFunctionRun({
                functionName: functionData?.name || functionId,
                input: parsedInput,
            });

            pendingHistoryRefreshRunIdRef.current = run.id ?? null;
            setCurrentRunId(run.id ?? null);
            setCurrentRunSource('new');
            setActiveTab('test');
            await refreshRuns();
        } catch (error) {
            console.error('Failed to run function:', error);
            toast.error('Failed to run function');
        } finally {
            setIsSubmitting(false);
        }
    };

    const handleReset = () => {
        setCurrentRunId(null);
        setCurrentRunSource(null);
        setInputData('{}');
        setFormData({});
    };

    const showComposer = !currentRun || currentRunSource !== 'history';

    const handleTabChange = (value: string) => {
        const nextTab = value as 'test' | 'history';
        if (nextTab === 'test' && activeTab !== 'test') {
            setCurrentRunId(null);
            setCurrentRunSource(null);
        }
        setActiveTab(nextTab);
    };

    return (
        <div className="relative flex h-full flex-col bg-[var(--card-bg)]">
            <div className="absolute right-3 top-3 z-10 flex items-center gap-2">
                <Tabs value={activeTab} onValueChange={handleTabChange}>
                    <TabsList className="h-7 p-0.5 bg-[var(--bg-subtle)]">
                        <TabsTrigger value="test" className="h-6 text-xs px-2.5 rounded-md">Run</TabsTrigger>
                        <TabsTrigger value="history" className="h-6 text-xs px-2.5 rounded-md">History</TabsTrigger>
                    </TabsList>
                </Tabs>
                {onClose && (
                    <>
                        <div className="w-px h-4 bg-[var(--bg-muted)] mx-1" />
                        <Button
                            variant="ghost"
                            size="icon"
                            onClick={onClose}
                            className="h-7 w-7 text-[var(--text-tertiary)] hover:text-[var(--text-secondary)] rounded-md"
                            aria-label="Close run inspector"
                        >
                            <X className="w-4 h-4" />
                        </Button>
                    </>
                )}
            </div>

            {activeTab === 'test' ? (
                <div className="flex-1 overflow-y-auto p-4 pt-14 space-y-4">
                    {showComposer ? (
                        <section className="space-y-3">
                            <div className="flex items-center justify-between">
                                <label className="text-xs font-medium text-[var(--text-secondary)]">Input</label>
                                <div className="flex items-center gap-2">
                                    {hasSchema && (
                                        <Button
                                            variant="ghost"
                                            size="sm"
                                            onClick={() => setUseRawJson((prev) => !prev)}
                                            className={cn(
                                                'h-6 text-xs',
                                                useRawJson
                                                    ? 'border border-[color:var(--chip-border)] bg-[var(--chip-bg)] text-[var(--chip-fg)]'
                                                    : 'text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]'
                                            )}
                                        >
                                            <FileJson className="w-3 h-3 mr-1" />
                                            Raw JSON
                                        </Button>
                                    )}
                                    <Button variant="ghost" size="sm" onClick={handleReset} className="h-6 text-xs text-[var(--text-tertiary)]">
                                        Reset
                                    </Button>
                                </div>
                            </div>

                            <div className="space-y-3">
                                {hasSchema && !useRawJson ? (
                                    <div className="grid gap-3 sm:grid-cols-2">
                                    {Object.entries(schemaProperties).map(([key, field]) => {
                                        const fieldType = getSchemaFieldType(field);
                                        return (
                                        <div key={key} className={cn('space-y-1.5', fieldType === 'string' && 'sm:col-span-2')}>
                                            <div className="flex items-center gap-2">
                                                <Label className="text-xs font-medium text-[var(--text-tertiary)] uppercase tracking-wider">{key}</Label>
                                                {field.type && (
                                                    <span className="text-xs text-[var(--text-tertiary)] px-1.5 py-0.5 rounded bg-[var(--bg-subtle)]">
                                                        {field.type}
                                                    </span>
                                                )}
                                            </div>
                                            {field.description && <p className="text-xs text-[var(--text-tertiary)]">{field.description}</p>}
                                            {fieldType === 'boolean' ? (
                                                <label className="flex h-8 items-center gap-2 rounded-md border border-[color:var(--field-border)] bg-[var(--field-bg)] px-2 text-sm text-[var(--text-secondary)]">
                                                    <input
                                                        type="checkbox"
                                                        checked={formData[key] === 'true'}
                                                        onChange={(event) => setFormData((prev) => ({ ...prev, [key]: event.target.checked ? 'true' : '' }))}
                                                        className="h-3.5 w-3.5"
                                                    />
                                                    Yes
                                                </label>
                                            ) : (
                                                <Input
                                                    type={fieldType === 'number' || fieldType === 'integer' ? 'number' : 'text'}
                                                    value={formData[key] || ''}
                                                    onChange={(e) => setFormData((prev) => ({ ...prev, [key]: e.target.value }))}
                                                    placeholder={`Enter ${key}...`}
                                                    className="h-8 text-sm bg-[var(--bg-canvas)] dark:bg-[color:color-mix(in_srgb,_var(--bg-canvas)_80%,_transparent)]"
                                                />
                                            )}
                                        </div>
                                        );
                                    })}
                                    </div>
                                ) : (
                                    <Textarea
                                        value={inputData}
                                        onChange={(e) => setInputData(e.target.value)}
                                        placeholder='{ "key": "value" }'
                                        className="font-mono text-xs min-h-[120px] resize-y bg-[var(--bg-canvas)] dark:bg-[color:color-mix(in_srgb,_var(--bg-canvas)_80%,_transparent)]"
                                    />
                                )}

                                <Button onClick={handleRun} disabled={isSubmitting} className="w-full h-8">
                                    {isSubmitting ? (
                                        <>
                                            <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                                            Running...
                                        </>
                                    ) : (
                                        <>
                                            <Play className="w-4 h-4 mr-2" />
                                            Run Function
                                        </>
                                    )}
                                </Button>
                            </div>
                        </section>
                    ) : (
                        <div className="flex items-center justify-end">
                            <Button
                                variant="ghost"
                                size="sm"
                                onClick={handleReset}
                                className="h-8 text-xs text-[var(--text-tertiary)] hover:text-[var(--text-primary)]"
                            >
                                <Play className="w-3.5 h-3.5 mr-1.5" />
                                New run
                            </Button>
                        </div>
                    )}

                    {currentRun ? (
                        <div className="pt-3">
                            <FunctionRunDetails
                                run={currentRun}
                                inputSchema={functionData?.input_schema}
                                outputSchema={functionData?.output_schema}
                                title="Latest Run"
                            />
                        </div>
                    ) : (
                        <div className="pt-3">
                            <div className="surface-panel-dashed p-5 text-center">
                                <div className="surface-panel-muted mx-auto flex h-11 w-11 items-center justify-center text-[var(--text-tertiary)]">
                                    <FunctionSquare className="h-5 w-5" />
                                </div>
                                <p className="mt-3 text-sm font-medium text-[var(--text-primary)]">Run the function to inspect output and logs</p>
                                <p className="mt-1 text-xs text-[var(--text-tertiary)]">Or pick a previous run from History.</p>
                            </div>
                        </div>
                    )}
                </div>
            ) : (
                <div className="flex min-h-0 flex-1 flex-col bg-[color:color-mix(in_srgb,_var(--bg-canvas)_40%,_transparent)] p-2 pt-14 dark:bg-[color:color-mix(in_srgb,_var(--bg-canvas)_70%,_transparent)]">
                    {isLoadingRuns && runs.length === 0 ? (
                        <div className="flex flex-col items-center justify-center h-full text-[var(--text-tertiary)] p-8">
                            <Loader2 className="w-8 h-8 mb-2 animate-spin opacity-30" />
                            <p className="text-xs">Loading runs...</p>
                        </div>
                    ) : runs.length === 0 ? (
                        <div className="flex flex-col items-center justify-center h-full text-[var(--text-tertiary)] p-8">
                            <Clock className="w-8 h-8 mb-2 opacity-20" />
                            <p className="text-xs">No run history</p>
                        </div>
                    ) : (
                        <div ref={historyScrollRef} className="min-h-0 flex-1 overflow-y-auto">
                            <div className="space-y-1.5">
                            {sortedRuns.map((run) => {
                                const preview = summarizeRunInput(run.input_data, functionData?.input_schema);
                                const duration = getRunDurationLabel(run);
                                const absoluteTime = formatAbsoluteTime(run.created_at);
                                const relativeTime = formatRelativeTime(run.created_at);

                                return (
                                    <button
                                        key={run.id || `${run.status || 'unknown'}-${run.created_at || 'unknown'}`}
                                        onClick={() => {
                                            setCurrentRunId(run.id ?? null);
                                            setCurrentRunSource('history');
                                            setActiveTab('test');
                                        }}
                                        className={cn(
                                            'function-test-run-button w-full cursor-pointer rounded-lg border p-3.5 text-left transition-[background-color,border-color,box-shadow,color] group',
                                            currentRunId === run.id
                                                ? 'state-surface-info bg-[var(--card-bg)] shadow-[var(--shadow-sm)]'
                                                : 'hover-border-intelligence border-[color:var(--row-border)] bg-[var(--row-bg)] hover:bg-[var(--row-bg-hover)] hover:shadow-[var(--shadow-xs)]'
                                        )}
                                    >
                                        <div className="flex items-start justify-between gap-3">
                                            <div className="min-w-0 flex-1 space-y-2">
                                                <div className="flex items-center gap-2 flex-wrap">
                                                    {run.id && <span className="font-mono text-xs font-medium text-[var(--text-tertiary)]">#{run.id.slice(0, 8)}</span>}
                                                    <Badge className={cn('function-test-status-badge type-micro-label h-5 px-1.5', getStatusStyles(run.status))}>
                                                        {run.status}
                                                    </Badge>
                                                    {duration && <span className="text-xs text-[var(--text-tertiary)]">{duration}</span>}
                                                </div>

                                                <div className="min-w-0">
                                                    <p className="text-sm font-medium text-[var(--text-primary)] truncate">
                                                        {preview.title}
                                                    </p>
                                                    {preview.subtitle && (
                                                        <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)] line-clamp-2">
                                                            {preview.subtitle}
                                                        </p>
                                                    )}
                                                </div>

                                                {run.error && (
                                                    <p className="text-xs text-[var(--state-error)] truncate">
                                                        {run.error}
                                                    </p>
                                                )}

                                                <div className="flex items-center justify-between gap-3 text-xs text-[var(--text-tertiary)]">
                                                    <span>{absoluteTime || 'Unknown time'}</span>
                                                    {relativeTime && <span>{relativeTime}</span>}
                                                </div>
                                            </div>

                                            <div className="ml-3 shrink-0 opacity-70 transition-opacity group-hover:opacity-100">
                                                {run.status === 'COMPLETED' ? (
                                                    <CheckCircle2 className="w-4 h-4 text-[var(--state-success)]" />
                                                ) : run.status === 'FAILED' ? (
                                                    <AlertCircle className="w-4 h-4 text-[var(--state-error)]" />
                                                ) : (
                                                    <RotateCcw className="w-4 h-4 text-[var(--text-tertiary)]" />
                                                )}
                                            </div>
                                        </div>
                                    </button>
                                );
                            })}
                            <div ref={historySentinelRef} aria-hidden className="h-px" />
                            {isLoadingMoreRuns && (
                                <div className="flex items-center justify-center py-3 text-[var(--text-tertiary)]">
                                    <Loader2 className="w-4 h-4 animate-spin opacity-40" />
                                </div>
                            )}
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
