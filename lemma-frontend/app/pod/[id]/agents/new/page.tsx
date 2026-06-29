'use client';

import { use, useMemo, useState, type ReactNode } from 'react';
import { useRouter } from 'next/navigation';
import {
    ArrowLeft,
    ArrowRight,
    Bot,
    Box,
    Braces,
    CheckCircle2,
    ClipboardList,
    Database,
    FileOutput,
    Folder,
    Image as ImageIcon,
    MessageSquare,
    Music,
    Plus,
    Search,
    UserRound,
    Wand2,
} from 'lucide-react';
import { toast } from 'sonner';

import { SchemaBuilder } from '@/components/agents/schema-builder';
import { AgentAvatarPicker } from '@/components/agents/agent-avatar-picker';
import { formatAgentRuntime, resolveDefaultAgentRuntime } from '@/components/agents/agent-runtime-helpers';
import { RuntimeModelPicker } from '@/components/lemma/assistant/model-picker';
import { PodPageHeader } from '@/components/pod/pod-page-header';
import { ConnectorsSelector, DatastoresSelector, FoldersSelector } from '@/components/pod/resource-selectors';
import { ResourceIcon } from '@/components/shared/resource-icon';
import { ResourceVisibilityBadge, ResourceVisibilitySelect } from '@/components/shared/resource-visibility';
import { showResourceCreatedToast, showResourceErrorToast } from '@/components/shared/resource-feedback';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { useAgentRuntimes, useAvailableAgentRuntimeHarnesses } from '@/lib/hooks/use-agent-runtime';
import { useCreateAgent } from '@/lib/hooks/use-agents';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { usePod } from '@/lib/hooks/use-pods';
import { AGENT_MASCOTS } from '@/lib/data/agent-mascots';
import { cn } from '@/lib/utils';
import { Agent, TableAccessMode, ToolSet } from '@/lib/types';

type SchemaProperty = Record<string, unknown>;
type SchemaFieldEntry = [string, SchemaProperty];
type BuilderStepId = 'identity' | 'instructions' | 'shape' | 'access' | 'review';

const BUILDER_STEPS: Array<{
    id: BuilderStepId;
    label: string;
    eyebrow: string;
    description: string;
}> = [
    {
        id: 'identity',
        label: 'Name the agent',
        eyebrow: 'Identity',
        description: 'Give this teammate a clear name and purpose.',
    },
    {
        id: 'instructions',
        label: 'Define the job',
        eyebrow: 'Instructions',
        description: 'Write the operating brief before adding tools.',
    },
    {
        id: 'shape',
        label: 'How do you want to use it?',
        eyebrow: 'Experience',
        description: 'Choose the interaction shape.',
    },
    {
        id: 'access',
        label: 'Give it context',
        eyebrow: 'Context',
        description: 'Pick only what this agent needs.',
    },
    {
        id: 'review',
        label: 'Review and create',
        eyebrow: 'Launch',
        description: 'Check the agent before it joins the pod.',
    },
];

const INSTRUCTION_STARTERS = [
    'Read the request, understand what the person needs, summarize the important context, draft a useful response, and ask a follow-up question when information is missing.',
    'Research the requested company or account using only available pod sources. Return a concise brief with useful context, risks, opportunities, and unknowns.',
    'Turn rough context into a polished draft. Preserve the user intent, keep the tone practical, and flag assumptions clearly.',
] as const;

const TOOL_META: Record<string, { label: string; icon: ReactNode }> = {
    POD: { label: 'Pod data', icon: <Folder className="h-3.5 w-3.5" /> },
    WORKSPACE_CLI: { label: 'Workspace CLI', icon: <Wand2 className="h-3.5 w-3.5" /> },
    SKILLS: { label: 'Skills', icon: <Box className="h-3.5 w-3.5" /> },
    WEB_SEARCH: { label: 'Web search', icon: <Search className="h-3.5 w-3.5" /> },
    USER_INTERACTION: { label: 'User interaction', icon: <UserRound className="h-3.5 w-3.5" /> },
    IMAGE_GENERATION: { label: 'Image generation', icon: <ImageIcon className="h-3.5 w-3.5" /> },
    AUDIO_GENERATION: { label: 'Audio generation', icon: <Music className="h-3.5 w-3.5" /> },
};

function isRecord(value: unknown): value is Record<string, unknown> {
    return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function getSchemaFields(schema: unknown): SchemaFieldEntry[] {
    if (!isRecord(schema) || !isRecord(schema.properties)) return [];
    return Object.entries(schema.properties).filter((entry): entry is SchemaFieldEntry => isRecord(entry[1]));
}

function schemaOrNull(schema: unknown): Record<string, unknown> | null {
    return getSchemaFields(schema).length > 0 ? schema as Record<string, unknown> : null;
}

export default function NewAgentPage({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    const { id: podId } = use(params);
    const router = useRouter();
    const podAccess = usePodAccess(podId);
    const createAgent = useCreateAgent();
    const { data: pod } = usePod(podId);
    const { data: runtimeCatalog } = useAgentRuntimes(pod?.organization_id);
    const { data: availableHarnesses } = useAvailableAgentRuntimeHarnesses();
    const defaultRuntime = resolveDefaultAgentRuntime(runtimeCatalog, pod?.config?.default_profile_id, availableHarnesses);
    const [currentStep, setCurrentStep] = useState<BuilderStepId>('identity');
    const [showTaskFields, setShowTaskFields] = useState(false);
    const [showOutputFields, setShowOutputFields] = useState(false);

    const [draftAgent, setDraftAgent] = useState<Agent>({
        id: 'draft',
        pod_id: podId,
        user_id: 'current-user',
        name: '',
        description: '',
        icon_url: AGENT_MASCOTS[0]?.src || null,
        agent_runtime: null,
        instruction: '',
        input_schema: {},
        output_schema: {},
        tool_sets: [],
        accessible_tables: [],
        accessible_folders: [],
        accessible_connectors: [],
        visibility: 'POD',
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
    } as Agent);

    const inputFields = useMemo(() => getSchemaFields(draftAgent.input_schema), [draftAgent.input_schema]);
    const outputFields = useMemo(() => getSchemaFields(draftAgent.output_schema), [draftAgent.output_schema]);
    const hasSchema = inputFields.length > 0 || outputFields.length > 0;
    const isConversational = !hasSchema;
    const taskFieldsVisible = showTaskFields || inputFields.length > 0;
    const outputFieldsVisible = showOutputFields || outputFields.length > 0;
    const currentStepIndex = BUILDER_STEPS.findIndex((step) => step.id === currentStep);
    const safeStepIndex = currentStepIndex >= 0 ? currentStepIndex : 0;
    const activeStep = BUILDER_STEPS[safeStepIndex];
    const isFinalStep = safeStepIndex === BUILDER_STEPS.length - 1;
    const hasName = Boolean(draftAgent.name.trim());
    const hasInstructions = Boolean(draftAgent.instruction.trim());
    const stepDone: Record<BuilderStepId, boolean> = {
        identity: hasName,
        instructions: hasInstructions,
        shape: safeStepIndex > 2,
        access: safeStepIndex > 3,
        review: false,
    };
    const canGoNext = currentStep !== 'identity' || hasName;
    const nextStep = BUILDER_STEPS[Math.min(safeStepIndex + 1, BUILDER_STEPS.length - 1)];

    const updateDraft = (updates: Partial<Agent>) => {
        setDraftAgent((prev) => ({ ...prev, ...updates }));
    };

    const goToNextStep = () => {
        if (!canGoNext) {
            toast.error('Name the agent first');
            return;
        }
        setCurrentStep(BUILDER_STEPS[Math.min(safeStepIndex + 1, BUILDER_STEPS.length - 1)].id);
    };

    const goToPreviousStep = () => {
        setCurrentStep(BUILDER_STEPS[Math.max(safeStepIndex - 1, 0)].id);
    };

    const handleCreate = async () => {
        if (!podAccess.can('agent.create')) return;
        if (!draftAgent.name.trim()) {
            toast.error('Please name the agent first');
            return;
        }

        try {
            const newAgent = await createAgent.mutateAsync({
                podId,
                data: {
                    name: draftAgent.name.trim(),
                    description: draftAgent.description || null,
                    icon_url: draftAgent.icon_url || undefined,
                    agent_runtime: draftAgent.agent_runtime ?? null,
                    instruction: draftAgent.instruction || defaultInstruction(draftAgent.name, draftAgent.description),
                    input_schema: schemaOrNull(draftAgent.input_schema),
                    output_schema: schemaOrNull(draftAgent.output_schema),
                    tool_sets: draftAgent.tool_sets,
                    accessible_tables: draftAgent.accessible_tables,
                    accessible_folders: draftAgent.accessible_folders,
                    accessible_connectors: draftAgent.accessible_connectors,
                    visibility: draftAgent.visibility as never,
                },
            });

            showResourceCreatedToast('Agent', newAgent.name);
            router.push(`/pod/${podId}/agents/${encodeURIComponent(newAgent.name)}?created=agent`);
        } catch (error) {
            console.error('Failed to create agent:', error);
            showResourceErrorToast(error, 'Failed to create agent');
        }
    };

    if (!podAccess.isLoading && !podAccess.can('agent.create')) {
        return (
            <div className="flex h-full items-center justify-center bg-transparent px-4">
                <div className="surface-panel max-w-lg p-6 text-center sm:p-8">
                    <h2 className="mb-2 font-display text-xl font-semibold text-[var(--text-primary)]">No access to create agents</h2>
                    <p className="text-sm text-[var(--text-secondary)]">You can use the agent area, but creating agents is outside your current permissions.</p>
                </div>
            </div>
        );
    }

    return (
        <div className="agent-builder-root flex h-full min-h-0 flex-col">
            <PodPageHeader
                podId={podId}
                variant="bar"
                title="Create agent"
                eyebrow="Guided builder"
                backHref={`/pod/${podId}/ai`}
                backLabel="Agents"
                productIconTone="agents"
                meta={(
                    <span className="text-xs text-[var(--text-secondary)]">
                        {hasName ? 'Ready to create' : 'Draft'} · {draftAgent.tool_sets.length} tools
                    </span>
                )}
            />

            <main className="min-h-0 flex-1 overflow-y-auto">
                <div className="agent-builder-canvas mx-auto w-full max-w-[76rem] px-6 pb-24 pt-6">
                    <section className="agent-builder-hero">
                        <div className="min-w-0">
                            <p className="section-label">{activeStep.eyebrow}</p>
                            <h1 className="agent-builder-title mt-2">{activeStep.label}</h1>
                            <p className="mt-2 max-w-2xl text-sm leading-6 text-[var(--text-secondary)]">{activeStep.description}</p>
                        </div>
                        <div className="hidden text-right text-sm text-[var(--text-secondary)] sm:block">
                            <span className="type-eyebrow">Draft</span>
                            <span className="ml-2 text-[var(--text-primary)]">{draftAgent.name.trim() || 'Unnamed agent'}</span>
                        </div>
                    </section>

                    <CompactStepProgress
                        steps={BUILDER_STEPS}
                        currentStep={currentStep}
                        stepDone={stepDone}
                        onSelect={(stepId) => setCurrentStep(stepId)}
                    />

                    <section className="agent-builder-stage" data-step={currentStep}>
                        {currentStep === 'identity' ? (
                            <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_20rem]">
                                <div className="space-y-6">
                                    <div>
                                        <label className="text-sm font-medium text-[var(--text-secondary)]">
                                            Agent name
                                        </label>
                                        <input
                                            value={draftAgent.name}
                                            onChange={(event) => updateDraft({ name: event.target.value })}
                                            placeholder="Customer Thread Briefing"
                                            className="form-field-control mt-2 h-11 w-full px-3 text-base font-medium tracking-normal text-[var(--text-primary)] outline-none placeholder:text-[var(--text-tertiary)]"
                                        />
                                    </div>
                                    <div>
                                        <label className="text-sm font-medium text-[var(--text-secondary)]">
                                            One-line purpose
                                        </label>
                                        <textarea
                                            value={draftAgent.description || ''}
                                            onChange={(event) => updateDraft({ description: event.target.value.slice(0, 200) })}
                                            placeholder="What should this agent help the pod do?"
                                            className="form-field-control mt-2 min-h-32 w-full resize-y px-3 py-2.5 text-sm leading-6 text-[var(--text-secondary)] outline-none placeholder:text-[var(--text-tertiary)]"
                                        />
                                    </div>
                                </div>
                                <div className="agent-builder-side-section">
                                    <AgentAvatarPicker
                                        name={draftAgent.name || 'Agent'}
                                        value={draftAgent.icon_url}
                                        onChange={(iconUrl) => updateDraft({ icon_url: iconUrl || undefined })}
                                        compact
                                    />
                                </div>
                            </div>
                        ) : null}

                        {currentStep === 'instructions' ? (
                            <div>
                                <div className="flex flex-wrap items-center justify-between gap-3">
                                    <p className="max-w-2xl text-sm leading-6 text-[var(--text-secondary)]">
                                        Write the operating brief in plain language. A focused job description matters more than a long list of tools.
                                    </p>
                                    <div className="flex flex-wrap gap-2">
                                        {INSTRUCTION_STARTERS.map((starter, index) => (
                                            <button
                                                key={starter}
                                                type="button"
                                                onClick={() => updateDraft({ instruction: draftAgent.instruction || starter })}
                                                className="choice-chip choice-chip-sm"
                                            >
                                                Example {index + 1}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                                <textarea
                                    value={draftAgent.instruction}
                                    onChange={(event) => updateDraft({ instruction: event.target.value })}
                                    placeholder="Tell the agent what it does, what information it can use, what to avoid, when to ask a follow-up question, and what good work looks like..."
                                    className="form-field-control mt-4 min-h-[20rem] w-full resize-y px-5 py-4 text-base leading-7 text-[var(--text-primary)] outline-none placeholder:text-[var(--text-tertiary)]"
                                />
                            </div>
                        ) : null}

                        {currentStep === 'shape' ? (
                            <div className="space-y-8">
                                <div className="agent-builder-section">
                                    <div className="settings-title-row mb-3">
                                        <Bot className="h-4 w-4 text-[var(--text-tertiary)]" />
                                        <h3 className="settings-title text-sm">Model</h3>
                                    </div>
                                    <RuntimeModelPicker
                                        catalog={runtimeCatalog}
                                        availableHarnesses={availableHarnesses}
                                        defaultRuntime={defaultRuntime}
                                        value={draftAgent.agent_runtime ?? null}
                                        onChange={(agentRuntime) => updateDraft({ agent_runtime: agentRuntime })}
                                        scopeHint="Default for this agent"
                                        manageHref={pod?.organization_id ? `/organizations/${pod.organization_id}/settings/agent-runtimes` : undefined}
                                    />
                                </div>

                                <div className="grid gap-3 md:grid-cols-2">
                                    <ModeChoice
                                        active={isConversational}
                                        icon={<MessageSquare className="h-5 w-5" />}
                                        title="Chat with it"
                                        text="Open-ended help and drafting."
                                        onClick={() => {
                                            setShowTaskFields(false);
                                            setShowOutputFields(false);
                                            updateDraft({ input_schema: {}, output_schema: {} });
                                        }}
                                    />
                                    <ModeChoice
                                        active={!isConversational || showTaskFields || showOutputFields}
                                        icon={<Braces className="h-5 w-5" />}
                                        title="Run a structured task"
                                        text="Guided input, predictable output."
                                        onClick={() => setShowTaskFields(true)}
                                    />
                                </div>

                                {isConversational && !showTaskFields && !showOutputFields ? (
                                    <ChatModeSummary />
                                ) : (
                                    <div className="grid gap-5 md:grid-cols-2">
                                        <SchemaMiniPanel
                                            icon={<ClipboardList className="h-4 w-4" />}
                                            title="People give it"
                                            description="Input fields."
                                            isOpen={taskFieldsVisible}
                                            actionLabel={inputFields.length > 0 ? 'Edit fields' : 'Add fields'}
                                            onOpen={() => setShowTaskFields(true)}
                                        >
                                            <SchemaBuilder
                                                value={draftAgent.input_schema || {}}
                                                onChange={(schema) => updateDraft({ input_schema: schema })}
                                            />
                                        </SchemaMiniPanel>
                                        <SchemaMiniPanel
                                            icon={<FileOutput className="h-4 w-4" />}
                                            title="It gives back"
                                            description="Structured result."
                                            isOpen={outputFieldsVisible}
                                            actionLabel={outputFields.length > 0 ? 'Edit output' : 'Add output'}
                                            onOpen={() => setShowOutputFields(true)}
                                        >
                                            <SchemaBuilder
                                                value={draftAgent.output_schema || {}}
                                                onChange={(schema) => updateDraft({ output_schema: schema })}
                                            />
                                        </SchemaMiniPanel>
                                    </div>
                                )}
                            </div>
                        ) : null}

                        {currentStep === 'access' ? (
                            <div className="space-y-8">
                                <div className="agent-builder-section">
                                    <ResourceVisibilitySelect
                                        value={draftAgent.visibility}
                                        resourceLabel="agents"
                                        resourceName={draftAgent.name || 'New agent'}
                                        onChange={(visibility) => updateDraft({ visibility })}
                                    />
                                </div>

                                <div className="agent-builder-section">
                                    <div className="inspector-section-header mb-3">
                                        <div className="settings-title-row">
                                            <Box className="h-4 w-4 text-[var(--text-tertiary)]" />
                                            <h3 className="inspector-section-title">Tools</h3>
                                        </div>
                                        <span className="inspector-section-meta">{draftAgent.tool_sets.length} selected</span>
                                    </div>
                                    <div className="agent-builder-permission-strip">
                                        {Object.values(ToolSet).map((tool) => {
                                            const meta = TOOL_META[tool] ?? { label: tool.replace(/_/g, ' '), icon: <Box className="h-3.5 w-3.5" /> };
                                            const isSelected = (draftAgent.tool_sets || []).includes(tool);
                                            return (
                                                <label
                                                    key={tool}
                                                    className="agent-builder-permission-option"
                                                    data-selected={isSelected}
                                                >
                                                    <Checkbox
                                                        checked={isSelected}
                                                        onCheckedChange={(checked) => {
                                                            const current = draftAgent.tool_sets || [];
                                                            updateDraft({
                                                                tool_sets: checked
                                                                    ? [...current, tool]
                                                                    : current.filter((entry) => entry !== tool),
                                                            });
                                                        }}
                                                        className="h-3.5 w-3.5"
                                                    />
                                                    <span className="shrink-0 text-[var(--text-tertiary)]">{meta.icon}</span>
                                                    <span className="truncate">{meta.label}</span>
                                                </label>
                                            );
                                        })}
                                    </div>
                                </div>

                                <div className="agent-builder-access-grid">
                                    <AccessBlock icon={<Database className="h-4 w-4" />} title="Tables">
                                        <DatastoresSelector
                                            podId={podId}
                                            selected={(draftAgent.accessible_tables || []).map((entry) => entry.table_name)}
                                            modeByName={Object.fromEntries(
                                                (draftAgent.accessible_tables || []).map((entry) => [entry.table_name, entry.mode])
                                            )}
                                            onChange={(names) => {
                                                const modeByTable = new Map(
                                                    (draftAgent.accessible_tables || []).map((entry) => [entry.table_name, entry.mode])
                                                );
                                                updateDraft({
                                                    accessible_tables: names.map((table_name) => ({
                                                        table_name,
                                                        mode: modeByTable.get(table_name) ?? TableAccessMode.READ,
                                                    })),
                                                });
                                            }}
                                            onModeChange={(name, mode) => {
                                                updateDraft({
                                                    accessible_tables: (draftAgent.accessible_tables || []).map((entry) =>
                                                        entry.table_name === name ? { ...entry, mode } : entry
                                                    ),
                                                });
                                            }}
                                            showLabel={false}
                                        />
                                    </AccessBlock>
                                    <AccessBlock icon={<Folder className="h-4 w-4" />} title="Folders">
                                        <FoldersSelector
                                            podId={podId}
                                            selected={draftAgent.accessible_folders || []}
                                            onChange={(folderIds) => updateDraft({ accessible_folders: folderIds })}
                                            showLabel={false}
                                        />
                                    </AccessBlock>
                                    <AccessBlock icon={<Wand2 className="h-4 w-4" />} title="Connectors">
                                        <ConnectorsSelector
                                            podId={podId}
                                            selected={draftAgent.accessible_connectors || []}
                                            onChange={(configs) => updateDraft({ accessible_connectors: configs })}
                                            showLabel={false}
                                        />
                                    </AccessBlock>
                                </div>
                            </div>
                        ) : null}

                        {currentStep === 'review' ? (
                            <LaunchReview
                                name={draftAgent.name}
                                iconUrl={draftAgent.icon_url}
                                description={draftAgent.description}
                                hasName={hasName}
                                hasInstructions={hasInstructions}
                                instruction={draftAgent.instruction}
                                isConversational={isConversational}
                                inputFieldsCount={inputFields.length}
                                outputFieldsCount={outputFields.length}
                                toolsCount={draftAgent.tool_sets.length}
                                tablesCount={draftAgent.accessible_tables?.length || 0}
                                foldersCount={draftAgent.accessible_folders?.length || 0}
                                appsCount={draftAgent.accessible_connectors?.length || 0}
                                runtimeLabel={formatAgentRuntime(draftAgent.agent_runtime ?? defaultRuntime, runtimeCatalog)}
                                visibility={draftAgent.visibility}
                            />
                        ) : null}

                    </section>
                </div>
            </main>

            <footer className="shrink-0 bg-[color:color-mix(in_srgb,var(--surface-1)_78%,transparent)] px-5 py-2.5 backdrop-blur-md">
                <div className="mx-auto flex w-full max-w-[76rem] items-center justify-between gap-3">
                    <div className="min-w-0">
                        <p className="type-eyebrow">
                            Step {safeStepIndex + 1} of {BUILDER_STEPS.length}
                        </p>
                        <p className="truncate text-sm font-medium text-[var(--text-primary)]">
                            {activeStep.label}
                            {!isFinalStep ? <span className="font-normal text-[var(--text-tertiary)]"> · Next: {nextStep.label}</span> : null}
                        </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                        <Button
                            type="button"
                            variant="outline"
                            onClick={goToPreviousStep}
                            disabled={safeStepIndex === 0 || createAgent.isPending}
                            className="gap-2"
                        >
                            <ArrowLeft className="h-3.5 w-3.5" />
                            Previous
                        </Button>
                        {isFinalStep ? (
                            <Button
                                type="button"
                                onClick={handleCreate}
                                disabled={createAgent.isPending || !hasName}
                                className="gap-2"
                            >
                                <Plus className="h-4 w-4" />
                                {createAgent.isPending ? 'Creating...' : 'Create agent'}
                            </Button>
                        ) : (
                            <Button
                                type="button"
                                onClick={goToNextStep}
                                disabled={createAgent.isPending || !canGoNext}
                                className="gap-2"
                            >
                                Next
                                <ArrowRight className="h-3.5 w-3.5" />
                            </Button>
                        )}
                    </div>
                </div>
            </footer>
        </div>
    );
}

function defaultInstruction(name: string, description?: string | null) {
    const subject = name.trim() || 'this agent';
    const purpose = description?.trim();
    return purpose
        ? `You are ${subject}. ${purpose} Be clear, useful, and stay within the pod context and granted tools.`
        : `You are ${subject}. Help the user with the task they bring to you. Be clear, useful, and stay within the pod context and granted tools.`;
}

function CompactStepProgress({
    steps,
    currentStep,
    stepDone,
    onSelect,
}: {
    steps: typeof BUILDER_STEPS;
    currentStep: BuilderStepId;
    stepDone: Record<BuilderStepId, boolean>;
    onSelect: (stepId: BuilderStepId) => void;
}) {
    const currentIndex = Math.max(0, steps.findIndex((step) => step.id === currentStep));
    return (
        <nav className="agent-builder-step-strip" data-progress={currentIndex + 1}>
            <div className="flex items-center justify-between gap-3 sm:hidden">
                <div className="type-eyebrow">
                    Progress
                </div>
                <div className="text-xs font-medium text-[var(--text-secondary)]">
                    {currentIndex + 1}/{steps.length}
                </div>
            </div>
            <div className="flex gap-1.5 overflow-x-auto">
                {steps.map((step, index) => {
                    const active = step.id === currentStep;
                    const done = stepDone[step.id] && !active;
                    return (
                        <button
                            key={step.id}
                            type="button"
                            onClick={() => onSelect(step.id)}
                            className={cn(
                                'agent-builder-step-button flex h-8 shrink-0 items-center gap-2 rounded-md px-2 text-xs transition-colors',
                                active ? 'agent-builder-step-button-active' : ''
                            )}
                        >
                            <span className={cn(
                                'agent-builder-step-index flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs font-medium',
                                done
                                    ? 'agent-builder-step-index-done'
                                    : active
                                    ? 'agent-builder-step-index-active'
                                    : ''
                            )}>
                                {done ? <CheckCircle2 className="h-3.5 w-3.5" /> : index + 1}
                            </span>
                            <span className={cn(
                                'min-w-0 truncate font-medium',
                                active ? 'text-[var(--text-primary)]' : 'text-[var(--text-secondary)]'
                            )}>
                                {step.label}
                            </span>
                        </button>
                    );
                })}
            </div>
            <div className="agent-builder-progress-track" aria-hidden="true">
                <span className="agent-builder-progress-fill" />
            </div>
        </nav>
    );
}

function ModeChoice({
    active,
    icon,
    title,
    text,
    onClick,
}: {
    active: boolean;
    icon: ReactNode;
    title: string;
    text: string;
    onClick: () => void;
}) {
    return (
        <button
            type="button"
            onClick={onClick}
            className={cn(
                'agent-builder-choice-button flex min-h-24 items-start gap-3 rounded-lg p-3.5 transition-gentle',
                active ? 'agent-builder-choice-button-active' : ''
            )}
        >
            <span className={cn(
                'flex h-10 w-10 shrink-0 items-center justify-center rounded-md',
                active ? 'bg-[var(--action-primary-soft)] text-[var(--action-primary)]' : 'text-[var(--text-secondary)]'
            )}>
                {icon}
            </span>
            <span>
                <span className="block text-base font-medium text-[var(--text-primary)]">{title}</span>
                <span className="mt-2 block text-sm leading-6 text-[var(--text-secondary)]">{text}</span>
            </span>
        </button>
    );
}

function AccessBlock({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
    return (
        <section className="agent-builder-access-card">
            <div className="settings-title-row mb-3">
                <span className="text-[var(--text-tertiary)]">{icon}</span>
                <h3 className="settings-title text-sm">{title}</h3>
            </div>
            {children}
        </section>
    );
}

function ChatModeSummary() {
    return (
        <div className="agent-builder-note">
            <div className="flex items-start gap-3">
                <MessageSquare className="mt-0.5 h-4 w-4 shrink-0 text-[var(--state-success)]" />
                <div>
                    <p className="text-sm font-medium text-[var(--text-primary)]">No form needed</p>
                    <p className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">
                        People will just message this agent. Add fields only if you want a repeatable run form later.
                    </p>
                </div>
            </div>
        </div>
    );
}

function LaunchReview({
    name,
    iconUrl,
    description,
    hasName,
    hasInstructions,
    instruction,
    isConversational,
    inputFieldsCount,
    outputFieldsCount,
    toolsCount,
    tablesCount,
    foldersCount,
    appsCount,
    runtimeLabel,
    visibility,
}: {
    name: string;
    iconUrl?: string | null;
    description?: string | null;
    hasName: boolean;
    hasInstructions: boolean;
    instruction: string;
    isConversational: boolean;
    inputFieldsCount: number;
    outputFieldsCount: number;
    toolsCount: number;
    tablesCount: number;
    foldersCount: number;
    appsCount: number;
    runtimeLabel: string;
    visibility?: string | null;
}) {
    const accessCount = toolsCount + tablesCount + foldersCount + appsCount;
    const displayName = name.trim() || 'Untitled agent';
    const purpose = description?.trim() || 'No one-line purpose yet.';
    const brief = hasInstructions
        ? instruction.trim()
        : 'A default brief will be created from the name and purpose.';
    const capabilities = [
        isConversational ? 'People can chat with it' : 'People can run it as a structured task',
        !isConversational && inputFieldsCount > 0 ? `${inputFieldsCount} input field${inputFieldsCount === 1 ? '' : 's'}` : null,
        !isConversational && outputFieldsCount > 0 ? `${outputFieldsCount} output field${outputFieldsCount === 1 ? '' : 's'}` : null,
        runtimeLabel,
        accessCount > 0 ? `${accessCount} tool/context item${accessCount === 1 ? '' : 's'} linked` : 'No extra access yet',
    ].filter(Boolean) as string[];

    return (
        <div className="max-w-4xl space-y-8">
            <section className="agent-builder-section">
                <div className="flex flex-col gap-5 sm:flex-row sm:items-start">
                    <ResourceIcon
                        iconUrl={iconUrl}
                        alt={`${displayName} profile picture`}
                        label={displayName}
                        imageClassName="object-contain p-1"
                        className="h-20 w-20 shrink-0 !border-0 !bg-transparent"
                        fallback={<Bot className="h-7 w-7 text-[var(--text-tertiary)]" />}
                    />
                    <div className="min-w-0 flex-1">
                        <p className="section-label">Agent</p>
                        <div className="mt-2 flex flex-wrap items-center gap-2">
                            <h3 className="truncate text-2xl font-medium text-[var(--text-primary)]">{displayName}</h3>
                            {!hasName ? (
                                <span className="state-badge-error rounded-md px-2 py-0.5 text-xs font-medium">
                                    Name missing
                                </span>
                            ) : null}
                        </div>
                        <p className="mt-2 max-w-2xl text-sm leading-6 text-[var(--text-secondary)]">{purpose}</p>

                        <div className="mt-4 flex flex-wrap gap-2">
                            <ResourceVisibilityBadge visibility={visibility} resourceLabel="agents" />
                            {capabilities.map((capability) => (
                                <span key={capability} className="chip chip-sm chip-quiet">
                                    {capability}
                                </span>
                            ))}
                        </div>
                    </div>
                </div>
            </section>

            <section className="agent-builder-section">
                <div className="flex items-start gap-3">
                    <ClipboardList className="mt-1 h-4 w-4 shrink-0 text-[var(--text-tertiary)]" />
                    <div className="min-w-0">
                        <p className="text-sm font-medium text-[var(--text-primary)]">
                            {hasInstructions ? 'Operating brief' : 'Default operating brief'}
                        </p>
                        <p className="mt-1 line-clamp-5 text-sm leading-6 text-[var(--text-secondary)]">
                            {brief}
                        </p>
                    </div>
                </div>
            </section>
        </div>
    );
}

function SchemaMiniPanel({
    icon,
    title,
    description,
    actionLabel,
    isOpen,
    onOpen,
    children,
}: {
    icon: ReactNode;
    title: string;
    description: string;
    actionLabel: string;
    isOpen: boolean;
    onOpen: () => void;
    children: ReactNode;
}) {
    return (
        <div className="agent-builder-section">
            <div className="flex gap-2">
                <span className="mt-0.5 shrink-0 text-[var(--text-secondary)]">{icon}</span>
                <div className="min-w-0 flex-1">
                    <h3 className="text-sm font-medium text-[var(--text-primary)]">{title}</h3>
                    <p className="mt-0.5 text-xs leading-5 text-[var(--text-secondary)]">{description}</p>
                </div>
            </div>
            {isOpen ? (
                <div className="mt-3">{children}</div>
            ) : (
                <Button type="button" variant="outline" size="sm" onClick={onOpen} className="mt-3 h-8 w-full gap-2 border-dashed text-xs">
                    <Plus className="h-3.5 w-3.5" />
                    {actionLabel}
                </Button>
            )}
        </div>
    );
}
