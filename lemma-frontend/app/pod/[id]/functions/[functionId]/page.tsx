'use client';

import { use, useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, Play, Save, Share2 } from '@/components/ui/icons';

import { FunctionEditor } from '@/components/functions/function-editor';
import { FunctionTestPanel } from '@/components/functions/function-test-panel';
import {
    ResourceDetailHeader,
    ResourceDetailShell,
    ResourceHeaderTabs,
    ResourceWorkSplit,
} from '@/components/pod/resource-layout';
import { ResourceArrivalNotice } from '@/components/shared/resource-feedback';
import { ResourceShareButton, ResourceVisibilityBadge, type ResourceVisibilityValue } from '@/components/shared/resource-visibility';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { resourceAllows } from '@/lib/authz/resource-actions';
import { useFunction, useUpdateFunction } from '@/lib/hooks/use-functions';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { Function as FunctionType, UpdateFunctionData } from '@/lib/types';

const SIDEBAR_WIDTH_CLASSES = [
    'w-[35%]', 'w-[36%]', 'w-[37%]', 'w-[38%]', 'w-[39%]', 'w-[40%]', 'w-[41%]', 'w-[42%]',
    'w-[43%]', 'w-[44%]', 'w-[45%]', 'w-[46%]', 'w-[47%]', 'w-[48%]', 'w-[49%]', 'w-[50%]',
    'w-[51%]', 'w-[52%]', 'w-[53%]', 'w-[54%]', 'w-[55%]', 'w-[56%]', 'w-[57%]', 'w-[58%]',
    'w-[59%]', 'w-[60%]',
] as const;

export default function FunctionDetailPage({
    params,
}: {
    params: Promise<{ id: string; functionId: string }>;
}) {
    const { id: podId, functionId } = use(params);
    const podAccess = usePodAccess(podId);
    const canUpdateFunction = podAccess.can('function.update');
    const canExecuteFunction = podAccess.can('function.execute');
    const canCreateWorkflow = podAccess.can('workflow.create');

    const { data: functionData, isLoading } = useFunction(podId, functionId);
    const updateFunction = useUpdateFunction();
    const { mutateAsync: updateFunctionAsync } = updateFunction;

    const [localData, setLocalData] = useState<FunctionType | null>(null);
    const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);
    const [isTestPanelOpen, setIsTestPanelOpen] = useState(true);
    const [panelTab, setPanelTab] = useState<'code' | 'config' | 'schemas' | 'runs'>('code');
    const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
    const [openRunRequestKey, setOpenRunRequestKey] = useState(0);
    const [sidebarWidthPct, setSidebarWidthPct] = useState(48);
    const [isResizingSidebar, setIsResizingSidebar] = useState(false);
    const [layoutWidth, setLayoutWidth] = useState(0);
    const layoutRef = useRef<HTMLDivElement | null>(null);

    const lastSavedHashRef = useRef('');
    const lastFailedHashRef = useRef<string | null>(null);

    const buildUpdatePayload = useCallback((data: FunctionType): UpdateFunctionData => ({
        description: data.description,
        icon_url: data.icon_url,
        code: data.code,
        config: data.config,
        input_schema: data.input_schema,
        output_schema: data.output_schema,
        accessible_tables: data.accessible_tables,
        accessible_folders: data.accessible_folders,
        accessible_connectors: data.accessible_connectors,
        visibility: data.visibility as UpdateFunctionData['visibility'],
    }), []);

    useEffect(() => {
        if (functionData && !hasUnsavedChanges) {
            // eslint-disable-next-line react-hooks/set-state-in-effect
            setLocalData(functionData);
            lastSavedHashRef.current = JSON.stringify(buildUpdatePayload(functionData));
            lastFailedHashRef.current = null;
        }
    }, [buildUpdatePayload, functionData, hasUnsavedChanges]);

    const isEqualValue = (currentValue: unknown, nextValue: unknown): boolean => {
        if (Object.is(currentValue, nextValue)) return true;
        if (typeof currentValue === 'object' && currentValue !== null && typeof nextValue === 'object' && nextValue !== null) {
            try {
                return JSON.stringify(currentValue) === JSON.stringify(nextValue);
            } catch {
                return false;
            }
        }
        return false;
    };

    const handleUpdate = useCallback((updates: Partial<FunctionType>) => {
        setLocalData((prev) => {
            if (!prev) return prev;
            if (!resourceAllows(prev, 'function.update', canUpdateFunction)) return prev;

            const changed = Object.entries(updates).some(([key, value]) => {
                const currentValue = prev[key as keyof FunctionType];
                return !isEqualValue(currentValue, value);
            });

            if (!changed) return prev;
            setHasUnsavedChanges(true);
            lastFailedHashRef.current = null;
            return { ...prev, ...updates };
        });
    }, [canUpdateFunction]);

    const handleSave = useCallback(async () => {
        const currentData = localData;
        if (!currentData) return;
        if (!resourceAllows(currentData, 'function.update', canUpdateFunction)) return;

        const payload = buildUpdatePayload(currentData);
        const payloadHash = JSON.stringify(payload);

        if (payloadHash === lastSavedHashRef.current) {
            setHasUnsavedChanges(false);
            lastFailedHashRef.current = null;
            return;
        }

        if (payloadHash === lastFailedHashRef.current) {
            return;
        }

        try {
            await updateFunctionAsync({
                podId,
                name: functionId,
                data: payload,
            });
            lastSavedHashRef.current = payloadHash;
            lastFailedHashRef.current = null;
            setHasUnsavedChanges(false);
        } catch (error) {
            lastFailedHashRef.current = payloadHash;
            console.error('Failed to save function:', error);
        }
    }, [buildUpdatePayload, canUpdateFunction, functionId, localData, podId, updateFunctionAsync]);

    const handleShareVisibilityChange = useCallback(async (visibility: ResourceVisibilityValue) => {
        const currentData = localData;
        if (!currentData) return;
        if (!resourceAllows(currentData, 'function.update', canUpdateFunction)) return;

        await updateFunctionAsync({
            podId,
            name: functionId,
            data: { visibility: visibility as UpdateFunctionData['visibility'] },
        });

        const nextData = { ...currentData, visibility };
        setLocalData((prev) => prev ? { ...prev, visibility } : prev);
        lastFailedHashRef.current = null;

        if (!hasUnsavedChanges) {
            lastSavedHashRef.current = JSON.stringify(buildUpdatePayload(nextData));
        }
    }, [buildUpdatePayload, canUpdateFunction, functionId, hasUnsavedChanges, localData, podId, updateFunctionAsync]);

    useEffect(() => {
        const container = layoutRef.current;
        if (!container) return;

        const syncWidth = () => {
            setLayoutWidth(container.getBoundingClientRect().width);
        };

        syncWidth();

        const observer = new ResizeObserver(syncWidth);
        observer.observe(container);

        return () => observer.disconnect();
    }, []);

    const isStackedLayout = layoutWidth > 0 && layoutWidth < 1500;
    const canUpdateCurrentFunction = resourceAllows(localData, 'function.update', canUpdateFunction);
    const canExecuteCurrentFunction = resourceAllows(localData, 'function.execute', canExecuteFunction);
    const canShowTestPanel = canExecuteCurrentFunction && isTestPanelOpen;
    const functionShareUrl = typeof window === 'undefined'
        ? undefined
        : `${window.location.origin}/pod/${podId}/functions/${encodeURIComponent(localData?.name || functionId)}`;

    useEffect(() => {
        if (!isResizingSidebar) return;

        const handleMouseMove = (event: MouseEvent) => {
            const container = layoutRef.current;
            if (!container) return;

            const rect = container.getBoundingClientRect();
            const nextWidth = ((rect.right - event.clientX) / rect.width) * 100;
            const clampedWidth = Math.max(35, Math.min(60, Math.round(nextWidth)));
            setSidebarWidthPct(clampedWidth);
        };

        const handleMouseUp = () => {
            setIsResizingSidebar(false);
        };

        window.addEventListener('mousemove', handleMouseMove);
        window.addEventListener('mouseup', handleMouseUp);

        return () => {
            window.removeEventListener('mousemove', handleMouseMove);
            window.removeEventListener('mouseup', handleMouseUp);
        };
    }, [isResizingSidebar]);

    if (isLoading) {
        return (
            <div className="flex h-full overflow-hidden bg-transparent">
                <div className="flex min-w-0 flex-1 flex-col">
                    <div className="sticky top-0 z-10 flex items-center justify-between bg-[color:color-mix(in_srgb,var(--surface-1)_88%,transparent)] px-4 py-2 shadow-[var(--shadow-xs)]">
                        <div className="flex items-center gap-2">
                            <div className="h-5 w-5 animate-pulse rounded bg-[var(--bg-subtle)]" />
                            <div className="h-5 w-32 animate-pulse rounded bg-[var(--bg-subtle)]" />
                        </div>
                        <div className="flex gap-2">
                            <div className="h-7 w-16 animate-pulse rounded bg-[var(--bg-subtle)]" />
                            <div className="h-7 w-8 animate-pulse rounded bg-[var(--bg-subtle)]" />
                        </div>
                    </div>

                    <div className="flex-1 space-y-8 p-12">
                        <div className="h-16 w-16 animate-pulse rounded-xl bg-[var(--bg-subtle)]" />
                        <div className="h-10 max-w-md animate-pulse rounded bg-[var(--bg-subtle)]" />
                        <div className="space-y-4">
                            <div className="h-8 w-full animate-pulse rounded bg-[var(--bg-subtle)]" />
                            <div className="h-24 w-full animate-pulse rounded bg-[var(--bg-subtle)]" />
                        </div>
                    </div>
                </div>

                <div className="hidden w-[500px] border-l border-[color:color-mix(in_srgb,var(--border-subtle)_35%,transparent)] bg-[var(--surface-1)] lg:block">
                    <div className="p-4">
                        <div className="h-8 w-32 animate-pulse rounded bg-[var(--bg-subtle)]" />
                    </div>
                    <div className="space-y-4 p-4">
                        <div className="h-32 animate-pulse rounded bg-[var(--bg-subtle)]" />
                        <div className="h-10 animate-pulse rounded bg-[var(--bg-subtle)]" />
                    </div>
                </div>
            </div>
        );
    }

    if (!localData) {
        return (
            <div className="flex h-full items-center justify-center bg-transparent">
                <div className="text-center">
                    <h2 className="font-display text-2xl font-semibold text-[var(--text-primary)]">Function not found</h2>
                </div>
            </div>
        );
    }

    const sidebarWidthClass = SIDEBAR_WIDTH_CLASSES[sidebarWidthPct - 35] || 'w-[48%]';

    return (
        <ResourceDetailShell>
            <ResourceDetailHeader
                title={localData.name}
                backHref={`/pod/${podId}/functions`}
                backLabel="Functions"
                meta={(
                    <div className="flex flex-wrap items-center gap-2">
                        <ResourceVisibilityBadge visibility={localData.visibility} resourceLabel="functions" />
                        <span>Function</span>
                        {canUpdateCurrentFunction
                            ? (hasUnsavedChanges || updateFunction.isPending ? <span className="text-[var(--state-warning)]">Saving edits</span> : <span>Saved</span>)
                            : <span>Read-only</span>}
                    </div>
                )}
                tabs={(
                    <ResourceHeaderTabs
                        value={panelTab}
                        onValueChange={setPanelTab}
                        items={[
                            { value: 'code', label: 'Code' },
                            { value: 'config', label: 'Config' },
                            { value: 'schemas', label: 'Schemas' },
                            { value: 'runs', label: 'Runs' },
                        ]}
                    />
                )}
                actions={(
                    <TooltipProvider>
                    <>
                        {canUpdateCurrentFunction && (hasUnsavedChanges || updateFunction.isPending) ? (
                            <Button
                                type="button"
                                size="sm"
                                onClick={() => void handleSave()}
                                disabled={updateFunction.isPending || !hasUnsavedChanges}
                                className="h-8 gap-1.5 px-3 text-xs font-medium"
                            >
                                {updateFunction.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                                {updateFunction.isPending ? 'Saving...' : 'Save changes'}
                            </Button>
                        ) : null}
                        {canExecuteCurrentFunction ? (
                            <Tooltip>
                                <TooltipTrigger asChild>
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="icon"
                                        onClick={() => setIsTestPanelOpen((prev) => !prev)}
                                        className="h-8 w-8 rounded"
                                        aria-label={isTestPanelOpen ? 'Hide test panel' : 'Show test panel'}
                                    >
                                        <Play className="h-4 w-4" />
                                    </Button>
                                </TooltipTrigger>
                                <TooltipContent>{isTestPanelOpen ? 'Hide test panel' : 'Show test panel'}</TooltipContent>
                            </Tooltip>
                        ) : null}
                        {canUpdateCurrentFunction ? (
                            <ResourceShareButton
                                value={localData.visibility}
                                podId={podId}
                                resourceType="function"
                                resourceId={localData.id}
                                resourceLabel="functions"
                                resourceName={localData.name}
                                shareUrl={functionShareUrl}
                                onChange={handleShareVisibilityChange}
                                trigger={({ openShare, disabled }) => (
                                    <Tooltip>
                                        <TooltipTrigger asChild>
                                            <Button
                                                type="button"
                                                variant="ghost"
                                                size="icon"
                                                className="h-8 w-8 rounded"
                                                onClick={openShare}
                                                disabled={disabled}
                                                aria-label="Share"
                                            >
                                                <Share2 className="h-4 w-4" />
                                            </Button>
                                        </TooltipTrigger>
                                        <TooltipContent>Share</TooltipContent>
                                    </Tooltip>
                                )}
                            />
                        ) : null}
                    </>
                    </TooltipProvider>
                )}
            />
            <ResourceArrivalNotice
                resource="function"
                title="Function created"
                description="Ready to test. Try sample input here, then tighten schemas or use it in a workflow."
                celebrate
                actions={[
                    ...(canUpdateCurrentFunction ? [{ label: 'Edit schemas', onClick: () => setPanelTab('schemas'), variant: 'primary' as const }] : []),
                    ...(canCreateWorkflow ? [{ label: 'Create workflow', href: `/pod/${podId}/flows/new` }] : []),
                ]}
                className="mx-4 mt-3"
            />

            <div
                ref={layoutRef}
                className={`min-h-0 flex-1 ${isResizingSidebar ? 'select-none' : ''}`}
            >
                <ResourceWorkSplit
                    isStacked={isStackedLayout}
                    main={(
                        <FunctionEditor
                            podId={podId}
                            functionData={localData}
                            panelTab={panelTab}
                            onPanelTabChange={setPanelTab}
                            onUpdate={handleUpdate}
                            onSave={() => void handleSave()}
                            isUpdating={updateFunction.isPending}
                            hasUnsavedChanges={hasUnsavedChanges}
                            isTestPanelOpen={canShowTestPanel}
                            onToggleTestPanel={() => setIsTestPanelOpen((prev) => !prev)}
                            onSelectRun={(runId) => {
                                setIsTestPanelOpen(true);
                                setSelectedRunId(runId);
                                setOpenRunRequestKey((prev) => prev + 1);
                            }}
                            hideHeader
                            isNameEditable={false}
                            shareUrl={functionShareUrl}
                            onShareVisibilityChange={handleShareVisibilityChange}
                        />
                    )}
                    separator={(
                        !isStackedLayout ? (
                            <div
                                role="separator"
                                aria-orientation="vertical"
                                className="w-px cursor-col-resize bg-[color:color-mix(in_srgb,var(--border-subtle)_35%,transparent)] hover:bg-[var(--text-tertiary)]"
                                onMouseDown={(event) => {
                                    event.preventDefault();
                                    setIsResizingSidebar(true);
                                }}
                            />
                        ) : null
                    )}
                    aside={canShowTestPanel ? (
                            <FunctionTestPanel
                                podId={podId}
                                functionId={functionId}
                                initialRunId={selectedRunId}
                                openRunRequestKey={openRunRequestKey}
                                onClose={() => setIsTestPanelOpen(false)}
                            />
                    ) : undefined}
                    asideClassName={!isStackedLayout ? `min-w-[520px] ${sidebarWidthClass}` : undefined}
                />
            </div>
        </ResourceDetailShell>
    );
}
