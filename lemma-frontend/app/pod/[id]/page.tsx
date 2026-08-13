'use client';

import { use, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { ArrowRight, ArrowUp, ChevronDown, ChevronUp, Plus, UserPlus, X } from '@/components/ui/icons';

import { useAIAssistant } from '@/components/ai/ai-assistant-context';
import { ProjectBranchChip } from '@/components/lemma/assistant/project-branch';
import { ProjectPicker } from '@/components/lemma/assistant/project-picker';
import { useGithubProjects } from '@/lib/hooks/use-github-projects';
import { ProtectedRoute } from '@/components/auth/protected-route';
import { resolveDefaultAgentRuntime } from '@/components/agents/agent-runtime-helpers';
import { RuntimeModelPicker } from '@/components/lemma/assistant/model-picker';
import { PodNewWorkspace } from '@/components/pod/pod-new-workspace';
import { StarterThemePicker } from '@/components/recipes/starter-theme-card';
import { Button } from '@/components/ui/button';
import { FEATURED_STARTER_THEMES } from '@/lib/recipes/recipes';
import { useLaunchRecipe } from '@/lib/recipes/use-launch-recipe';
import { useAgents } from '@/lib/hooks/use-agents';
import { useAppPages } from '@/lib/hooks/use-app';
import { useScopedConversations } from '@/lib/hooks/use-assistants';
import { useAgentRuntimes } from '@/lib/hooks/use-agent-runtime';
import {
    normalizeWorkflowRunStatus,
    useFlows,
    useWorkflowRunSnapshots,
} from '@/lib/hooks/use-flows';
import {
    UNATTENDED_NOTIFICATION_STATUSES,
    useNotifications,
} from '@/lib/hooks/use-notifications';
import {
    buildNotificationHref,
    describeNotificationMeta,
    flattenNotificationBody,
} from '@/lib/notifications/notification-display';
import { usePod } from '@/lib/hooks/use-pods';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { usePodJoinRequests } from '@/lib/hooks/use-pod-join-requests';
import { usePodSurfaces } from '@/lib/hooks/use-pod-surfaces';
import { buildScopedConversationHref } from '@/lib/assistant/conversation-composer-context';
import { useSchedules } from '@/lib/hooks/use-schedules';
import { PodHomePresence } from '@/components/pod/pod-home-presence';
import { cn } from '@/lib/utils';
import { formatAgentName } from '@/lib/utils/agents';
import { isConversationRunningStatus, normalizeConversationStatus } from '@/lib/utils/conversations';
import { describeScheduleConfig, getScheduleTargetKind, getScheduleTargetName } from '@/lib/utils/schedules';
import {
    resolvePodHomeStarterMode,
    type PodHomeResourceSignals,
} from '@/lib/pods/pod-home-starters';
import { readComposerLaunch, stripComposerLaunchParams } from '@/lib/pods/composer-launch';
import type { AgentRuntimeConfig, Conversation } from '@/lib/types';
import { StepLoader } from '@/components/brand/loader';
import { Skeleton } from '@/components/shared/loading';

const RUNNING_RUN_STATUSES = new Set(['PENDING', 'RUNNING', 'EXECUTING', 'IN_PROGRESS', 'PROCESSING']);
const FAILED_RUN_STATUSES = new Set(['FAILED', 'ERROR', 'CANCELLED', 'CANCELED']);
const COMPLETED_RUN_STATUSES = new Set(['COMPLETED', 'SUCCESS', 'SUCCEEDED']);
const RECENT_CONVERSATION_STATUSES = new Set(['completed', 'complete', 'success', 'succeeded', 'failed', 'error']);
const COMPOSER_LAUNCH_DURATION_MS = 560;
const HOME_PANELS_DEFER_MS = 600;
const POD_HOME_STARTER_DISMISS_KEY = 'lemma:pod-home:starter-nudge';
/** How far back an outcome may be and still count as news on home. Without a
 *  cutoff the panel kept printing failures from a month and a half ago as if
 *  they had just happened. */
const RECENT_OUTCOME_MAX_AGE_MS = 14 * 24 * 60 * 60 * 1000;
/** A conversation started from a prompt has no title but its own first message,
 *  so the row was printing a whole build instruction. Clip it to a phrase. */
const OUTCOME_TITLE_MAX_LENGTH = 58;
/**
 * One conversation read for the whole page. Home used to make two — one at
 * `limit: 1` purely to answer "is this pod fresh?", and the activity region's at
 * `limit: 20` — which are the same list at two sizes, so they missed each
 * other's cache and cost two round trips.
 */
const POD_HOME_CONVERSATION_LIMIT = 100;
/** How many unattended notifications home prints before deferring to the page.
 *  Home is a glance at what is happening; a queue is a queue and has its own
 *  route. */
const POD_HOME_NOTIFICATION_LIMIT = 4;

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
    const pathname = usePathname();
    const searchParams = useSearchParams();
    const assistant = useAIAssistant();
    const podAccess = usePodAccess(podId);
    const { data: pod } = usePod(podId);
    const { data: runtimeCatalog } = useAgentRuntimes(pod?.organization_id);
    const canWriteConversations = podAccess.can('conversation.write');
    const githubProjects = useGithubProjects({ enabled: canWriteConversations });
    const canReadAgents = podAccess.can('agent.read');
    const canReadWorkflows = podAccess.can('workflow.read');
    const canReadSurfaces = podAccess.canAccessRoute('surfaces');
    const canReadConversations = podAccess.can('conversation.read');
    const { data: homeAgentsData, isLoading: isLoadingHomeAgents } = useAgents(canReadAgents ? podId : undefined);
    const { data: homeFlows = [], isLoading: isLoadingHomeFlows } = useFlows(canReadWorkflows ? podId : undefined);
    const { pages: homeAppPages, isLoading: isLoadingHomeApps } = useAppPages(podId);
    const { data: homeSurfaces = [], isLoading: isLoadingHomeSurfaces } = usePodSurfaces(canReadSurfaces ? podId : undefined);
    const { data: homeConversationsData, isLoading: isLoadingHomeConversations } = useScopedConversations(
        { podId },
        { limit: POD_HOME_CONVERSATION_LIMIT, enabled: canReadConversations },
    );
    const homeConversations = useMemo(() => homeConversationsData?.items || [], [homeConversationsData?.items]);
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
    // A composer launch arrives as URL params, is consumed once, and is stripped
    // from the URL. Refs rather than state because nothing renders from them and
    // a re-render mid-send must not drop the framing off the message in flight.
    const composerLaunchSeededRef = useRef(false);
    const launchInstructionsRef = useRef<string | null>(null);
    const launchMetadataRef = useRef<Record<string, unknown> | null>(null);

    const isLaunchingComposer = launchAnimation !== null;
    const isBlankingHome = isLaunchingComposer || isRouteHandoff;
    const isBusy = isSending || isBlankingHome || assistant.isLoading || assistant.isOpenedConversationRunning || assistant.isUploadingFiles;
    const canSend = canWriteConversations && draft.trim().length > 0 && !isBusy;
    const podDefaultRuntime = pod?.config?.default_runtime
        ?? resolveDefaultAgentRuntime(runtimeCatalog, pod?.config?.default_profile_id);
    const selectedCommandRuntime = assistant.conversationRuntime ?? null;
    const isLoadingHomeState =
        isLoadingHomeAgents ||
        isLoadingHomeFlows ||
        isLoadingHomeApps ||
        isLoadingHomeSurfaces ||
        isLoadingHomeConversations;
    const podHomeResourceSignals = useMemo<PodHomeResourceSignals>(() => ({
        appCount: homeAppPages.length,
        agentCount: homeAgentsData?.items?.length || 0,
        workflowCount: homeFlows.length,
        surfaceCount: homeSurfaces.length,
        activeSurfaceCount: homeSurfaces.filter((surface) => String(surface.status || '').toUpperCase() === 'ACTIVE').length,
        scheduleCount: 0,
        conversationCount: homeConversations.length,
        hasUsedWorkflow: false,
    }), [homeAgentsData?.items?.length, homeAppPages.length, homeConversations.length, homeFlows.length, homeSurfaces]);
    const starterMode = resolvePodHomeStarterMode(podHomeResourceSignals);
    const showStarterHome = podAccess.isBuilder && !isLoadingHomeState && starterMode === 'fresh';

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

    /** Caret after the stem's trailing space, so the next keystroke continues it. */
    const focusComposerAtEnd = () => {
        window.requestAnimationFrame(() => {
            const input = composerInputRef.current;
            if (!input) return;
            input.focus();
            input.setSelectionRange(input.value.length, input.value.length);
        });
    };

    /** A shortcut tile hands over an unfinished sentence; it never sends one. */
    const prepareComposerPrompt = (prompt: string) => {
        setDraft(prompt);
        focusComposerAtEnd();
    };

    // Arriving from a start path: the composer opens holding the start of a
    // sentence, cursor at the end, and nothing is sent. Once only — the params
    // come straight back off the URL so a refresh cannot re-seed a draft the
    // user already cleared.
    useEffect(() => {
        if (composerLaunchSeededRef.current) return;
        const launch = readComposerLaunch(searchParams);
        if (!launch) return;

        composerLaunchSeededRef.current = true;
        if (launch.draft) setDraft(launch.draft);
        launchInstructionsRef.current = launch.instructions ?? null;
        launchMetadataRef.current = launch.metadata ?? null;

        const nextQuery = stripComposerLaunchParams(searchParams);
        router.replace(nextQuery ? `${pathname}?${nextQuery}` : pathname);

        // After the replace, so the focus is not stolen by the re-render.
        focusComposerAtEnd();
    }, [pathname, router, searchParams]);

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
            await assistant.sendMessage(message, {
                forceNewConversation: true,
                instructions: launchInstructionsRef.current || undefined,
                conversationMetadata: launchMetadataRef.current ?? undefined,
            });
            // The framing belonged to the sentence they just finished, not to
            // the pod. Everything after this is an ordinary message.
            launchInstructionsRef.current = null;
            launchMetadataRef.current = null;
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
                    // Extra headroom when the composer is the hero: landing it a
                    // little above the optical centre reads as composed, where
                    // pinning it to the top read as a search bar.
                    "mx-auto flex min-h-full w-full max-w-6xl flex-1 flex-col items-center px-5 pb-10 sm:px-6",
                    showStarterHome ? "pt-8 md:pt-12" : "pt-12 md:pt-20",
                    isBlankingHome && "pointer-events-none opacity-0",
                )}
            >
                {showStarterHome ? (
                    <section className="w-full max-w-3xl">
                        <p className="type-eyebrow-mono text-[var(--text-tertiary)]">Start inside this pod</p>
                        <h1 className="mt-3 text-2xl font-medium leading-tight tracking-tight text-[var(--text-primary)] sm:text-3xl">
                            What should {pod?.name || 'this pod'} become?
                        </h1>
                    </section>
                ) : null}
                {/* The composer sits directly under the question it answers.
                    It used to be last, below a themed picker, a prompt list and
                    two captions explaining them — so the screen asked three
                    times and buried the one field that takes any answer. */}
                <div className={cn('w-full max-w-3xl', showStarterHome && 'mt-6')}>
                    {showStarterHome ? null : (
                        <p className="pod-home-eyebrow mb-3.5">{pod?.name || 'This pod'}</p>
                    )}
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
                            "pod-home-composer transition-opacity duration-150",
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
                            placeholder={canWriteConversations
                                ? showStarterHome
                                    ? "Describe the app, agent, or workflow you want..."
                                    : "What should happen next?"
                                : "You can read this pod, but not start new conversations."}
                            disabled={!canWriteConversations}
                            className="inline-edit-field min-h-10 flex-1 resize-none bg-transparent py-3 text-base leading-6 text-[var(--text-primary)] outline-none placeholder:text-[var(--text-tertiary)]"
                        />
                        <RuntimeModelPicker
                            catalog={runtimeCatalog}
                            defaultRuntime={podDefaultRuntime}
                            value={selectedCommandRuntime}
                            onChange={handleCommandRuntimeChange}
                            disabled={!canWriteConversations}
                            compact
                            triggerLabelClassName="hidden sm:block"
                            scopeHint="Just for this chat"
                            manageHref={pod?.organization_id ? `/organizations/${pod.organization_id}/settings/agent-runtimes` : undefined}
                        />
                        {canWriteConversations ? (
                            <ProjectPicker
                                value={assistant.pendingProject}
                                onChange={assistant.setPendingProject}
                                projects={githubProjects.projects}
                                isConnected={githubProjects.isConnected}
                                isLoadingProjects={githubProjects.isLoadingProjects}
                                error={githubProjects.error}
                                accountId={githubProjects.accountId}
                                connectHref={`/pod/${encodeURIComponent(podId)}/connectors`}
                            />
                        ) : null}
                        {canWriteConversations && assistant.pendingProject ? (
                            <ProjectBranchChip
                                project={assistant.pendingProject}
                                onChange={(ref) => assistant.setPendingProject({
                                    ...assistant.pendingProject!,
                                    ref,
                                })}
                            />
                        ) : null}
                        <button
                            type="submit"
                            aria-label="Send"
                            disabled={!canSend}
                            className="pod-home-send-button custom-focus-ring inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--action-primary)] text-[var(--text-on-brand)] transition-colors hover:bg-[var(--action-primary-hover)] disabled:bg-[var(--surface-2)] disabled:text-[var(--text-tertiary)]"
                        >
                            {isBusy ? <StepLoader size="sm" /> : <ArrowUp className="h-4 w-4" />}
                        </button>
                    </form>
                    {/* The room, directly under the box you type into: who is here,
                        human and agent, and what is already on duty. */}
                    {isLoadingHomeState || showStarterHome ? null : (
                        <div className="mt-6">
                            <PodHomePresence podId={podId} conversations={homeConversations} />
                        </div>
                    )}
                </div>
                {/* The same launcher the new-conversation screen uses, in the same
                    place relative to the composer: shortcuts under the box you
                    type in. Home used to grow its own for the identical job —
                    taller themed tabs, a second prompt list, and two captions
                    explaining both — which is what made this read as three
                    screens asking the same question. */}
                {showStarterHome ? (
                    <section className="mt-8 w-full max-w-3xl">
                        <PodNewWorkspace
                            podId={podId}
                            selectedAgentName={null}
                            onPreparePrompt={prepareComposerPrompt}
                            onSelectAgent={(agentName) =>
                                router.push(buildScopedConversationHref({
                                    podId,
                                    conversationId: 'new',
                                    agentName,
                                }))
                            }
                            placement="below-composer"
                        />
                    </section>
                ) : null}
                {/* Nothing until we know which home this is. A fresh pod shows the
                    starter section above and no activity region at all, so drawing
                    the activity skeleton first promised a panel that never arrived
                    and then took it away again. Once `isLoadingHomeState` settles
                    the region either exists or it does not, and from there it only
                    ever goes skeleton → content. */}
                {isLoadingHomeState || showStarterHome ? null : (
                    <div className="mt-10 w-full max-w-3xl">
                        {showHomePanels
                            ? <PodAgentWorkflowKanban podId={podId} baseResourceSignals={podHomeResourceSignals} conversations={homeConversations} />
                            : <PodHomePanelsSkeleton />}
                    </div>
                )}
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
                            <StepLoader size="sm" />
                        </span>
                    </div>
                </div>
            ) : null}
        </div>
    );
}

/**
 * The Activity section, waiting.
 *
 * Rendered twice on the way in and it has to be the same both times: once while
 * the panels are deliberately deferred so the composer paints first, and again
 * by the kanban itself while its five queries land. Those used to be different
 * looks — a skeleton, then a real heading over an empty panel with a spinner in
 * the status pill — which is most of why this page felt like it loaded three or
 * four times.
 *
 * It no longer draws a placeholder for the join-requests panel above it: that
 * panel renders for almost no one, so the placeholder was a box that appeared
 * and then vanished on nearly every visit.
 */
function PodHomeActivitySkeleton() {
    return (
        <div className="space-y-3" role="status" aria-label="Loading pod activity">
            <div className="pod-home-work-heading flex items-center justify-between gap-4">
                <Skeleton shape="block" className="h-4 w-20" />
                <Skeleton className="h-3 w-24" />
            </div>
            <div className="pod-home-work-panel">
                {[0, 1, 2].map((item) => (
                    <div key={item} className="pod-home-work-section-row space-y-2" data-skeleton="true">
                        <Skeleton className="h-2.5 w-24" />
                        <div className="flex items-center gap-3 py-1">
                            <Skeleton shape="circle" className="h-1.5 w-1.5" />
                            <Skeleton className="h-3 w-36" />
                            <Skeleton className="ml-auto h-3 w-28" />
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}

function PodHomePanelsSkeleton() {
    return (
        <div className="mt-8 w-full space-y-6">
            <PodHomeActivitySkeleton />
        </div>
    );
}

function PodJoinRequestsHomePanel({ podId }: { podId: string }) {
    const podAccess = usePodAccess(podId);
    const canManageMembers = podAccess.can('pod.member.manage');
    // Pod home renders for everyone, so this has to stay off the wire until the
    // permission is known. `can()` is false while permissions are still loading,
    // which is the answer we want: ask once, after we know we are allowed to.
    const { data, isLoading } = usePodJoinRequests(podId, 'PENDING', {
        enabled: canManageMembers,
    });
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

function PodAgentWorkflowKanban({
    podId,
    baseResourceSignals,
    conversations,
}: {
    podId: string;
    baseResourceSignals: PodHomeResourceSignals;
    /** Read once by the page and handed down, so this panel and the band and
     *  the presence line all describe the same list. */
    conversations: Conversation[];
}) {
    const podAccess = usePodAccess(podId);
    const canReadAgents = podAccess.can('agent.read');
    const canReadWorkflows = podAccess.can('workflow.read');
    const canReadSchedules = podAccess.can('schedule.read');
    const { data: agentsData, isLoading: loadingAgents } = useAgents(canReadAgents ? podId : undefined);
    const { data: workflowsData = [], isLoading: loadingWorkflows } = useFlows(canReadWorkflows ? podId : undefined);
    const { data: schedulesData, isLoading: loadingSchedules } = useSchedules(canReadSchedules ? podId : undefined, { isActive: true, limit: 12 });
    // Scoped to OPEN by the server rather than filtered here, so home reads a
    // short list instead of pulling a history it throws away. Notifications are
    // addressed to one person and gated on membership, so there is no capability
    // to check — everyone who can see this page can be asked something on it.
    const { data: notificationsData, isLoading: loadingNotifications } = useNotifications(podId, {
        limit: POD_HOME_NOTIFICATION_LIMIT,
        status: UNATTENDED_NOTIFICATION_STATUSES,
    });
    const unattendedNotifications = useMemo(
        () => (notificationsData?.items || []).slice(0, POD_HOME_NOTIFICATION_LIMIT),
        [notificationsData?.items],
    );

    const agents = useMemo(() => agentsData?.items || [], [agentsData?.items]);
    const workflows = useMemo(() => workflowsData || [], [workflowsData]);
    const schedules = useMemo(() => schedulesData?.items || [], [schedulesData?.items]);
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
                if (!FAILED_RUN_STATUSES.has(status) && !COMPLETED_RUN_STATUSES.has(status)) return false;
                return isRecentOutcome(run.completed_at || run.updated_at || run.created_at);
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
            .filter((conversation) => isRecentOutcome(conversation.updated_at || conversation.created_at))
            .slice(0, Math.max(0, 5 - workflowOutcomes.length))
            .map((conversation) => {
                const status = normalizeConversationStatus(conversation.status);
                const failed = status === 'failed' || status === 'error';
                return conversationToAgentItem(conversation, agentsByNameOrId, podId, failed ? 'danger' : 'success');
            });

        return [...workflowOutcomes, ...agentOutcomes].slice(0, 5);
    }, [agentsByNameOrId, conversations, podId, runSnapshots]);

    const isLoading = loadingAgents || loadingWorkflows || loadingSchedules || loadingRuns || loadingNotifications;
    const hasKanbanItems =
        unattendedNotifications.length + upcomingItems.length + movingItems.length + recentOutcomeItems.length > 0;
    const hasUsedWorkflow = runSnapshots.some((snapshot) => snapshot.runs.some((run) => {
        const status = normalizeWorkflowRunStatus(run.status);
        return RUNNING_RUN_STATUSES.has(status) || COMPLETED_RUN_STATUSES.has(status);
    }));
    const starterMode = resolvePodHomeStarterMode({
        ...baseResourceSignals,
        agentCount: agents.length,
        workflowCount: workflows.length,
        scheduleCount: schedules.length,
        conversationCount: conversations.length,
        hasUsedWorkflow,
    });
    const showFormingNudge = podAccess.isBuilder && !isLoading && starterMode === 'forming';

    return (
        <>
            <div className="mt-8 w-full space-y-6">
                <PodJoinRequestsHomePanel podId={podId} />
                {/* The same skeleton the deferred region just showed, so the hand-off
                    from "not mounted yet" to "mounted and fetching" is invisible.
                    It used to render the real heading over an empty panel with a
                    spinner in the status pill — a third look, and one that stated
                    "0 scheduled" before it knew. */}
                {isLoading ? (
                    <PodHomeActivitySkeleton />
                ) : hasKanbanItems ? (
                    <section className="pod-home-work-section">
                        <div className="pod-home-work-heading flex items-center justify-between gap-4">
                            <h2 className="pod-home-work-title">Activity</h2>
                            <div className="pod-home-work-live-pill">
                                {movingItems.length > 0 ? (
                                    <span className="pod-home-work-live-dot" />
                                ) : null}
                                <span>
                                    {unattendedNotifications.length > 0
                                        ? `${unattendedNotifications.length} waiting on you · `
                                        : ''}
                                    {movingItems.length > 0 ? `${movingItems.length} running · ` : ''}
                                    {schedules.length} scheduled
                                </span>
                            </div>
                        </div>

                        <div className="pod-home-work-panel">
                            {/* First in the panel, above everything the pod is
                                doing on its own: these are the only rows that
                                are blocked on a person. One line each — the
                                title and when it arrived — because anything
                                longer turns four of them into a wall, and the
                                body is one click away on the row's own page. */}
                            {unattendedNotifications.length > 0 ? (
                                <div className="pod-home-work-section-row">
                                    <p className="pod-home-work-section-label">Needs you</p>
                                    <div className="pod-home-work-list">
                                        {unattendedNotifications.map((notification) => (
                                            <Link
                                                key={notification.id}
                                                href={buildNotificationHref(podId, notification.id)}
                                                className="pod-home-notification-row group"
                                            >
                                                <span
                                                    className={cn(
                                                        'notification-row-dot',
                                                        notification.awaiting_response
                                                            ? 'bg-[var(--action-primary)]'
                                                            : 'bg-[var(--text-tertiary)]',
                                                    )}
                                                    aria-hidden="true"
                                                />
                                                <span className="pod-home-notification-title">
                                                    {notification.title}
                                                </span>
                                                {/* The title is the topic and a
                                                    topic repeats; the body is
                                                    what tells two of these
                                                    apart. */}
                                                <span className="pod-home-notification-preview">
                                                    {flattenNotificationBody(notification.body)}
                                                </span>
                                                <span className="pod-home-notification-meta">
                                                    {describeNotificationMeta(notification)}
                                                </span>
                                            </Link>
                                        ))}
                                    </div>
                                </div>
                            ) : null}

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
                {showFormingNudge ? <PodRecipesHomeNudge podId={podId} /> : null}
            </div>
        </>
    );
}

function PodRecipesHomeNudge({ podId }: { podId: string }) {
    const themes = FEATURED_STARTER_THEMES;
    const { launchRecipe } = useLaunchRecipe(podId);
    const [expanded, setExpanded] = useState(false);
    const [dismissed, setDismissed] = useState<boolean | null>(null);
    const storageKey = `${POD_HOME_STARTER_DISMISS_KEY}:${podId}`;

    useEffect(() => {
        let nextDismissed = false;
        try {
            nextDismissed = window.localStorage.getItem(storageKey) === '1';
        } catch {}
        const frame = window.requestAnimationFrame(() => setDismissed(nextDismissed));
        return () => window.cancelAnimationFrame(frame);
    }, [storageKey]);

    const dismiss = () => {
        try {
            window.localStorage.setItem(storageKey, '1');
        } catch {
            // The in-memory dismissal still works when browser storage is unavailable.
        }
        setDismissed(true);
    };

    if (themes.length === 0 || dismissed !== false) return null;

    return (
        <section className="pod-home-starter-nudge w-full" data-expanded={expanded ? 'true' : 'false'}>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
                <span className="pod-home-starter-nudge-icon">
                    <Plus className="h-4 w-4" />
                </span>
                <div className="min-w-0 flex-1">
                    <h2 className="text-sm font-medium text-[var(--text-primary)]">Add another capability</h2>
                    <p className="mt-0.5 truncate text-xs text-[var(--text-tertiary)]">Dashboards, surface agents, knowledge, intake, and automations.</p>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                    <Button
                        type="button"
                        variant="quiet"
                        size="sm"
                        onClick={() => setExpanded((value) => !value)}
                        aria-expanded={expanded}
                        className="h-8 gap-1.5 px-2.5 text-xs"
                    >
                        {expanded ? 'Hide ideas' : 'Show ideas'}
                        {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                    </Button>
                    <Link
                        href={`/pod/${podId}/recipes`}
                        className="custom-focus-ring inline-flex h-8 items-center gap-1 rounded-md px-2.5 text-xs text-[var(--text-tertiary)] transition-colors hover:text-[var(--text-primary)]"
                    >
                        Browse all
                        <ArrowRight className="h-3.5 w-3.5" />
                    </Link>
                    <Button
                        type="button"
                        variant="quiet"
                        size="icon"
                        onClick={dismiss}
                        aria-label="Dismiss starter suggestions for this pod"
                        title="Dismiss"
                        className="h-8 w-8 text-[var(--text-tertiary)]"
                    >
                        <X className="h-3.5 w-3.5" />
                    </Button>
                </div>
            </div>

            {expanded ? (
                <div className="mt-3 border-t border-[var(--border-subtle)] pt-3">
                    <StarterThemePicker
                        themes={themes}
                        onLaunch={(recipe, message) => launchRecipe(recipe, { message })}
                    />
                </div>
            ) : null}
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
                    item.statusTone === 'live' && 'lemma-live-pulse',
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
        // The agent's name first. A conversation started from the composer has
        // no title but its own opening message, so falling straight through to
        // `conversation.title` printed a whole build instruction as the row
        // title — clipped, mid-word, six weeks after anyone cared.
        title: agent?.name || agentKey
            ? formatDisplayName(agent?.name || agentKey)
            : clipOutcomeTitle(conversation.title) || 'Agent run',
        detail: failed
            ? `Failed ${formatRelativeTime(conversation.updated_at || conversation.created_at)}.`
            : tone === 'live'
                ? clipOutcomeTitle(conversation.title) || 'Conversation is running.'
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
    // A trigger with no resolvable target only exists on the pod-wide ledger.
    return `/pod/${podId}/settings/automation`;
}

/** Whether an outcome is still news. Old enough and it belongs on the resource's
 *  own page, not on home. */
function isRecentOutcome(value: string | null | undefined) {
    const timestamp = value ? Date.parse(value) : NaN;
    // An undated row is kept: dropping it would hide a real outcome over a
    // missing field, and the relative-time formatter already says "recently".
    if (!Number.isFinite(timestamp)) return true;
    return Date.now() - timestamp <= RECENT_OUTCOME_MAX_AGE_MS;
}

/** A title that is really a prompt, cut to a phrase at a word boundary. */
function clipOutcomeTitle(value: string | null | undefined) {
    const cleaned = (value || '').replace(/\s+/g, ' ').trim();
    if (!cleaned) return '';
    if (cleaned.length <= OUTCOME_TITLE_MAX_LENGTH) return cleaned;
    const head = cleaned.slice(0, OUTCOME_TITLE_MAX_LENGTH);
    const lastSpace = head.lastIndexOf(' ');
    return `${(lastSpace > 24 ? head.slice(0, lastSpace) : head).replace(/[,.;:]$/, '')}…`;
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

    // No gate on `usePod` here. It used to blank the entire page behind a centred
    // loader while that query resolved — but nothing on this branch read the pod
    // record, the shell above has already resolved it, and the composer is static
    // markup that can paint immediately. The one thing that genuinely waits is
    // the activity region, and it says so itself.
    return (
        <ProtectedRoute>
            <PodBlankChatHome podId={podId} />
        </ProtectedRoute>
    );
}
