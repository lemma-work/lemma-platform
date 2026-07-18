'use client';

import { use, useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from 'react';
import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import {
    ArrowLeft,
    ChevronDown,
    ImagePlus,
    Loader2,
    Plus,
    Share2,
} from '@/components/ui/icons';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import { toast } from 'sonner';
import { StepLoader } from '@/components/brand/loader';

import { FlowEditor } from '@/components/flows/flow-editor';
import { FlowExecutionPanel } from '@/components/flows/flow-execution-panel';
import {
    ResourceDetailHeader,
    ResourceDetailShell,
    ResourceDetailViewport,
    ResourceTabPane,
} from '@/components/pod/resource-layout';
import { ProductIcon } from '@/components/pod/product-icon';
import { AutomationPane, TriggersSection } from '@/components/pod/resource-automation';
import { ResourceIcon } from '@/components/shared/resource-icon';
import { ResourceArrivalNotice } from '@/components/shared/resource-feedback';
import { ResourceShareButton, ResourceVisibilityBadge, type ResourceVisibilityValue } from '@/components/shared/resource-visibility';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import {
    useFlow,
    useFlows,
    useUpdateFlow,
    useUpdateFlowGraph,
} from '@/lib/hooks/use-flows';
import { resourceAllows } from '@/lib/authz/resource-actions';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { usePodAutomation } from '@/lib/hooks/use-pod-automation';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { FlowDefinition, Workflow, WorkflowUpdateInput } from '@/lib/types';
import { playSoundFeedback } from '@/lib/feedback/sound-feedback';

type WorkflowDetailTab = 'runs' | 'triggers' | 'edit';
type WorkflowEditView = 'steps' | 'flow';

export default function FlowDetailPage({
    params,
}: {
    params: Promise<{ id: string; flowId: string }>;
}) {
    const { id: podId, flowId } = use(params);
    const workflowName = flowId;
    const pathname = usePathname();
    const router = useRouter();
    const searchParams = useSearchParams();
    const podAccess = usePodAccess(podId);
    const canCreateWorkflow = podAccess.can('workflow.create');
    const canUpdateWorkflow = podAccess.can('workflow.update');
    const canUseSchedules = podAccess.canAny(['schedule.read', 'schedule.create']);
    const canCreateSchedule = podAccess.can('schedule.create');
    const canUpdateSchedule = podAccess.can('schedule.update');
    const canDeleteSchedule = podAccess.can('schedule.delete');

    const { data: flowData, isLoading } = useFlow(podId, workflowName);
    const { data: allFlowsData = [] } = useFlows(podId);
    // Pod-wide schedules, grouped client-side — shared cache with the schedules page.
    const automation = usePodAutomation(podId, { schedules: canUseSchedules, surfaces: false });
    const workflowSchedules = automation.schedulesForWorkflow(flowData?.name ?? workflowName);
    const updateFlow = useUpdateFlow();
    const updateFlowGraph = useUpdateFlowGraph();

    const [localDefinition, setLocalDefinition] = useState<FlowDefinition | null>(null);
    const allFlows = useMemo(() => allFlowsData || [], [allFlowsData]);
    const canUpdateCurrentWorkflow = resourceAllows(flowData, 'workflow.update', canUpdateWorkflow);
    const requestedMode = searchParams.get('mode');
    const activeTab: WorkflowDetailTab = requestedMode === 'triggers' && canUseSchedules
        ? 'triggers'
        : requestedMode === 'edit' && canUpdateCurrentWorkflow
            ? 'edit'
            : 'runs';

    const setActiveTab = useCallback((nextTab: WorkflowDetailTab) => {
        if (nextTab === 'edit' && !canUpdateCurrentWorkflow) return;
        if (nextTab === 'triggers' && !canUseSchedules) return;
        const nextParams = new URLSearchParams(searchParams.toString());

        if (nextTab === 'runs') {
            nextParams.delete('mode');
        } else {
            nextParams.set('mode', nextTab);
        }

        const nextQuery = nextParams.toString();
        router.replace(nextQuery ? `${pathname}?${nextQuery}` : pathname, { scroll: false });
    }, [canUpdateCurrentWorkflow, canUseSchedules, pathname, router, searchParams]);

    useEffect(() => {
        if (!flowData) return;

        // eslint-disable-next-line react-hooks/set-state-in-effect
        setLocalDefinition({
            nodes: flowData.nodes || [],
            edges: flowData.edges || [],
            viewport: flowData.viewport || { x: 0, y: 0, zoom: 1 },
        });
    }, [flowData]);

    const handleDefinitionSave = useCallback(async (definition: FlowDefinition) => {
        if (!resourceAllows(flowData, 'workflow.update', canUpdateWorkflow)) return;
        setLocalDefinition(definition);
        try {
            await updateFlowGraph.mutateAsync({
                podId,
                id: workflowName,
                data: {
                    nodes: definition.nodes,
                    edges: definition.edges,
                },
            });
            playSoundFeedback('action-success');
        } catch (error) {
            console.error('Failed to save workflow:', error);
            toast.error(error instanceof Error ? error.message : 'Failed to save workflow. Please try again.');
        }
    }, [canUpdateWorkflow, flowData, podId, setLocalDefinition, updateFlowGraph, workflowName]);

    const handleFlowSettingsSave = useCallback(async (updates: Partial<Workflow>) => {
        if (!flowData) return;
        if (!resourceAllows(flowData, 'workflow.update', canUpdateWorkflow)) return;

        const flowUpdatePayload = {
            description:
                typeof updates.description === 'string' || updates.description === null
                    ? updates.description
                    : undefined,
            icon_url:
                typeof updates.icon_url === 'string' || updates.icon_url === null
                    ? updates.icon_url
                    : undefined,
            mode:
                typeof updates.mode === 'string' || updates.mode === null
                    ? updates.mode
                    : undefined,
            visibility:
                typeof updates.visibility === 'string' || updates.visibility === null
                    ? updates.visibility as WorkflowUpdateInput['visibility']
                    : undefined,
            start: 'start' in updates ? updates.start : undefined,
        };

        if (Object.values(flowUpdatePayload).some((value) => typeof value !== 'undefined')) {
            await updateFlow.mutateAsync({
                podId,
                id: workflowName,
                data: flowUpdatePayload,
            });
        }

    }, [canUpdateWorkflow, flowData, podId, updateFlow, workflowName]);

    const handleShareVisibilityChange = useCallback(async (visibility: ResourceVisibilityValue) => {
        await handleFlowSettingsSave({ visibility });
    }, [handleFlowSettingsSave]);

    if (isLoading) {
        return (
            <div className="flex h-full items-center justify-center bg-transparent">
                <StepLoader size="sm" />
            </div>
        );
    }

    if (!flowData) {
        return (
            <div className="flex h-full items-center justify-center bg-transparent">
                <div className="text-center">
                    <h2 className="font-display text-2xl font-semibold text-[var(--text-primary)]">Workflow not found</h2>
                </div>
            </div>
        );
    }

    const workflowShareUrl = typeof window === 'undefined'
        ? undefined
        : `${window.location.origin}/pod/${podId}/flows/${encodeURIComponent(flowData.name || workflowName)}`;

    const switcher = (
        <DropdownMenu.Root>
            <DropdownMenu.Trigger asChild>
                <button
                    type="button"
                    className="flow-detail-switcher-button lemma-quiet-action lemma-quiet-action-sm custom-focus-ring text-[var(--text-tertiary)]"
                    aria-label="Switch workflow"
                >
                    <ChevronDown className="h-3.5 w-3.5" />
                </button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
                <DropdownMenu.Content
                    align="start"
                    sideOffset={8}
                    className="surface-panel z-50 w-64 p-1 shadow-[var(--shadow-lg)]"
                >
                    <div className="px-2 py-1.5 type-eyebrow-medium">
                        Workflows
                    </div>
                    {allFlows.map((flow) => (
                        <DropdownMenu.Item asChild key={flow.name}>
                            <Link
                                href={`/pod/${podId}/flows/${encodeURIComponent(flow.name)}`}
                                className="lemma-menu-row lemma-menu-row-between"
                            >
                                <span className="truncate">{flow.name}</span>
                                {flow.name === flowData.name ? (
                                    <span className="h-1.5 w-1.5 rounded-full bg-[var(--delight)]" />
                                ) : null}
                            </Link>
                        </DropdownMenu.Item>
                    ))}
                    {canCreateWorkflow ? (
                        <>
                            <DropdownMenu.Separator className="my-1 h-px bg-[var(--border-subtle)]" />
                            <DropdownMenu.Item asChild>
                                <Link
                                    href={`/pod/${podId}/flows/new`}
                                    className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-2 text-sm font-medium text-[var(--delight)] outline-none transition-colors hover:bg-[var(--delight-soft)] hover:text-[var(--text-primary)]"
                                >
                                    <Plus className="h-3.5 w-3.5" />
                                    Add workflow
                                </Link>
                            </DropdownMenu.Item>
                        </>
                    ) : null}
                </DropdownMenu.Content>
            </DropdownMenu.Portal>
        </DropdownMenu.Root>
    );

    return (
        <ResourceDetailShell>
            <ResourceDetailHeader
                title={flowData.name}
                backHref={`/pod/${podId}/flows`}
                backLabel="Workflows"
                switcher={switcher}
                meta={<ResourceVisibilityBadge visibility={flowData.visibility} resourceLabel="workflows" />}
                fullscreen={activeTab === 'edit'}
                tabs={(
                    <WorkflowModeSwitch
                        value={activeTab}
                        onChange={setActiveTab}
                        canEdit={canUpdateCurrentWorkflow}
                        canTriggers={canUseSchedules}
                    />
                )}
                actions={canUpdateCurrentWorkflow ? (
                    <TooltipProvider>
                    <ResourceShareButton
                        value={flowData.visibility}
                        podId={podId}
                        resourceType="workflow"
                        resourceId={flowData.id}
                        resourceLabel="workflows"
                        resourceName={flowData.name}
                        shareUrl={workflowShareUrl}
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
                    </TooltipProvider>
                ) : null}
            />
            <ResourceArrivalNotice
                resource="workflow"
                title="Workflow created"
                description="Start by adding the steps this workflow should follow. Runs will appear here once it has work to do."
                celebrate
                actions={[
                    ...(canUpdateCurrentWorkflow ? [{ label: 'Add steps', onClick: () => setActiveTab('edit'), variant: 'primary' as const }] : []),
                    { label: 'Add schedule', href: `/pod/${podId}/schedules?workflow=${encodeURIComponent(flowData.name)}` },
                ]}
                className="mx-4 mt-3"
            />

            <ResourceDetailViewport>
                <ResourceTabPane active={activeTab === 'edit'}>
                    {activeTab === 'edit' && localDefinition ? (
                        <EditWorkflowPanel
                            flowName={flowData.name}
                            onExit={() => setActiveTab('runs')}
                            definition={localDefinition}
                            onDefinitionChange={handleDefinitionSave}
                            isSavingDefinition={updateFlowGraph.isPending}
                            flow={flowData}
                            podId={podId}
                            onSettingsSave={handleFlowSettingsSave}
                            onShareVisibilityChange={handleShareVisibilityChange}
                        />
                    ) : null}
                </ResourceTabPane>

                <ResourceTabPane active={activeTab === 'triggers'}>
                    {activeTab === 'triggers' ? (
                        <AutomationPane>
                            <TriggersSection
                                podId={podId}
                                target={{ kind: 'workflow', name: flowData.name }}
                                schedules={workflowSchedules}
                                newHref={`/pod/${podId}/schedules/new?workflow=${encodeURIComponent(flowData.name)}`}
                                description="What starts this workflow on its own — a rhythm, an app event, or a data change."
                                canCreate={canCreateSchedule}
                                canUpdate={canUpdateSchedule}
                                canDelete={canDeleteSchedule}
                            />
                        </AutomationPane>
                    ) : null}
                </ResourceTabPane>

                <ResourceTabPane active={activeTab === 'runs'}>
                    {activeTab === 'runs' && <FlowExecutionPanel podId={podId} flowName={workflowName} />}
                </ResourceTabPane>
            </ResourceDetailViewport>
        </ResourceDetailShell>
    );
}

const WORKFLOW_MODE_LABELS: Record<WorkflowDetailTab, string> = {
    runs: 'Runs',
    triggers: 'Triggers',
    edit: 'Edit',
};

function WorkflowModeSwitch({
    value,
    onChange,
    canEdit,
    canTriggers,
}: {
    value: WorkflowDetailTab;
    onChange: (value: WorkflowDetailTab) => void;
    canEdit: boolean;
    canTriggers: boolean;
}) {
    const items: WorkflowDetailTab[] = [
        'runs',
        ...(canTriggers ? ['triggers' as const] : []),
        ...(canEdit ? ['edit' as const] : []),
    ];
    return (
        <div className="segmented-control">
            {items.map((item) => (
                <button
                    key={item}
                    type="button"
                    onClick={() => onChange(item)}
                    className="segmented-control-item custom-focus-ring"
                    data-active={value === item}
                    aria-pressed={value === item}
                >
                    {WORKFLOW_MODE_LABELS[item]}
                </button>
            ))}
        </div>
    );
}

function EditWorkflowPanel({
    flowName,
    onExit,
    definition,
    onDefinitionChange,
    isSavingDefinition,
    flow,
    podId,
    onSettingsSave,
    onShareVisibilityChange,
}: {
    flowName: string;
    onExit: () => void;
    definition: FlowDefinition;
    onDefinitionChange: (definition: FlowDefinition) => Promise<void> | void;
    isSavingDefinition: boolean;
    flow: Workflow;
    podId: string;
    onSettingsSave: (updates: Partial<Workflow>) => Promise<void>;
    onShareVisibilityChange: (visibility: ResourceVisibilityValue) => Promise<void> | void;
}) {
    const [description, setDescription] = useState(flow.description || '');
    const [iconUrl, setIconUrl] = useState<string | null>(flow.icon_url || null);
    const [visibility, setVisibility] = useState(flow.visibility || 'POD');
    const [editorView, setEditorView] = useState<WorkflowEditView>('steps');
    const shareUrl = typeof window === 'undefined'
        ? undefined
        : `${window.location.origin}/pod/${podId}/flows/${encodeURIComponent(flowName)}`;

    useEffect(() => {
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setDescription(flow.description || '');
        setIconUrl(flow.icon_url || null);
        setVisibility(flow.visibility || 'POD');
    }, [flow.description, flow.icon_url, flow.visibility]);

    const saveMetadata = useCallback(async () => {
        const trimmed = description.trim();
        const nextUpdates: Partial<Workflow> = {};

        if (trimmed !== (flow.description || '')) {
            nextUpdates.description = trimmed || null;
        }

        if (iconUrl !== (flow.icon_url || null)) {
            nextUpdates.icon_url = iconUrl;
        }

        if (visibility !== (flow.visibility || 'POD')) {
            nextUpdates.visibility = visibility;
        }

        if (Object.keys(nextUpdates).length === 0) return;
        await onSettingsSave(nextUpdates);
    }, [description, flow.description, flow.icon_url, flow.visibility, iconUrl, onSettingsSave, visibility]);

    const handleDefinitionSave = useCallback(async (nextDefinition: FlowDefinition) => {
        await onDefinitionChange(nextDefinition);
        await saveMetadata();
    }, [onDefinitionChange, saveMetadata]);

    const saveIcon = useCallback((nextIconUrl: string | null) => {
        setIconUrl(nextIconUrl);
    }, []);

    return (
        <div className="flex h-full min-h-0 flex-col bg-[var(--bg-canvas)]">
            <div className="flex h-16 shrink-0 items-center justify-between gap-4 border-b border-[color:color-mix(in_srgb,var(--border-subtle)_52%,transparent)] bg-[color:color-mix(in_srgb,var(--bg-canvas)_88%,transparent)] px-4 backdrop-blur-sm">
                <div className="flex min-w-0 flex-1 items-center gap-3">
                    <button
                        type="button"
                        onClick={onExit}
                        className="lemma-card-icon-control custom-focus-ring h-8 w-8 shrink-0"
                        aria-label="Back to runs"
                    >
                        <ArrowLeft className="h-4 w-4" />
                    </button>
                    <HeaderIconEditor
                        name={flowName}
                        iconUrl={iconUrl}
                        onChange={saveIcon}
                    />
                    <div className="min-w-0 flex-1">
                        <div className="flex min-w-0 items-center gap-2">
                            <h1 className="truncate text-lg font-semibold tracking-normal text-[var(--text-primary)]">{flowName}</h1>
                            <ResourceVisibilityBadge visibility={visibility} resourceLabel="workflows" />
                        </div>
                        <input
                            value={description}
                            className="inline-edit-field mt-0.5 block h-5 w-full truncate bg-transparent text-sm text-[var(--text-tertiary)] outline-none placeholder:text-[var(--text-tertiary)]"
                            onChange={(event) => setDescription(event.target.value)}
                            onKeyDown={(event) => {
                                if (event.key === 'Enter') {
                                    event.preventDefault();
                                }
                            }}
                            placeholder="Add a one-line workflow description"
                        />
                    </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                    <ResourceShareButton
                        value={visibility}
                        podId={podId}
                        resourceType="workflow"
                        resourceId={flow.id}
                        resourceLabel="workflows"
                        resourceName={flowName}
                        shareUrl={shareUrl}
                        onChange={async (nextVisibility) => {
                            await onShareVisibilityChange(nextVisibility);
                            setVisibility(nextVisibility);
                        }}
                        trigger={({ openShare, disabled }) => (
                            <Button
                                type="button"
                                variant="secondary"
                                size="sm"
                                className="h-8 gap-1.5 px-3 text-xs font-medium"
                                onClick={openShare}
                                disabled={disabled}
                            >
                                <Share2 className="h-3.5 w-3.5" />
                                Share
                            </Button>
                        )}
                    />
                    <div className="segmented-control" data-edu="flow-view-toggle">
                        {(['steps', 'flow'] as WorkflowEditView[]).map((mode) => (
                            <button
                                key={mode}
                                type="button"
                                className="segmented-control-item custom-focus-ring"
                                data-active={editorView === mode}
                                aria-pressed={editorView === mode}
                                onClick={() => setEditorView(mode)}
                            >
                                {mode === 'steps' ? 'Steps' : 'Flow'}
                            </button>
                        ))}
                    </div>
                </div>
            </div>

            <div className="min-h-0 flex-1">
                <FlowEditor
                    initialDefinition={definition}
                    flowStart={flow.start || undefined}
                    onStartSave={(start) => onSettingsSave({ start })}
                    viewMode={editorView}
                    onViewModeChange={setEditorView}
                    onSave={handleDefinitionSave}
                    isSaving={isSavingDefinition}
                    podId={podId}
                />
            </div>
        </div>
    );
}

function HeaderIconEditor({
    name,
    iconUrl,
    onChange,
}: {
    name: string;
    iconUrl: string | null;
    onChange: (iconUrl: string | null) => void;
}) {
    const inputRef = useRef<HTMLInputElement>(null);
    const [isUploading, setIsUploading] = useState(false);

    const handleFileSelection = async (event: ChangeEvent<HTMLInputElement>) => {
        const file = event.target.files?.[0];
        if (!file) return;

        setIsUploading(true);
        try {
            const response = await getLemmaClient().icons.upload(file);
            onChange(response.icon_url);
        } catch (error) {
            console.error('Failed to upload workflow icon:', error);
            toast.error('Failed to upload icon');
        } finally {
            setIsUploading(false);
            if (inputRef.current) inputRef.current.value = '';
        }
    };

    return (
        <button
            type="button"
            className="flow-detail-icon-upload-button group relative shrink-0"
            onClick={() => inputRef.current?.click()}
            aria-label="Change workflow icon"
            title="Change workflow icon"
            disabled={isUploading}
        >
            <input
                ref={inputRef}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={handleFileSelection}
            />
            <ResourceIcon
                iconUrl={iconUrl}
                alt={`${name} icon`}
                label={name}
                className="h-10 w-10 rounded-lg !border-0 !bg-transparent"
                fallback={<ProductIcon kind="workflows" size="lg" />}
            />
            <span className="absolute -bottom-1 -right-1 inline-flex h-4 w-4 items-center justify-center rounded-full border border-[var(--row-border)] bg-[var(--card-bg)] text-[var(--text-tertiary)] shadow-[var(--shadow-xs)]">
                {isUploading ? <Loader2 className="h-2.5 w-2.5 animate-spin" /> : <ImagePlus className="h-2.5 w-2.5" />}
            </span>
        </button>
    );
}
