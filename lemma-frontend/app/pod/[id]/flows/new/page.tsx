'use client';

import { use, useMemo, useState, type ReactNode } from 'react';
import { useRouter } from 'next/navigation';
import {
    ArrowLeft,
    ArrowRight,
    CheckCircle2,
    Loader2,
    Plus,
    Sparkles,
    Workflow,
} from '@/components/ui/icons';
import { toast } from 'sonner';

import { FlowTemplate, flowTemplates, getTemplateById } from '@/components/flows/flow-templates';
import { PodHeaderMetrics, PodPageHeader } from '@/components/pod/pod-page-header';
import { ProductIcon } from '@/components/pod/product-icon';
import { ResourceIconUploader } from '@/components/shared/resource-icon-uploader';
import { showResourceCreatedToast, showResourceErrorToast } from '@/components/shared/resource-feedback';
import { ResourceVisibilitySelect } from '@/components/shared/resource-visibility';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/shared/empty-state';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { useCreateFlow, useUpdateFlowGraph } from '@/lib/hooks/use-flows';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { cn } from '@/lib/utils';

type BuilderStepId = 'details' | 'starter';

const BUILDER_STEPS: Array<{
    id: BuilderStepId;
    label: string;
    eyebrow: string;
    description: string;
}> = [
    {
        id: 'details',
        label: 'Name the workflow',
        eyebrow: 'Identity',
        description: 'Give this reusable procedure a clear name and purpose.',
    },
    {
        id: 'starter',
        label: 'Choose a starting shape',
        eyebrow: 'Procedure',
        description: 'Pick a safe skeleton now, then add agents and functions in the editor.',
    },
];

const categoryLabels: Record<FlowTemplate['category'], string> = {
    automation: 'Automation',
    approval: 'Approval',
    data: 'Data',
    ai: 'AI',
    notification: 'Notify',
};

export default function NewFlowPage({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    const { id: podId } = use(params);
    const router = useRouter();
    const podAccess = usePodAccess(podId);
    const createFlow = useCreateFlow();
    const updateFlowGraph = useUpdateFlowGraph();

    const [currentStep, setCurrentStep] = useState<BuilderStepId>('details');
    const [selectedTemplate, setSelectedTemplate] = useState<FlowTemplate | null>(getTemplateById('blank') || null);
    const [formData, setFormData] = useState({
        name: '',
        description: '',
        visibility: 'POD',
    });
    const [iconUrl, setIconUrl] = useState<string | null>(null);

    const safeStepIndex = BUILDER_STEPS.findIndex((step) => step.id === currentStep);
    const currentIndex = safeStepIndex >= 0 ? safeStepIndex : 0;
    const activeStep = BUILDER_STEPS[currentIndex];
    const isFinalStep = currentStep === 'starter';
    const hasName = Boolean(formData.name.trim());
    const isBusy = createFlow.isPending || updateFlowGraph.isPending;
    const selectedTemplateName = selectedTemplate?.name || 'Blank workflow';
    const selectedStepCount = selectedTemplate?.definition?.nodes?.length || 0;
    const stepDone: Record<BuilderStepId, boolean> = {
        details: hasName,
        starter: Boolean(selectedTemplate),
    };
    const nextStep = BUILDER_STEPS[Math.min(currentIndex + 1, BUILDER_STEPS.length - 1)];

    const templates = useMemo(() => flowTemplates, []);

    const goToNextStep = () => {
        if (!hasName) {
            toast.error('Name the workflow first');
            return;
        }
        setCurrentStep('starter');
    };

    const goToPreviousStep = () => {
        setCurrentStep('details');
    };

    const handleSelectTemplate = (template: FlowTemplate) => {
        setSelectedTemplate(template);

        if (!formData.description.trim() && template.id !== 'blank') {
            setFormData((prev) => ({
                ...prev,
                description: template.description,
            }));
        }
    };

    const handleSubmit = async () => {
        if (!podAccess.can('workflow.create')) return;
        if (!selectedTemplate || !hasName) return;

        try {
            const flow = await createFlow.mutateAsync({
                podId,
                data: {
                    name: formData.name.trim(),
                    description: formData.description.trim() || undefined,
                    icon_url: iconUrl || undefined,
                    visibility: formData.visibility as never,
                },
            });

            if (selectedTemplate.definition.nodes.length > 0) {
                await updateFlowGraph.mutateAsync({
                    podId,
                    id: flow.name,
                    data: {
                        nodes: selectedTemplate.definition.nodes,
                        edges: selectedTemplate.definition.edges,
                    },
                });
            }

            showResourceCreatedToast('Workflow', flow.name);
            router.push(`/pod/${podId}/flows/${encodeURIComponent(flow.name)}?created=workflow`);
        } catch (error) {
            console.error('Failed to create workflow:', error);
            showResourceErrorToast(error, 'Failed to create workflow');
        }
    };

    if (!podAccess.isLoading && !podAccess.can('workflow.create')) {
        return (
            <div className="context-shell flex min-h-full items-center justify-center bg-transparent p-6">
                <EmptyState
                    variant="panel"
                    icon={<Workflow className="h-5 w-5" />}
                    title="No access to create workflows"
                    description="You can still open workflows you have permission to read."
                />
            </div>
        );
    }

    return (
        <div className="agent-builder-root flex h-full min-h-0 flex-col">
            <PodPageHeader
                podId={podId}
                variant="bar"
                title="Create workflow"
                eyebrow="Guided builder"
                backHref={`/pod/${podId}/flows`}
                backLabel="Workflows"
                productIconKind="workflows"
                meta={<PodHeaderMetrics items={[
                    { label: 'Status', value: hasName ? 'Ready' : 'Draft', tone: hasName ? 'ready' : 'muted' },
                    { label: 'Starter', value: selectedTemplateName, tone: selectedTemplate ? 'ready' : 'muted' },
                ]} />}
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
                            <span className="ml-2 text-[var(--text-primary)]">{formData.name.trim() || 'Unnamed workflow'}</span>
                        </div>
                    </section>

                    <CompactStepProgress
                        steps={BUILDER_STEPS}
                        currentStep={currentStep}
                        stepDone={stepDone}
                        onSelect={setCurrentStep}
                    />

                    <section className="agent-builder-stage" data-step={currentStep}>
                        {currentStep === 'details' ? (
                            <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_20rem]">
                                    <div className="space-y-4">
                                        <div>
                                            <Label htmlFor="flow-name" className="type-eyebrow">
                                                Workflow name
                                            </Label>
                                            <Input
                                                id="flow-name"
                                                required
                                                autoFocus
                                                placeholder="Customer Onboarding"
                                                value={formData.name}
                                                onChange={(event) => setFormData((prev) => ({ ...prev, name: event.target.value }))}
                                                className="mt-2 h-11 text-base font-normal"
                                            />
                                        </div>

                                        <div>
                                            <Label htmlFor="flow-description" className="type-eyebrow">
                                                One-line purpose
                                            </Label>
                                            <Textarea
                                                id="flow-description"
                                                rows={5}
                                                placeholder="What should this workflow coordinate?"
                                                value={formData.description}
                                                onChange={(event) => setFormData((prev) => ({ ...prev, description: event.target.value }))}
                                                className="mt-2 min-h-28 resize-y"
                                            />
                                        </div>
                                    </div>

                                    <div className="space-y-3">
                                        <div className="resource-soft-block p-3">
                                            <Label className="type-eyebrow">
                                                Display icon
                                            </Label>
                                            <div className="mt-2">
                                                <ResourceIconUploader
                                                    kind="flow"
                                                    name={formData.name || 'Workflow'}
                                                    value={iconUrl}
                                                    onChange={setIconUrl}
                                                    compact
                                                />
                                            </div>
                                        </div>
                                        <div className="resource-soft-block p-3">
                                            <ResourceVisibilitySelect
                                                value={formData.visibility}
                                                resourceLabel="workflows"
                                                resourceName={formData.name || 'New workflow'}
                                                onChange={(visibility) => setFormData((prev) => ({ ...prev, visibility }))}
                                            />
                                        </div>
                                    </div>
                                </div>
                        ) : null}

                        {currentStep === 'starter' ? (
                            <div className="space-y-6">
                                <div className="agent-builder-note max-w-3xl">
                                    Starters create only valid form, decision, wait, and end steps. Add agents and functions after creation once you can choose the real resources.
                                </div>
                                <TemplateList
                                    templates={templates}
                                    selectedTemplate={selectedTemplate}
                                    onSelectTemplate={handleSelectTemplate}
                                />
                            </div>
                        ) : null}

                        <div className="mt-8 max-w-xl">
                            <WorkflowPreview
                                name={formData.name}
                                description={formData.description}
                                templateName={selectedTemplateName}
                                stepCount={selectedStepCount}
                                iconUrl={iconUrl}
                            />
                        </div>
                    </section>
                </div>
            </main>

            <footer className="resource-footer-glass shrink-0 px-5 py-2.5 backdrop-blur-md">
                <div className="mx-auto flex w-full max-w-[86rem] items-center justify-between gap-3">
                    <div className="min-w-0">
                        <p className="type-eyebrow">
                            Step {currentIndex + 1} of {BUILDER_STEPS.length}
                        </p>
                        <p className="truncate text-sm font-normal text-[var(--text-primary)]">
                            {activeStep.label}
                            {!isFinalStep ? <span className="font-normal text-[var(--text-tertiary)]"> · Next: {nextStep.label}</span> : null}
                        </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                        <Button
                            type="button"
                            variant="outline"
                            onClick={goToPreviousStep}
                            disabled={currentStep === 'details' || isBusy}
                            className="gap-2"
                        >
                            <ArrowLeft className="h-3.5 w-3.5" />
                            Previous
                        </Button>
                        {isFinalStep ? (
                            <Button
                                type="button"
                                onClick={handleSubmit}
                                disabled={isBusy || !hasName || !selectedTemplate}
                                className="gap-2"
                            >
                                {isBusy ? (
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                ) : (
                                    <Sparkles className="h-4 w-4" />
                                )}
                                {isBusy ? 'Creating...' : 'Create workflow'}
                            </Button>
                        ) : (
                            <Button
                                type="button"
                                onClick={goToNextStep}
                                disabled={!hasName || isBusy}
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

function TemplateList({
    templates,
    selectedTemplate,
    onSelectTemplate,
}: {
    templates: FlowTemplate[];
    selectedTemplate: FlowTemplate | null;
    onSelectTemplate: (template: FlowTemplate) => void;
}) {
    return (
        <div className="grid gap-3 md:grid-cols-2">
            {templates.map((template) => {
                const selected = selectedTemplate?.id === template.id;
                const stepCount = template.definition.nodes.length;
                return (
                    <button
                        key={template.id}
                        type="button"
                        className={cn(
                            'flow-new-template-card-button flex min-h-32 items-start gap-4 rounded-lg border p-4 text-left transition-gentle',
                            selected
                                ? 'resource-option-selected'
                                : 'resource-option-hover border-transparent bg-transparent'
                        )}
                        onClick={() => onSelectTemplate(template)}
                    >
                        <span className={cn(
                            'flex h-10 w-10 shrink-0 items-center justify-center rounded-md',
                            selected ? 'bg-[var(--action-primary)] text-[var(--text-on-brand)]' : 'resource-soft-icon'
                        )}>
                            {template.id === 'blank' ? <Plus className="h-5 w-5" /> : <Workflow className="h-5 w-5" />}
                        </span>
                        <span className="min-w-0 flex-1">
                            <span className="block text-base font-normal text-[var(--text-primary)]">{template.name}</span>
                            <span className="mt-2 line-clamp-2 block text-sm leading-6 text-[var(--text-secondary)]">
                                {template.description}
                            </span>
                            <span className="mt-3 flex flex-wrap items-center gap-2">
                                <span className="chip chip-sm">
                                    {categoryLabels[template.category]}
                                </span>
                                <span className="chip chip-sm">
                                    {stepCount > 0 ? `${stepCount} step${stepCount === 1 ? '' : 's'}` : 'Empty'}
                                </span>
                            </span>
                        </span>
                    </button>
                );
            })}
        </div>
    );
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
        <nav className="agent-builder-step-strip flow-new-step-strip" data-progress={currentIndex + 1}>
            <div className="flex gap-1.5 overflow-x-auto">
                {steps.map((step, index) => {
                    const active = step.id === currentStep;
                    const done = stepDone[step.id] && !active;

                    return (
                        <button
                            key={step.id}
                            type="button"
                            className={cn(
                                'agent-builder-step-button flex h-8 shrink-0 items-center gap-2 rounded-md px-2 text-left text-xs transition-colors',
                                active ? 'agent-builder-step-button-active' : ''
                            )}
                            onClick={() => onSelect(step.id)}
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

function WorkflowPreview({
    name,
    description,
    templateName,
    stepCount,
    iconUrl,
}: {
    name: string;
    description: string;
    templateName: string;
    stepCount: number;
    iconUrl?: string | null;
}) {
    const displayName = name.trim() || 'Untitled workflow';
    const purpose = description.trim() || 'No one-line purpose yet.';

    return (
        <section className="p-2">
            <div className="flex items-center gap-3">
                {iconUrl ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={iconUrl} alt="" className="h-14 w-14 shrink-0 rounded-md object-cover" />
                ) : (
                    <span className="flex h-14 w-14 shrink-0 items-center justify-center">
                        <ProductIcon kind="workflows" size="lg" />
                    </span>
                )}
                <div className="min-w-0">
                    <h3 className="truncate text-base font-normal text-[var(--text-primary)]">{displayName}</h3>
                    <p className="mt-1 line-clamp-2 text-xs leading-5 text-[var(--text-tertiary)]">{purpose}</p>
                </div>
            </div>

            <div className="my-4 h-px bg-[var(--row-border)]" />

            <div className="space-y-2 text-xs text-[var(--text-secondary)]">
                <PreviewLine done={Boolean(name.trim())} text="Named workflow" />
                <PreviewLine done text={templateName} />
                <PreviewLine done={stepCount > 0} text={stepCount > 0 ? `${stepCount} starter step${stepCount === 1 ? '' : 's'}` : 'Blank canvas'} muted />
            </div>

            <p className="mt-4 rounded-md bg-[var(--row-bg)] px-3 py-2 text-xs leading-5 text-[var(--text-secondary)]">
                Schedules decide when this runs. This page only creates the work procedure.
            </p>
        </section>
    );
}

function PreviewLine({ done, text, muted }: { done: boolean; text: ReactNode; muted?: boolean }) {
    return (
        <div className={cn('flex items-center gap-2', muted && !done ? 'text-[var(--text-tertiary)]' : undefined)}>
            <span className={cn(
                'flex h-4 w-4 shrink-0 items-center justify-center rounded-full',
                done
                    ? 'resource-done-dot'
                    : 'bg-transparent text-transparent'
            )}>
                <CheckCircle2 className="h-2.5 w-2.5" />
            </span>
            {text}
        </div>
    );
}
