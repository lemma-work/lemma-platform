'use client';

import { use, useMemo, useState } from 'react';
import Link from 'next/link';
import {
    Bot,
    Boxes,
    CalendarClock,
    ChevronRight,
    MessageCircle,
    Plus,
    Share2,
    Waypoints,
    type LemmaIcon,
} from '@/components/ui/icons';
import { toast } from 'sonner';

import { LemmaMark } from '@/components/brand/logo';
import { Button } from '@/components/ui/button';
import { DestructiveConfirmationDialog } from '@/components/shared/destructive-confirmation-dialog';
import { EmptyState } from '@/components/shared/empty-state';
import { ConceptHint } from '@/components/education/concept-hint';
import { SectionPrimer } from '@/components/education/section-primer';
import { ResourceIndexHeader, ResourceIndexShell, ResourceMetricButton, ResourceMetricStrip } from '@/components/pod/resource-layout';
import { ResourceIcon } from '@/components/shared/resource-icon';
import { DestructiveResourceActionItem, ResourceActionsMenu } from '@/components/shared/resource-actions-menu';
import { ResourceShareButton, ResourceVisibilityBadge, type ResourceVisibilityValue } from '@/components/shared/resource-visibility';
import { DropdownMenuItem } from '@/components/ui/dropdown-menu';
import { useAgents, useDeleteAgent, useUpdateAgent } from '@/lib/hooks/use-agents';
import { resourceAllows } from '@/lib/authz/resource-actions';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { useFlows } from '@/lib/hooks/use-flows';
import { usePodAutomation } from '@/lib/hooks/use-pod-automation';
import { useSchedules } from '@/lib/hooks/use-schedules';
import type { Agent, UpdateAgentData, Workflow } from '@/lib/types';
import { NodeType } from '@/lib/types';
import { formatAgentName } from '@/lib/utils/agents';
import { getAgentNodeName } from '@/lib/utils/flow-node-config';

type AgentFilter = 'all' | 'workflows' | 'scheduled';

function countConnections(agent: Agent): number {
    return (
        (agent.tool_sets?.length ?? agent.toolsets?.length ?? 0)
        + (agent.accessible_tables?.length ?? 0)
        + (agent.accessible_folders?.length ?? 0)
        + (agent.accessible_connectors?.length ?? 0)
    );
}

function agentSummary(agent: Agent): string | null {
    const description = agent.description?.trim();
    if (description) return description;
    return null;
}

export default function AgentsPage({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    const { id: podId } = use(params);
    const podAccess = usePodAccess(podId);
    const canCreateAgent = podAccess.can('agent.create');
    const canUpdateAgent = podAccess.can('agent.update');
    const canDeleteAgent = podAccess.can('agent.delete');
    const canReadSchedules = podAccess.can('schedule.read');
    const canReadWorkflows = podAccess.can('workflow.read');
    const canUseSurfaces = podAccess.canAccessRoute('surfaces');
    const { data: agentsData, isLoading } = useAgents(podId);
    const { data: schedulesData } = useSchedules(canReadSchedules ? podId : undefined, { limit: 100 });
    const { data: flowsData } = useFlows(canReadWorkflows ? podId : undefined);
    // Surfaces that fall to the pod default assistant (the virtual Super Agent).
    const automation = usePodAutomation(podId, { schedules: false, surfaces: canUseSurfaces });
    const defaultSurfaceCount = automation.defaultSurfaces.length;
    const { mutate: deleteAgent, isPending: isDeletingAgent } = useDeleteAgent();
    const updateAgent = useUpdateAgent();
    const [agentFilter, setAgentFilter] = useState<AgentFilter>('all');
    const [agentPendingDelete, setAgentPendingDelete] = useState<Agent | null>(null);

    const agents = useMemo(() => agentsData?.items ?? [], [agentsData?.items]);
    const flows = useMemo(() => flowsData || [], [flowsData]);
    const schedules = useMemo(() => schedulesData?.items || [], [schedulesData?.items]);
    const activeAgentScheduleCount = schedules.filter((schedule) => schedule.agent_name && schedule.is_active !== false).length;
    const scheduledAgentNames = useMemo(() => new Set(
        schedules
            .filter((schedule) => schedule.agent_name && schedule.is_active !== false)
            .map((schedule) => schedule.agent_name as string)
    ), [schedules]);
    const agentUsage = useMemo(() => buildAgentUsage(flows), [flows]);
    const filteredAgents = useMemo(() => {
        return agents.filter((agent) => {
            const isScheduled = scheduledAgentNames.has(agent.name);
            const isInWorkflow = (agentUsage.get(agent.name)?.size || 0) > 0;
            const matchesFilter =
                agentFilter === 'all'
                || (agentFilter === 'scheduled' && isScheduled)
                || (agentFilter === 'workflows' && isInWorkflow);
            return matchesFilter;
        });
    }, [agentFilter, agentUsage, agents, scheduledAgentNames]);
    const agentsInWorkflows = agents.filter((agent) => (agentUsage.get(agent.name)?.size || 0) > 0).length;
    const agentPendingDeleteScheduleCount = agentPendingDelete
        ? schedules.filter((schedule) => schedule.agent_name === agentPendingDelete.name && schedule.is_active !== false).length
        : 0;
    const agentPendingDeleteWorkflowCount = agentPendingDelete
        ? agentUsage.get(agentPendingDelete.name)?.size || 0
        : 0;

    const handleDeleteAgent = () => {
        if (!agentPendingDelete) return;
        if (!resourceAllows(agentPendingDelete, 'agent.delete', canDeleteAgent)) return;
        deleteAgent(
            { podId, agentName: agentPendingDelete.name },
            {
                onSuccess: () => {
                    toast.success('Agent deleted');
                    setAgentPendingDelete(null);
                },
                onError: () => toast.error('Failed to delete agent'),
            }
        );
    };

    return (
        <ResourceIndexShell>
            <ResourceIndexHeader
                title="Agents"
                productIconKind="agents"
                meta={<ConceptHint concept="agent" />}
                actions={(
                    canCreateAgent ? <Link href={`/pod/${podId}/agents/new`}>
                        <Button className="gap-2" size="sm">
                            <Plus className="h-4 w-4" />
                            New agent
                        </Button>
                    </Link> : null
                )}
            />

            <SectionPrimer concept="agent" className="mb-4" />

            {isLoading ? (
                <div className="space-y-4">
                    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                        {[1, 2, 3, 4].map((item) => (
                            <div key={`agent-metric-skeleton-${item}`} className="h-28 animate-pulse rounded-lg bg-[var(--surface-2)]" />
                        ))}
                    </div>
                    <div className="h-80 animate-pulse rounded-lg bg-[var(--surface-2)]" />
                </div>
            ) : agents.length === 0 ? (
                <EmptyState
                    variant="panel"
                    icon={<Bot className="h-5 w-5" />}
                    title="No agents yet"
                    description={canCreateAgent
                        ? "Add the first agent this pod can run. Start with a role, instructions, and the context it can access."
                        : "No agents are available to you yet."}
                    action={canCreateAgent ? (
                        <Link href={`/pod/${podId}/agents/new`}>
                            <Button size="sm" className="gap-2">
                                <Plus className="h-4 w-4" />
                                New agent
                            </Button>
                        </Link>
                    ) : undefined}
                />
            ) : (
                <div>
                    <ResourceMetricStrip>
                        <ResourceMetricButton active={agentFilter === 'all'} label="Agents" count={agents.length} onClick={() => setAgentFilter('all')} />
                        <ResourceMetricButton active={agentFilter === 'workflows'} label="In workflows" count={agentsInWorkflows} onClick={() => setAgentFilter('workflows')} />
                        <ResourceMetricButton active={agentFilter === 'scheduled'} label="Scheduled" count={activeAgentScheduleCount} onClick={() => setAgentFilter('scheduled')} />
                    </ResourceMetricStrip>

                    <section className="resource-index-grid resource-index-grid-md-2 resource-index-grid-xl-3 sm:grid-cols-2 xl:grid-cols-3">
                        {canUseSurfaces && agentFilter === 'all' ? (
                            <PodAssistantCard podId={podId} channelCount={defaultSurfaceCount} />
                        ) : null}
                        {filteredAgents.map((agent) => (
                            <AgentProfileCard
                                key={agent.id}
                                agent={agent}
                                podId={podId}
                                activeScheduleCount={schedules.filter((schedule) => schedule.agent_name === agent.name && schedule.is_active !== false).length}
                                workflowCount={agentUsage.get(agent.name)?.size || 0}
                                onDelete={setAgentPendingDelete}
                                canUpdate={resourceAllows(agent, 'agent.update', canUpdateAgent)}
                                canDelete={resourceAllows(agent, 'agent.delete', canDeleteAgent)}
                                onShareVisibilityChange={async (visibility) => {
                                    await updateAgent.mutateAsync({
                                        podId,
                                        agentName: agent.name,
                                        data: { visibility: visibility as UpdateAgentData['visibility'] },
                                    });
                                }}
                            />
                        ))}
                        {filteredAgents.length === 0 ? (
                            <EmptyState
                                variant="compact"
                                icon={<Bot className="h-4 w-4" />}
                                title="No agents match this search"
                                description="Try a different agent name or description."
                            />
                        ) : null}
                    </section>
                </div>
            )}
            <DestructiveConfirmationDialog
                open={Boolean(agentPendingDelete)}
                onOpenChange={(open) => {
                    if (!open) setAgentPendingDelete(null);
                }}
                title="Delete agent"
                description={`Delete "${agentPendingDelete ? formatAgentName(agentPendingDelete.name) : ''}"? This removes the agent from this pod.`}
                resourceName={agentPendingDelete ? formatAgentName(agentPendingDelete.name) : ''}
                consequences={[
                    agentPendingDeleteWorkflowCount > 0
                        ? `${agentPendingDeleteWorkflowCount} workflow${agentPendingDeleteWorkflowCount === 1 ? '' : 's'} reference this agent and may fail until updated.`
                        : 'No workflows currently reference this agent.',
                    agentPendingDeleteScheduleCount > 0
                        ? `${agentPendingDeleteScheduleCount} active schedule${agentPendingDeleteScheduleCount === 1 ? '' : 's'} target this agent.`
                        : 'No active schedules currently target this agent.',
                    'This action cannot be undone.',
                ]}
                confirmLabel="Delete agent"
                pendingLabel="Deleting agent..."
                isPending={isDeletingAgent}
                onConfirm={handleDeleteAgent}
            />
        </ResourceIndexShell>
    );
}

// Stable per-agent tint (see .resource-monogram[data-tint] in builders-and-ledgers.css)
// so identical sparkle avatars become distinguishable.
const AGENT_TINT_COUNT = 6;

function hashString(value: string): number {
    let result = 0;
    for (let i = 0; i < value.length; i += 1) {
        result = (result << 5) - result + value.charCodeAt(i);
        result |= 0;
    }
    return Math.abs(result);
}

function agentInitials(name: string): string {
    const tokens = name.trim().split(/[\s\-_]+/).filter(Boolean);
    if (tokens.length >= 2) return `${tokens[0][0]}${tokens[1][0]}`.toUpperCase();
    return (tokens[0] || name).slice(0, 2).toUpperCase();
}

function AgentMonogram({ name }: { name: string }) {
    return (
        <span
            className="resource-monogram flex h-full w-full items-center justify-center rounded-lg text-sm font-semibold"
            data-tint={hashString(name) % AGENT_TINT_COUNT}
        >
            {agentInitials(name)}
        </span>
    );
}

function AgentStat({ icon: Icon, value, label }: { icon: LemmaIcon; value: number; label: string }) {
    return (
        <span className="inline-flex items-center gap-1" title={label} aria-label={label}>
            <Icon className="h-3.5 w-3.5" aria-hidden />
            {value}
        </span>
    );
}

// The pod's default responder, rendered as a first-class card even though it
// has no agent row. Its "channels" are the surfaces not assigned to any agent.
function PodAssistantCard({ podId, channelCount }: { podId: string; channelCount: number }) {
    return (
        <Link
            href={`/pod/${podId}/ai/assistant`}
            className="resource-index-card group flex min-h-40 flex-col p-4"
        >
            <div className="flex items-start justify-between gap-3">
                <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-[var(--card-bg)] shadow-[var(--shadow-xs)]">
                    <LemmaMark size="sm" />
                </span>
                <span className="chip chip-sm chip-muted shrink-0">Default</span>
            </div>

            <div className="mt-3 min-w-0">
                <h2 className="truncate text-base font-semibold tracking-normal text-[var(--text-primary)]">Pod Assistant</h2>
                <p className="mt-1 line-clamp-2 min-h-10 text-sm leading-6 text-[var(--text-secondary)]">
                    This pod&apos;s most capable agent — adds tables, builds workflows, spins up agents, and edits data directly.
                </p>
            </div>

            <div className="mt-3 flex items-center justify-between gap-2 text-xs text-[var(--text-tertiary)]">
                {channelCount > 0 ? (
                    <span className="inline-flex items-center gap-1" title={`${channelCount} unassigned channel${channelCount === 1 ? '' : 's'}`}>
                        <MessageCircle className="h-3.5 w-3.5" aria-hidden />
                        {channelCount} channel{channelCount === 1 ? '' : 's'}
                    </span>
                ) : null}
                <span className="inline-flex items-center gap-1 font-medium text-[var(--text-secondary)] transition-gentle group-hover:translate-x-0.5">
                    Open
                    <ChevronRight className="h-3.5 w-3.5" />
                </span>
            </div>
        </Link>
    );
}

function AgentProfileCard({
    agent,
    podId,
    activeScheduleCount,
    workflowCount,
    onDelete,
    canUpdate,
    canDelete,
    onShareVisibilityChange,
}: {
    agent: Agent;
    podId: string;
    activeScheduleCount: number;
    workflowCount: number;
    onDelete: (agent: Agent) => void;
    canUpdate: boolean;
    canDelete: boolean;
    onShareVisibilityChange: (visibility: ResourceVisibilityValue) => Promise<void>;
}) {
    const connectionCount = countConnections(agent);
    const summary = agentSummary(agent);
    const status = activeScheduleCount > 0
        ? { label: 'Scheduled', className: 'text-[var(--state-success)]' }
        : workflowCount > 0
            ? { label: 'In workflow', className: 'text-[var(--text-secondary)]' }
            : null;
    const hasMenuActions = canUpdate || canDelete;
    const agentShareUrl = typeof window === 'undefined'
        ? undefined
        : `${window.location.origin}/pod/${podId}/agents/${encodeURIComponent(agent.name)}`;

    return (
        <div className="resource-index-card group min-h-40 p-4">
            <div className="flex items-start justify-between gap-3">
                <Link href={`/pod/${podId}/agents/${encodeURIComponent(agent.name)}`} className="min-w-0 flex-1">
                    <ResourceIcon
                        iconUrl={agent.icon_url}
                        alt={`${formatAgentName(agent.name)} profile picture`}
                        label={agent.name}
                        imageClassName="object-contain p-1"
                        className="h-11 w-11 shrink-0 rounded-lg bg-transparent"
                        fallback={<AgentMonogram name={agent.name} />}
                    />
                </Link>
                {status ? (
                    <span className={`inline-flex shrink-0 items-center gap-1.5 text-xs font-medium ${status.className}`}>
                        <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
                        {status.label}
                    </span>
                ) : null}
            </div>

            <Link href={`/pod/${podId}/agents/${encodeURIComponent(agent.name)}`} className="block">
                <div className="mt-3 min-w-0">
                    <h2 className="truncate text-base font-semibold tracking-normal text-[var(--text-primary)]">{formatAgentName(agent.name)}</h2>
                    <p className="mt-1 line-clamp-2 min-h-10 text-sm leading-6 text-[var(--text-secondary)]">
                        {summary || 'Ready for instructions, tools, and pod context.'}
                    </p>
                </div>
            </Link>

            <div className="mt-3 flex items-center justify-between gap-2 text-xs text-[var(--text-tertiary)]">
                <div className="flex min-w-0 items-center gap-3">
                    <ResourceVisibilityBadge visibility={agent.visibility} resourceLabel="agents" hideWhenDefault />
                    {connectionCount > 0 ? (
                        <AgentStat icon={Boxes} value={connectionCount} label={`${connectionCount} tool${connectionCount === 1 ? '' : 's'} & data source${connectionCount === 1 ? '' : 's'} connected`} />
                    ) : null}
                    {workflowCount > 0 ? (
                        <AgentStat icon={Waypoints} value={workflowCount} label={`In ${workflowCount} workflow${workflowCount === 1 ? '' : 's'}`} />
                    ) : null}
                    {activeScheduleCount > 0 ? (
                        <AgentStat icon={CalendarClock} value={activeScheduleCount} label={`${activeScheduleCount} active schedule${activeScheduleCount === 1 ? '' : 's'}`} />
                    ) : null}
                    {connectionCount === 0 && workflowCount === 0 && activeScheduleCount === 0 ? (
                        <span>Ready to set up</span>
                    ) : null}
                </div>
                <div className="flex shrink-0 items-center gap-1">
                {hasMenuActions ? (
                    <ResourceActionsMenu
                        ariaLabel={`Open actions for ${formatAgentName(agent.name)}`}
                        align="end"
                        triggerClassName="h-7 w-7 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
                    >
                        {canUpdate ? (
                            <ResourceShareButton
                                value={agent.visibility}
                                podId={podId}
                                resourceType="agent"
                                resourceId={agent.id}
                                resourceLabel="agents"
                                resourceName={formatAgentName(agent.name)}
                                shareUrl={agentShareUrl}
                                onChange={onShareVisibilityChange}
                                className="contents"
                                trigger={({ openShare, disabled }) => (
                                    <DropdownMenuItem
                                        disabled={disabled}
                                        onSelect={(event) => {
                                            event.preventDefault();
                                            openShare();
                                        }}
                                    >
                                        <Share2 className="mr-2 h-4 w-4" />
                                        Share
                                    </DropdownMenuItem>
                                )}
                            />
                        ) : null}
                        {canDelete ? (
                            <DestructiveResourceActionItem onSelect={() => onDelete(agent)}>
                            Delete agent
                            </DestructiveResourceActionItem>
                        ) : null}
                    </ResourceActionsMenu>
                ) : null}
                <Link
                    href={`/pod/${podId}/agents/${encodeURIComponent(agent.name)}`}
                    className="inline-flex items-center gap-1 font-medium text-[var(--text-secondary)] transition-gentle group-hover:translate-x-0.5"
                >
                    Open
                    <ChevronRight className="h-3.5 w-3.5" />
                </Link>
                </div>
            </div>
        </div>
    );
}

function buildAgentUsage(flows: Workflow[]) {
    const usage = new Map<string, Set<string>>();
    flows.forEach((flow) => {
        (flow.nodes || []).forEach((node) => {
            if (node.type !== NodeType.AGENT) return;
            const agentName = getAgentNodeName(node.config);
            if (!agentName) return;
            const set = usage.get(agentName) || new Set<string>();
            set.add(flow.name);
            usage.set(agentName, set);
        });
    });
    return usage;
}
