'use client';

import { use, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ArrowRight, ArrowUp, Loader2, MessageCircle, Plus, UserPlus, X } from '@/components/ui/icons';

import { useAIAssistant } from '@/components/ai/ai-assistant-context';
import { StepLoader } from '@/components/brand/loader';
import { ProtectedRoute } from '@/components/auth/protected-route';
import { FirstWinChecklist } from '@/components/education/first-win-checklist';
import { resolveDefaultAgentRuntime } from '@/components/agents/agent-runtime-helpers';
import { RuntimeModelPicker } from '@/components/lemma/assistant/model-picker';
import { RecipeFeatureCard } from '@/components/recipes/recipe-card';
import { featuredRecipes } from '@/lib/recipes/recipes';
import { useLaunchRecipe } from '@/lib/recipes/use-launch-recipe';
import { useAgents } from '@/lib/hooks/use-agents';
import { useScopedConversations } from '@/lib/hooks/use-assistants';
import { useAgentRuntimes, useAvailableAgentRuntimeHarnesses } from '@/lib/hooks/use-agent-runtime';
import {
    normalizeWorkflowRunStatus,
    useFlows,
    useWorkflowRunSnapshots,
} from '@/lib/hooks/use-flows';
import { usePod } from '@/lib/hooks/use-pods';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { usePodJoinRequests } from '@/lib/hooks/use-pod-join-requests';
import { usePodSurfaces } from '@/lib/hooks/use-pod-surfaces';
import { useSchedules } from '@/lib/hooks/use-schedules';
import { cn } from '@/lib/utils';
import { formatAgentName } from '@/lib/utils/agents';
import { isConversationRunningStatus, normalizeConversationStatus } from '@/lib/utils/conversations';
import { describeScheduleConfig, getScheduleTargetKind, getScheduleTargetName } from '@/lib/utils/schedules';
import type { AgentRuntimeConfig, AssistantSurface, Conversation } from '@/lib/types';

const RUNNING_RUN_STATUSES = new Set(['PENDING', 'RUNNING', 'EXECUTING', 'IN_PROGRESS', 'PROCESSING']);
const FAILED_RUN_STATUSES = new Set(['FAILED', 'ERROR', 'CANCELLED', 'CANCELED']);
const COMPLETED_RUN_STATUSES = new Set(['COMPLETED', 'SUCCESS', 'SUCCEEDED']);
const RECENT_CONVERSATION_STATUSES = new Set(['completed', 'complete', 'success', 'succeeded', 'failed', 'error']);
const COMPOSER_LAUNCH_DURATION_MS = 560;
const HOME_PANELS_DEFER_MS = 600;

interface ComposerLaunchAnimation {
    id: number;
    message: string;
    from: {
        top: number;
        height: number;
    };
    to: {
        top: number;
    };
    active: boolean;
    done: boolean;
}

function PodBlankChatHome({ podId }: { podId: string }) {
    const router = useRouter();
    const assistant = useAIAssistant();
    const podAccess = usePodAccess(podId);
    const { data: pod } = usePod(podId);
    const { data: runtimeCatalog } = useAgentRuntimes(pod?.organization_id);
    const { data: availableHarnesses } = useAvailableAgentRuntimeHarnesses();
    const canWriteConversations = podAccess.can('conversation.write');
    const [draft, setDraft] = useState('');
    const [isSending, setIsSending] = useState(false);
    const [launchAnimation, setLaunchAnimation] = useState<ComposerLaunchAnimation | null>(null);
    const [pendingRouteConversationId, setPendingRouteConversationId] = useState<string | null>(null);
    const [isRouteHandoff, setIsRouteHandoff] = useState(false);
    const [showHomePanels, setShowHomePanels] = useState(false);
    const rootRef = useRef<HTMLDivElement>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const composerFormRef = useRef<HTMLFormElement>(null);
    const composerInputRef = useRef<HTMLTextAreaElement>(null);
    const submittedFromConversationRef = useRef<string | null>(null);
    const launchFrameRef = useRef<number | null>(null);
    const launchTimerRef = useRef<number | null>(null);

    const isLaunchingComposer = launchAnimation !== null;
    const isBlankingHome = isLaunchingComposer || isRouteHandoff;
    const isBusy = isSending || isBlankingHome || assistant.isLoading || assistant.isOpenedConversationRunning || assistant.isUploadingFiles;
    const canSend = canWriteConversations && draft.trim().length > 0 && !isBusy;
    const podDefaultRuntime = pod?.config?.default_runtime
        ?? resolveDefaultAgentRuntime(runtimeCatalog, pod?.config?.default_profile_id, availableHarnesses);
    const selectedCommandRuntime = assistant.conversationRuntime ?? null;

    const handleCommandRuntimeChange = (runtime: AgentRuntimeConfig | null) => {
        void assistant.setConversationModel(
            (runtime?.model_name ?? null) as never,
            runtime,
        );
    };

    useEffect(() => {
        const timer = window.setTimeout(() => setShowHomePanels(true), HOME_PANELS_DEFER_MS);
        return () => window.clearTimeout(timer);
    }, []);

    useEffect(() => {
        const previousConversationId = submittedFromConversationRef.current;
        if (previousConversationId === null) return;
        if (!assistant.openedConversationId) return;
        if (assistant.openedConversationId === previousConversationId) return;
        submittedFromConversationRef.current = null;
        if (launchAnimation && !launchAnimation.done) {
            setPendingRouteConversationId(assistant.openedConversationId);
            return;
        }
        setIsRouteHandoff(true);
        router.replace(`/pod/${podId}/conversations/${encodeURIComponent(assistant.openedConversationId)}`);
    }, [assistant.openedConversationId, launchAnimation, podId, router]);

    useEffect(() => {
        if (!pendingRouteConversationId || (launchAnimation && !launchAnimation.done)) return;
        const nextConversationId = pendingRouteConversationId;
        setIsRouteHandoff(true);
        router.replace(`/pod/${podId}/conversations/${encodeURIComponent(nextConversationId)}`);
    }, [launchAnimation, pendingRouteConversationId, podId, router]);

    useEffect(() => {
        return () => {
            if (launchFrameRef.current !== null) {
                window.cancelAnimationFrame(launchFrameRef.current);
            }
            if (launchTimerRef.current !== null) {
                window.clearTimeout(launchTimerRef.current);
            }
        };
    }, []);

    const startComposerLaunchAnimation = (message: string) => {
        if (typeof window === 'undefined') return;
        if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

        const form = composerFormRef.current;
        const root = rootRef.current;
        if (!form || !root) return;

        const rect = form.getBoundingClientRect();
        const rootRect = root.getBoundingClientRect();
        const viewportWidth = window.innerWidth;
        const viewportHeight = window.innerHeight;
        const bottomInset = viewportWidth >= 640 ? 18 : 12;
        const startTop = Math.max(0, rect.top - rootRect.top);
        const targetTop = Math.max(12, viewportHeight - rootRect.top - rect.height - bottomInset);
        const animationId = Date.now();

        if (launchFrameRef.current !== null) {
            window.cancelAnimationFrame(launchFrameRef.current);
        }
        if (launchTimerRef.current !== null) {
            window.clearTimeout(launchTimerRef.current);
        }

        setLaunchAnimation({
            id: animationId,
            message,
            from: {
                top: startTop,
                height: rect.height,
            },
            to: {
                top: targetTop,
            },
            active: false,
            done: false,
        });

        launchFrameRef.current = window.requestAnimationFrame(() => {
            launchFrameRef.current = window.requestAnimationFrame(() => {
                setLaunchAnimation((current) => current?.id === animationId ? { ...current, active: true } : current);
            });
        });

        launchTimerRef.current = window.setTimeout(() => {
            setLaunchAnimation((current) => current?.id === animationId ? { ...current, active: true, done: true } : current);
        }, COMPOSER_LAUNCH_DURATION_MS);
    };

    const handleFiles = async (files: FileList | null) => {
        if (!canWriteConversations) return;
        const selectedFiles = Array.from(files || []);
        if (selectedFiles.length === 0) return;
        await assistant.uploadFiles(selectedFiles, { deferUntilSend: true });
    };

    const submit = async () => {
        const message = draft.trim();
        if (!canWriteConversations || !message || isBusy) return;
        submittedFromConversationRef.current = assistant.openedConversationId || '';
        startComposerLaunchAnimation(message);
        setIsSending(true);
        try {
            assistant.clearMessages();
            await assistant.sendMessage(message, { forceNewConversation: true });
            setDraft('');
        } catch (error) {
            setLaunchAnimation(null);
            setPendingRouteConversationId(null);
            setIsRouteHandoff(false);
            submittedFromConversationRef.current = null;
            throw error;
        } finally {
            setIsSending(false);
        }
    };

    const launchAnimationStyle = launchAnimation ? {
        top: launchAnimation.from.top,
        height: launchAnimation.from.height,
        transform: `translate3d(0, ${launchAnimation.active ? launchAnimation.to.top - launchAnimation.from.top : 0}px, 0)`,
    } satisfies CSSProperties : undefined;

    return (
        <div ref={rootRef} className="relative flex min-h-full flex-col bg-transparent text-[var(--text-primary)]">
            <main
                aria-hidden={isBlankingHome}
                className={cn(
                    "mx-auto flex min-h-full w-full max-w-6xl flex-1 flex-col items-center px-5 pb-10 pt-10 sm:px-6 md:pt-14",
                    isBlankingHome && "pointer-events-none opacity-0",
                )}
            >
                <div className="w-full max-w-4xl">
                    {assistant.pendingFiles.length > 0 ? (
                        <div className="mb-3 flex flex-wrap justify-center gap-2">
                            {assistant.pendingFiles.map((file) => (
                                <span
                                    key={`${file.name}-${file.size}-${file.lastModified}`}
                                    className="inline-flex max-w-60 items-center gap-2 rounded-md border border-[color:var(--chip-border)] bg-[var(--chip-bg)] px-2.5 py-1.5 text-xs text-[var(--chip-fg)]"
                                >
                                    <span className="truncate">{file.name}</span>
                                    <button
                                        type="button"
                                        aria-label={`Remove ${file.name}`}
                                        onClick={() => assistant.removePendingFile(`${file.name}:${file.size}:${file.lastModified}`)}
                                        className="resource-remove-button h-4 w-4"
                                    >
                                        <X className="h-3 w-3" />
                                    </button>
                                </span>
                            ))}
                        </div>
                    ) : null}
                    <form
                        onSubmit={(event) => {
                            event.preventDefault();
                            void submit();
                        }}
                        ref={composerFormRef}
                        className={cn(
                            "form-field-control flex min-h-16 items-center gap-2 px-3 transition-opacity duration-150",
                            launchAnimation && "opacity-0",
                        )}
                    >
                        <input
                            ref={fileInputRef}
                            type="file"
                            multiple
                            className="hidden"
                            onChange={(event) => {
                                void handleFiles(event.currentTarget.files);
                                event.currentTarget.value = '';
                            }}
                        />
                        <button
                            type="button"
                            aria-label="Attach files"
                            title="Attach files"
                            onClick={() => fileInputRef.current?.click()}
                            disabled={isBusy || !canWriteConversations}
                            className="lemma-quiet-icon-button custom-focus-ring h-9 w-9 disabled:opacity-50"
                        >
                            <Plus className="h-4.5 w-4.5" strokeWidth={1.8} />
                        </button>
                        <textarea
                            ref={composerInputRef}
                            value={draft}
                            onChange={(event) => setDraft(event.target.value)}
                            onKeyDown={(event) => {
                                if (event.key === 'Enter' && !event.shiftKey) {
                                    event.preventDefault();
                                    void submit();
                                }
                            }}
                            rows={1}
                            placeholder={canWriteConversations ? "What should happen next?" : "You can read this pod, but not start new conversations."}
                            disabled={!canWriteConversations}
                            className="inline-edit-field min-h-10 flex-1 resize-none bg-transparent py-3 text-base leading-6 text-[var(--text-primary)] outline-none placeholder:text-[var(--text-tertiary)]"
                        />
                        <RuntimeModelPicker
                            catalog={runtimeCatalog}
                            availableHarnesses={availableHarnesses}
                            defaultRuntime={podDefaultRuntime}
                            value={selectedCommandRuntime}
                            onChange={handleCommandRuntimeChange}
                            disabled={!canWriteConversations}
                            compact
                            triggerLabelClassName="hidden sm:block"
                            scopeHint="Just for this chat"
                            manageHref={pod?.organization_id ? `/organizations/${pod.organization_id}/settings/agent-runtimes` : undefined}
                        />
                        <button
                            type="submit"
                            aria-label="Send"
                            disabled={!canSend}
                            className="pod-home-send-button custom-focus-ring inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--action-primary)] text-[var(--text-on-brand)] transition-colors hover:bg-[var(--action-primary-hover)] disabled:bg-[var(--surface-2)] disabled:text-[var(--text-tertiary)]"
                        >
                            {isBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowUp className="h-4 w-4" />}
                        </button>
                    </form>
                </div>
                {showHomePanels ? <PodAgentWorkflowKanban podId={podId} /> : <PodHomePanelsSkeleton />}
            </main>
            {launchAnimation && launchAnimationStyle ? (
                <div
                    aria-hidden="true"
                    className="pointer-events-none absolute left-5 right-5 z-50 will-change-transform transition-transform duration-500 ease-[cubic-bezier(0.22,1,0.36,1)] sm:left-6 sm:right-6"
                    /* eslint-disable-next-line no-restricted-syntax -- Runtime composer launch geometry is measured from the submitted input. */
                    style={launchAnimationStyle}
                >
                    <div className="composer-launch-ghost form-field-control mx-auto flex h-full min-h-16 w-full max-w-4xl items-center gap-2 px-3">
                        <span className="lemma-quiet-icon-button flex h-9 w-9 shrink-0 items-center justify-center opacity-70">
                            <Plus className="h-4.5 w-4.5" strokeWidth={1.8} />
                        </span>
                        <span className="min-w-0 flex-1 truncate py-3 text-left text-base leading-6 text-[var(--text-primary)]">
                            {launchAnimation.message}
                        </span>
                        <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--action-primary)] text-[var(--text-on-brand)]">
                            <Loader2 className="h-4 w-4 animate-spin" />
                        </span>
                    </div>
                </div>
            ) : null}
        </div>
    );
}

function PodHomePanelsSkeleton() {
    return (
        <div className="mt-8 w-full space-y-6" role="status" aria-label="Loading pod activity">
            <div className="space-y-2">
                <div className="lemma-skeleton h-3 w-20 rounded-md" />
                <div className="surface-panel flex items-center gap-3 p-3">
                    <div className="lemma-skeleton h-8 w-8 rounded-md" />
                    <div className="min-w-0 flex-1 space-y-2">
                        <div className="lemma-skeleton h-3 w-28 rounded-md" />
                        <div className="lemma-skeleton h-2.5 w-48 max-w-full rounded-md" />
                    </div>
                </div>
            </div>
            <div className="space-y-3">
                <div className="flex items-center justify-between gap-4">
                    <div className="lemma-skeleton h-4 w-20 rounded-md" />
                    <div className="lemma-skeleton h-3 w-24 rounded-md" />
                </div>
                <div className="pod-home-work-panel">
                    {[0, 1, 2].map((item) => (
                        <div key={item} className="pod-home-work-section-row space-y-2">
                            <div className="lemma-skeleton h-2.5 w-24 rounded-md" />
                            <div className="flex items-center gap-3 py-1">
                                <div className="lemma-skeleton h-1.5 w-1.5 rounded-full" />
                                <div className="lemma-skeleton h-3 w-36 rounded-md" />
                                <div className="lemma-skeleton ml-auto h-3 w-28 rounded-md" />
                            </div>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}

function PodJoinRequestsHomePanel({ podId }: { podId: string }) {
    const podAccess = usePodAccess(podId);
    const canManageMembers = podAccess.can('pod.member.manage');
    const { data, isLoading } = usePodJoinRequests(podId, 'PENDING');
    const requests = data?.items || [];

    if (!canManageMembers || isLoading || requests.length === 0) return null;

    const first = requests[0];
    const firstLabel = first.user_name || first.user_email || first.user_id;
    const headline =
        requests.length === 1
            ? `${firstLabel} wants to join this pod`
            : `${requests.length} people are waiting to join`;
    const detail =
        requests.length === 1
            ? first.user_email && first.user_email !== firstLabel
                ? first.user_email
                : 'Review and approve their access request.'
            : `Including ${firstLabel} and ${requests.length - 1} more.`;

    return (
        <section className="lemma-pop-card w-full p-4 sm:p-5">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex min-w-0 items-start gap-3">
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-[var(--row-border)] bg-[var(--delight-soft)] text-[var(--delight)]">
                        <UserPlus className="h-5 w-5" />
                    </div>
                    <div className="min-w-0">
                        <p className="truncate text-sm font-semibold text-[var(--text-primary)]">{headline}</p>
                        <p className="mt-1 truncate text-sm leading-6 text-[var(--text-secondary)]">{detail}</p>
                    </div>
                </div>
                <Link
                    href={`/pod/${podId}/settings/members?view=requests`}
                    className="custom-focus-ring inline-flex shrink-0 items-center justify-center gap-2 rounded-lg bg-[var(--action-primary)] px-3.5 py-2 text-sm font-medium text-[var(--text-on-brand)] transition-colors hover:bg-[var(--action-primary-hover)]"
                >
                    Review requests
                    <ArrowRight className="h-4 w-4" />
                </Link>
            </div>
        </section>
    );
}

type KanbanItem = {
    id: string;
    kind: 'agent' | 'workflow';
    title: string;
    detail: string;
    href: string;
    status: string;
    statusTone: 'muted' | 'success' | 'warning' | 'danger' | 'live';
    iconUrl?: string | null;
};

function PodAgentWorkflowKanban({ podId }: { podId: string }) {
    const podAccess = usePodAccess(podId);
    const canReadAgents = podAccess.can('agent.read');
    const canReadWorkflows = podAccess.can('workflow.read');
    const canReadSchedules = podAccess.can('schedule.read');
    const canReadConversations = podAccess.can('conversation.read');
    const { data: agentsData, isLoading: loadingAgents } = useAgents(canReadAgents ? podId : undefined);
    const { data: workflowsData = [], isLoading: loadingWorkflows } = useFlows(canReadWorkflows ? podId : undefined);
    const { data: schedulesData, isLoading: loadingSchedules } = useSchedules(canReadSchedules ? podId : undefined, { isActive: true, limit: 12 });
    const { data: conversationsData, isLoading: loadingConversations } = useScopedConversations({ podId }, { limit: 20, enabled: canReadConversations });

    const agents = useMemo(() => agentsData?.items || [], [agentsData?.items]);
    const workflows = useMemo(() => workflowsData || [], [workflowsData]);
    const schedules = useMemo(() => schedulesData?.items || [], [schedulesData?.items]);
    const conversations = useMemo(() => conversationsData?.items || [], [conversationsData?.items]);
    const sampledWorkflows = useMemo(() => workflows.slice(0, 8).map((workflow) => workflow.name), [workflows]);
    const { data: runSnapshots = [], isLoading: loadingRuns } = useWorkflowRunSnapshots(podId, sampledWorkflows, 3, { pollWhenLive: true, enabled: canReadWorkflows });

    const agentsByNameOrId = useMemo(() => {
        const map = new Map<string, (typeof agents)[number]>();
        agents.forEach((agent) => {
            map.set(agent.name, agent);
            if (agent.id) map.set(agent.id, agent);
        });
        return map;
    }, [agents]);

    const workflowsByNameOrId = useMemo(() => {
        const map = new Map<string, (typeof workflows)[number]>();
        workflows.forEach((workflow) => {
            map.set(workflow.name, workflow);
            if (workflow.id) map.set(workflow.id, workflow);
        });
        return map;
    }, [workflows]);

    const upcomingItems = useMemo<KanbanItem[]>(() => {
        return schedules
            .filter((schedule) => schedule.is_active !== false)
            .slice(0, 5)
            .map((schedule) => {
                const targetKind = getScheduleTargetKind(schedule);
                const targetName = getScheduleTargetName(schedule);
                const agent = targetKind === 'agent' ? agentsByNameOrId.get(targetName) : undefined;
                const workflow = targetKind === 'workflow' ? workflowsByNameOrId.get(targetName) : undefined;
                const resolvedName = agent?.name || workflow?.name || targetName;

                return {
                    id: `schedule-${schedule.id || schedule.workflow_name || schedule.agent_name || resolvedName}`,
                    kind: targetKind === 'agent' ? 'agent' as const : 'workflow' as const,
                    title: formatAgentName(resolvedName),
                    detail: describeScheduleConfig(schedule),
                    href: getScheduleHref(podId, schedule, agent?.name, workflow?.name),
                    status: 'Scheduled',
                    statusTone: 'muted' as const,
                    iconUrl: agent?.icon_url,
                };
            });
    }, [agentsByNameOrId, workflowsByNameOrId, podId, schedules]);

    const movingItems = useMemo<KanbanItem[]>(() => {
        const runningWorkflows = runSnapshots.flatMap((snapshot) => {
            const runningRun = snapshot.runs.find((run) => RUNNING_RUN_STATUSES.has(normalizeWorkflowRunStatus(run.status)));
            if (!runningRun) return [];

            return [{
                id: `run-${runningRun.id}`,
                kind: 'workflow' as const,
                title: formatDisplayName(snapshot.workflowName),
                detail: `Run ${formatDisplayName(normalizeWorkflowRunStatus(runningRun.status).toLowerCase())}.`,
                href: `/pod/${podId}/flows/${encodeURIComponent(snapshot.workflowName)}/runs/${encodeURIComponent(runningRun.id)}`,
                status: 'Running',
                statusTone: 'live' as const,
            }];
        });

        const runningAgentConversations = conversations
            .filter((conversation) => isConversationRunningStatus(conversation.status))
            .slice(0, Math.max(0, 5 - runningWorkflows.length))
            .map((conversation) => conversationToAgentItem(conversation, agentsByNameOrId, podId, 'live'));

        return [...runningWorkflows, ...runningAgentConversations].slice(0, 5);
    }, [agentsByNameOrId, conversations, podId, runSnapshots]);

    const recentOutcomeItems = useMemo<KanbanItem[]>(() => {
        const workflowOutcomes = runSnapshots.flatMap((snapshot) => {
            const outcomeRun = snapshot.runs.find((run) => {
                const status = normalizeWorkflowRunStatus(run.status);
                return FAILED_RUN_STATUSES.has(status) || COMPLETED_RUN_STATUSES.has(status);
            });
            if (!outcomeRun) return [];

            const status = normalizeWorkflowRunStatus(outcomeRun.status);
            const failed = FAILED_RUN_STATUSES.has(status);
            return [{
                id: `outcome-${outcomeRun.id}`,
                kind: 'workflow' as const,
                title: formatDisplayName(snapshot.workflowName),
                detail: `${failed ? 'Failed' : 'Completed'} ${formatRelativeTime(outcomeRun.completed_at || outcomeRun.updated_at || outcomeRun.created_at)}.`,
                href: `/pod/${podId}/flows/${encodeURIComponent(snapshot.workflowName)}/runs/${encodeURIComponent(outcomeRun.id)}`,
                status: failed ? 'Failed' : 'Completed',
                statusTone: failed ? 'danger' as const : 'success' as const,
            }];
        });

        const agentOutcomes = conversations
            .filter((conversation) => RECENT_CONVERSATION_STATUSES.has(normalizeConversationStatus(conversation.status)))
            .slice(0, Math.max(0, 5 - workflowOutcomes.length))
            .map((conversation) => {
                const status = normalizeConversationStatus(conversation.status);
                const failed = status === 'failed' || status === 'error';
                return conversationToAgentItem(conversation, agentsByNameOrId, podId, failed ? 'danger' : 'success');
            });

        return [...workflowOutcomes, ...agentOutcomes].slice(0, 5);
    }, [agentsByNameOrId, conversations, podId, runSnapshots]);

    const isLoading = loadingAgents || loadingWorkflows || loadingSchedules || loadingRuns || loadingConversations;
    const hasKanbanItems = upcomingItems.length + movingItems.length + recentOutcomeItems.length > 0;

    return (
        <>
            {!isLoading ? (
                <FirstWinChecklist
                    podId={podId}
                    agentCount={agents.length}
                    workflowCount={workflows.length}
                    conversationCount={conversations.length}
                />
            ) : null}
            <div className="mt-8 w-full space-y-6">
                <PodJoinRequestsHomePanel podId={podId} />
                <PodSurfacesHomePanel podId={podId} />
            {isLoading || hasKanbanItems ? (
                <section className="pod-home-work-section">
                    <div className="pod-home-work-heading flex items-center justify-between gap-4">
                        <h2 className="pod-home-work-title">Activity</h2>
                        <div className="pod-home-work-live-pill">
                            {isLoading ? (
                                <Loader2 className="h-3 w-3 animate-spin" />
                            ) : movingItems.length > 0 ? (
                                <span className="pod-home-work-live-dot" />
                            ) : null}
                            <span>
                                {movingItems.length > 0 ? `${movingItems.length} running · ` : ''}
                                {schedules.length} scheduled
                            </span>
                        </div>
                    </div>

                    <div className="pod-home-work-panel">
                        {upcomingItems.length > 0 ? (
                            <div className="pod-home-work-section-row">
                                <p className="pod-home-work-section-label">Upcoming</p>
                                <div className="pod-home-work-list">
                                    {upcomingItems.map((item) => (
                                        <KanbanCard key={item.id} item={item} />
                                    ))}
                                </div>
                            </div>
                        ) : null}

                        {movingItems.length > 0 ? (
                            <div className="pod-home-work-section-row">
                                <p className="pod-home-work-section-label">Working now</p>
                                <div className="pod-home-work-list">
                                    {movingItems.map((item) => (
                                        <KanbanCard key={item.id} item={item} />
                                    ))}
                                </div>
                            </div>
                        ) : null}

                        {recentOutcomeItems.length > 0 ? (
                            <div className="pod-home-work-section-row">
                                <p className="pod-home-work-section-label">Recent outcomes</p>
                                <div className="pod-home-work-list">
                                    {recentOutcomeItems.map((item) => (
                                        <KanbanCard key={item.id} item={item} />
                                    ))}
                                </div>
                            </div>
                        ) : null}
                    </div>
                </section>
            ) : null}

                {!isLoading || hasKanbanItems ? <PodRecipesHomePanel podId={podId} /> : null}
            </div>
        </>
    );
}

const SURFACE_META: Record<string, { label: string; logo: string }> = {
    SLACK: { label: 'Slack', logo: '/surfaces/slack.png' },
    TEAMS: { label: 'Teams', logo: '/surfaces/teams.png' },
    GMAIL: { label: 'Gmail', logo: '/surfaces/gmail.png' },
    OUTLOOK: { label: 'Outlook', logo: '/surfaces/outlook.png' },
    TELEGRAM: { label: 'Telegram', logo: '/surfaces/telegram.png' },
    WHATSAPP: { label: 'WhatsApp', logo: '/surfaces/whatsapp.png' },
};

const SURFACE_STATUS_TONE: Record<'success' | 'warning' | 'danger' | 'muted', { text: string; dot: string }> = {
    success: { text: 'text-[var(--state-success)]', dot: 'bg-[var(--state-success)]' },
    warning: { text: 'text-[var(--state-warning)]', dot: 'bg-[var(--state-warning)]' },
    danger: { text: 'text-[var(--state-error)]', dot: 'bg-[var(--state-error)]' },
    muted: { text: 'text-[var(--text-tertiary)]', dot: 'bg-[var(--text-tertiary)]' },
};

function surfaceStatusView(status?: string | null): { label: string; tone: 'success' | 'warning' | 'danger' | 'muted' } {
    const raw = String(status || '').toUpperCase();
    if (raw === 'ACTIVE') return { label: 'Live', tone: 'success' };
    if (raw === 'PENDING_ADMIN_CONSENT') return { label: 'Needs consent', tone: 'warning' };
    if (raw === 'ERROR') return { label: 'Error', tone: 'danger' };
    return { label: 'Paused', tone: 'muted' };
}

function surfaceAddress(surface: AssistantSurface): string {
    const channel = surface.config?.channels?.[0];
    return (channel?.channel_name || channel?.channel_id || surface.surface_identity_username || '').trim();
}

// "Reachable at" — the inbound twin of "Your apps". Surfaces shown as relationships
// (channel → who answers → live), not as a platform config grid. A real callout
// invites the first connection when nothing is wired up yet.
function PodSurfacesHomePanel({ podId }: { podId: string }) {
    const podAccess = usePodAccess(podId);
    const canUse = podAccess.canAccessRoute('surfaces');
    const { data: surfaces = [], isLoading } = usePodSurfaces(canUse ? podId : undefined);
    const surfacesHref = `/pod/${podId}/surfaces`;

    if (!canUse) return null;

    if (isLoading) {
        return (
            <section className="pod-home-surfaces-panel w-full" data-state="loading" role="status" aria-label="Loading surfaces">
                <div className="flex items-center justify-between gap-3">
                    <h2 className="text-base font-normal text-[var(--text-secondary)]">Surfaces</h2>
                    <div className="lemma-skeleton h-3 w-12 rounded-md" />
                </div>
                <ul className="mt-2 flex flex-col gap-0.5" aria-hidden="true">
                    {[0, 1, 2].map((item) => (
                        <li key={item} className="flex items-center gap-3 px-2 py-2">
                            <div className="lemma-skeleton h-8 w-8 shrink-0 rounded-lg" />
                            <div className="min-w-0 flex-1 space-y-2">
                                <div className="lemma-skeleton h-3 w-40 max-w-full rounded-md" />
                                <div className="lemma-skeleton h-2.5 w-24 rounded-md" />
                            </div>
                            <div className="lemma-skeleton h-3 w-10 rounded-md" />
                        </li>
                    ))}
                </ul>
            </section>
        );
    }

    if (surfaces.length === 0) {
        return (
            <section className="pod-home-surfaces-panel w-full" data-state="ready">
                <h2 className="text-base font-normal text-[var(--text-secondary)]">Surfaces</h2>
                <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2">
                    <span className="flex items-center gap-1.5">
                        {(['SLACK', 'GMAIL', 'TELEGRAM'] as const).map((key) => (
                            <span
                                key={key}
                                className="surface-logo-chip flex h-7 w-7 items-center justify-center rounded-md"
                            >
                                <Image src={SURFACE_META[key].logo} alt={SURFACE_META[key].label} width={16} height={16} className="object-contain" />
                            </span>
                        ))}
                    </span>
                    <p className="text-sm leading-6 text-[var(--text-secondary)]">
                        No surfaces yet —{' '}
                        <Link
                            href={surfacesHref}
                            className="custom-focus-ring font-medium text-[var(--text-primary)] underline-offset-2 hover:underline"
                        >
                            connect a surface
                        </Link>{' '}
                        so this pod can answer messages where your team already works.
                    </p>
                </div>
            </section>
        );
    }

    const sorted = [...surfaces].sort(
        (a, b) => (b.status === 'ACTIVE' ? 1 : 0) - (a.status === 'ACTIVE' ? 1 : 0),
    );

    return (
        <section className="pod-home-surfaces-panel w-full" data-state="ready">
            <div className="flex items-center justify-between gap-3">
                <h2 className="text-base font-normal text-[var(--text-secondary)]">Surfaces</h2>
                <Link
                    href={surfacesHref}
                    className="custom-focus-ring shrink-0 text-sm font-medium text-[var(--text-tertiary)] transition-colors hover:text-[var(--text-primary)]"
                >
                    Manage
                </Link>
            </div>
            <ul className="pod-home-surfaces-list mt-2 flex flex-col gap-0.5">
                {sorted.map((surface) => {
                    const platform = String(surface.platform || '').toUpperCase();
                    const meta = SURFACE_META[platform];
                    const label = meta?.label || formatDisplayName(platform);
                    const status = surfaceStatusView(surface.status);
                    const tone = SURFACE_STATUS_TONE[status.tone];
                    const address = surfaceAddress(surface);
                    const responder = surface.agent_name?.trim() || 'Pod default';

                    return (
                        <li key={surface.id}>
                            <Link
                                href={surfacesHref}
                                className="group flex items-center gap-3 rounded-lg px-2 py-2 transition-colors hover:bg-[color:color-mix(in_srgb,var(--surface-2)_55%,transparent)]"
                            >
                                <span className={cn(
                                    'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg',
                                    meta?.logo
                                        ? 'surface-logo-chip'
                                        : 'border border-[color:color-mix(in_srgb,var(--border-subtle)_50%,transparent)] bg-[var(--surface-2)]'
                                )}>
                                    {meta?.logo ? (
                                        <Image src={meta.logo} alt="" width={16} height={16} className="object-contain" />
                                    ) : (
                                        <MessageCircle className="h-4 w-4 text-[var(--text-secondary)]" />
                                    )}
                                </span>
                                <div className="min-w-0 flex-1">
                                    <p className="truncate text-sm text-[var(--text-primary)]">
                                        <span className="font-normal">{label}</span>
                                        {address ? <span className="text-[var(--text-tertiary)]"> · {address}</span> : null}
                                    </p>
                                    <p className="truncate text-xs text-[var(--text-secondary)]">
                                        {responder} answers
                                    </p>
                                </div>
                                <span className={cn('inline-flex shrink-0 items-center gap-1.5 text-xs', tone.text)}>
                                    <span className={cn('h-1.5 w-1.5 rounded-full', tone.dot)} />
                                    {status.label}
                                </span>
                            </Link>
                        </li>
                    );
                })}
            </ul>
        </section>
    );
}

function PodRecipesHomePanel({ podId }: { podId: string }) {
    const { launchRecipe } = useLaunchRecipe(podId);
    const featured = featuredRecipes.slice(0, 3);

    if (featured.length === 0) return null;

    return (
        <section className="w-full">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
                <div className="max-w-2xl">
                    <p className="type-eyebrow-mono">
                        Recipes
                    </p>
                    <h2 className="mt-2 text-lg font-medium text-[var(--text-primary)]">
                        Fastest way to feel this pod work
                    </h2>
                    <p className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">
                        A recipe adds capability in minutes — from a one-line prompt the assistant builds, to a bot you message, to a full kit.
                    </p>
                </div>
                <Link
                    href={`/pod/${podId}/recipes`}
                    className="custom-focus-ring inline-flex h-9 items-center justify-center gap-2 rounded-md border border-[color:var(--button-secondary-border)] bg-[var(--button-secondary-bg)] px-3 text-sm font-medium text-[var(--button-secondary-fg)] transition-colors hover:border-[var(--field-border-hover)] hover:bg-[var(--button-secondary-bg-hover)]"
                >
                    All recipes
                    <ArrowRight className="h-4 w-4" />
                </Link>
            </div>

            <div className="mt-4 grid gap-3 lg:grid-cols-3">
                {featured.map((recipe) => (
                    <RecipeFeatureCard
                        key={recipe.id}
                        podId={podId}
                        recipe={recipe}
                        onLaunch={() => launchRecipe(recipe)}
                    />
                ))}
            </div>
        </section>
    );
}

function KanbanCard({ item }: { item: KanbanItem }) {
    return (
        <Link
            href={item.href}
            className="group flex items-center gap-2.5 rounded-md px-2 py-1.5 transition-colors hover:bg-[color:color-mix(in_srgb,var(--surface-2)_50%,transparent)]"
        >
            <span
                className={cn(
                    'h-1.5 w-1.5 shrink-0 rounded-full',
                    item.statusTone === 'live' && 'animate-pulse',
                    kanbanDotClass(item.statusTone),
                )}
                aria-hidden="true"
            />
            <span className="min-w-0 flex-1 truncate text-sm text-[var(--text-primary)]">
                {item.title}
            </span>
            <span className="max-w-[55%] shrink-0 truncate text-xs text-[var(--text-tertiary)]">
                {item.detail}
            </span>
        </Link>
    );
}

function conversationToAgentItem(
    conversation: Conversation,
    agentsByNameOrId: Map<string, { id?: string; name: string; icon_url?: string | null; description?: string | null }>,
    podId: string,
    tone: KanbanItem['statusTone']
): KanbanItem {
    const scopedConversation = conversation as Conversation & {
        agent_name?: string | null;
        agent_id?: string | null;
        assistant_name?: string | null;
        assistant_id?: string | null;
    };
    const agentKey = scopedConversation.agent_name || scopedConversation.agent_id || scopedConversation.assistant_name || scopedConversation.assistant_id || '';
    const agent = agentKey ? agentsByNameOrId.get(agentKey) : undefined;
    const failed = tone === 'danger';

    return {
        id: `agent-conversation-${conversation.id}`,
        kind: 'agent',
        title: formatDisplayName(agent?.name || agentKey || conversation.title || 'Agent run'),
        detail: failed
            ? `Failed ${formatRelativeTime(conversation.updated_at || conversation.created_at)}.`
            : tone === 'live'
                ? conversation.title || 'Conversation is running.'
                : `Completed ${formatRelativeTime(conversation.updated_at || conversation.created_at)}.`,
        href: `/pod/${podId}/conversations/${encodeURIComponent(conversation.id)}`,
        status: failed ? 'Failed' : tone === 'live' ? 'Running' : 'Completed',
        statusTone: tone,
        iconUrl: agent?.icon_url,
    };
}

function getScheduleHref(podId: string, schedule: { workflow_name?: string | null; agent_name?: string | null }, agentName?: string, workflowName?: string) {
    if (agentName || schedule.agent_name) return `/pod/${podId}/agents/${encodeURIComponent(agentName || schedule.agent_name || '')}`;
    if (workflowName || schedule.workflow_name) return `/pod/${podId}/flows/${encodeURIComponent(workflowName || schedule.workflow_name || '')}`;
    return `/pod/${podId}/schedules`;
}

function formatRelativeTime(value: string | null | undefined) {
    const timestamp = value ? Date.parse(value) : NaN;
    if (!Number.isFinite(timestamp)) return 'recently';
    const diffMs = Date.now() - timestamp;
    const diffMinutes = Math.max(0, Math.round(diffMs / 60000));
    if (diffMinutes < 1) return 'just now';
    if (diffMinutes < 60) return `${diffMinutes}m ago`;
    const diffHours = Math.round(diffMinutes / 60);
    if (diffHours < 24) return `${diffHours}h ago`;
    const diffDays = Math.round(diffHours / 24);
    return `${diffDays}d ago`;
}

function kanbanDotClass(tone: KanbanItem['statusTone']) {
    if (tone === 'danger') return 'bg-[var(--state-error)]';
    if (tone === 'warning') return 'bg-[var(--delight)]';
    if (tone === 'success') return 'bg-[var(--state-success)]';
    if (tone === 'live') return 'bg-[var(--state-info)]';
    return 'bg-[var(--text-tertiary)]';
}

function formatDisplayName(value: string | null | undefined) {
    const cleaned = (value || '')
        .replace(/[_-]+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();

    if (!cleaned) return 'Untitled';

    return cleaned;
}

export default function PodPage({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    const { id: podId } = use(params);
    const { isLoading: isLoadingPod } = usePod(podId);

    return (
        <ProtectedRoute>
            {isLoadingPod ? (
                <div className="flex h-full items-center justify-center">
                    <StepLoader size="sm" />
                </div>
            ) : (
                <PodBlankChatHome podId={podId} />
            )}
        </ProtectedRoute>
    );
}
