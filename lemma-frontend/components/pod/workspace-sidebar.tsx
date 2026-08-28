'use client';

import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
    AppWindow,
    Check,
    ChevronDown,
    ChevronsUpDown,
    Home,
    PanelLeftClose,
    Plus,
    Search,
    Share2,
    Upload,
    X,
} from '@/components/ui/icons';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import { NotificationsBell } from '@/components/notifications/notifications-bell';
import { POD_DEFAULT_AGENT_SELECTOR } from 'lemma-sdk';

import { useAIAssistant } from '@/components/ai/ai-assistant-context';
import { Logo } from '@/components/brand/logo';
import { LocalSettingsButton } from '@/components/desktop/local-settings-button';
import { ShareSheet } from '@/components/bundle/share-sheet';
import { ImportDialog } from '@/components/bundle/import-dialog';
import { ProductIcon, type ProductIconKind } from '@/components/pod/product-icon';
import { AccountMenu } from '@/components/shared/account-menu';
import { PodMark } from '@/components/pod/pod-mark';
import { ResourceIcon } from '@/components/shared/resource-icon';
import { ResourceIdentity } from '@/components/shared/resource-identity';
import { Button } from '@/components/ui/button';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { cn } from '@/lib/utils';
import { agentsQueryOptions, useAgents } from '@/lib/hooks/use-agents';
import { useAppPages } from '@/lib/hooks/use-app';
import { DEFAULT_RESPONDER_NAME, formatAgentName } from '@/lib/utils/agents';
import { appPageSlugFromRouteParam } from '@/lib/utils/app-page-slugs';
import {
    tableQueryOptions,
    tableRecordsQueryOptions,
    tablesQueryOptions,
} from '@/lib/hooks/use-datastores';
import { flowsQueryOptions } from '@/lib/hooks/use-flows';
import { useAccessiblePods, type AccessiblePod, type AccessiblePodGroup } from '@/lib/hooks/use-pods';
import { useScopedConversations } from '@/lib/hooks/use-assistants';
import { identityHueClass } from '@/lib/utils/resource-icon-value';
import { LEM_SEED } from '@/lib/identity/seeded-identity';
import {
    filterSidebarConversations,
    getConversationMark,
    mergeSidebarConversations,
    SIDEBAR_CONVERSATION_LIMIT,
    type ConversationMark,
} from '@/lib/assistant/sidebar-conversations';
import {
    filterSwitcherPodGroups,
    filterSwitcherPods,
    shouldShowPodFilter,
    toPodDisplayLabel,
} from '@/lib/pods/pod-switcher';
import {
    buildResourceCreationHref,
    type AssistantCreationKind,
} from '@/lib/pods/resource-creation';
import { getAppRecipeExamples } from '@/lib/recipes/recipes';
import type { Agent, Conversation } from '@/lib/types';
import { getConversationSignal } from '@/lib/utils/conversations';
import { Skeleton } from '@/components/shared/loading';

interface WorkspaceSidebarProps {
    podId: string;
    podName?: string;
    podIconUrl?: string | null;
    /**
     * When provided, the nav's own collapse control is rendered in the header.
     * This is the single nav toggle on desktop (paired with the rail's expand
     * button); the drawer passes this to close itself.
     */
    onCollapse?: () => void;
}

const DATASTORE_NAME = 'default';

/** The agents rail shows who works here, it is not the roster — the pod
 *  assistant plus a handful of faces, the rest one click away. Same answer
 *  the recents list makes, and the apps rail makes it for software. */
/** Which slice of the pod's history Recents is showing. */
type ConversationScope = 'assistant' | 'all' | 'agent';

const SIDEBAR_AGENT_LIMIT = 5;
const SIDEBAR_APP_LIMIT = 5;

/**
 * Whether the setup places are disclosed. Kept per-user rather than per-pod:
 * whether you are someone who builds workflows is a fact about you, not about
 * which pod you happen to have open.
 *
 * A module store rather than component state because the shell mounts this
 * sidebar twice — once inline, once inside the mobile drawer. Two copies of the
 * same preference would drift the moment the viewport crossed `md`.
 */
const SIDEBAR_MORE_STORAGE_KEY = 'lemma:sidebar-more-open';

const moreDisclosureListeners = new Set<() => void>();
let moreDisclosureCache: boolean | null = null;

function subscribeToMoreDisclosed(listener: () => void) {
    moreDisclosureListeners.add(listener);
    return () => {
        moreDisclosureListeners.delete(listener);
    };
}

function getMoreDisclosed() {
    if (moreDisclosureCache !== null) return moreDisclosureCache;

    try {
        moreDisclosureCache = window.localStorage.getItem(SIDEBAR_MORE_STORAGE_KEY) === '1';
    } catch {
        // localStorage can be unavailable in private or restricted browser contexts.
        moreDisclosureCache = false;
    }

    return moreDisclosureCache;
}

// Closed on the server and through hydration, so the first client render agrees
// with the markup it is adopting; the stored answer lands on the pass after.
function getMoreDisclosedOnServer() {
    return false;
}

function setMoreDisclosed(open: boolean) {
    moreDisclosureCache = open;

    try {
        window.localStorage.setItem(SIDEBAR_MORE_STORAGE_KEY, open ? '1' : '0');
    } catch {
        // localStorage can be unavailable in private or restricted browser contexts.
    }

    moreDisclosureListeners.forEach((listener) => listener());
}

const ASSISTANT_CREATION_COPY: Record<AssistantCreationKind, {
    title: string;
    description: string;
    prompt: string;
    placeholder: string;
    examples: string[];
    action: string;
    manualLabel?: string;
    iconKind: ProductIconKind;
}> = {
    agent: {
        title: 'New agent',
        description: 'Describe the job. Lemma will create the agent and show what changed.',
        prompt: 'What should this agent do?',
        placeholder: 'Review new support tickets, detect urgency, and draft the next response',
        examples: [
            'Triage support tickets and draft replies',
            'Watch deals and flag risky follow-ups',
        ],
        action: 'Create with assistant',
        manualLabel: 'Create manually',
        iconKind: 'agents',
    },
    app: {
        title: 'New app',
        description: 'Describe the operator surface. Lemma will create the app from the conversation.',
        prompt: 'What should this app help people do?',
        placeholder: 'Review renewals, see account risk, and approve the next customer action',
        examples: getAppRecipeExamples(3),
        action: 'Create app with assistant',
        iconKind: 'apps',
    },
    workflow: {
        title: 'New workflow',
        description: 'Describe the loop. Lemma will create a practical first version.',
        prompt: 'What should this workflow run?',
        placeholder: 'When a customer record changes, check risk and prepare follow-up',
        examples: [
            'Run a risk check when a customer changes',
            'Summarize new records every morning',
        ],
        action: 'Create with assistant',
        manualLabel: 'Create manually',
        iconKind: 'workflows',
    },
    table: {
        title: 'New table',
        description: 'Describe the data. Lemma will design the schema and create the table.',
        prompt: 'What should this table store?',
        placeholder: 'Project milestones with owner, date, risk, latest update, and next action',
        examples: [
            'Track project milestones and owners',
            'Store customer follow-ups and next actions',
        ],
        action: 'Create with assistant',
        manualLabel: 'Create manually',
        iconKind: 'tables',
    },
};

/**
 * The sidebar is the pod's activity spine: identity, one way to act, a fixed
 * set of places, and then the conversation history. Its shape never changes
 * with the route — resource lists belong to the page that owns them, not to a
 * second copy in the nav. The places sit above the history because they are the
 * part that must hold still; the history is the only thing here that stretches.
 */
export function WorkspaceSidebar({ podId, podName, podIconUrl, onCollapse }: WorkspaceSidebarProps) {
    const pathname = usePathname();
    const searchParams = useSearchParams();
    const router = useRouter();
    const queryClient = useQueryClient();
    const [assistantCreationKind, setAssistantCreationKind] = useState<AssistantCreationKind | null>(null);
    const [assistantCreationPrompt, setAssistantCreationPrompt] = useState('');
    const [bundleShareOpen, setBundleShareOpen] = useState(false);
    const [bundleImportOpen, setBundleImportOpen] = useState(false);
    const [podSwitcherOpen, setPodSwitcherOpen] = useState(false);
    const [conversationFilter, setConversationFilter] = useState('');
    const [filterOpen, setFilterOpen] = useState(false);
    /* The scope is *chosen*, and the choice is remembered against the page it
       was made on. Landing on an agent defaults Recents to that agent — asking
       "where do I see this one's conversations" and being handed the pod's whole
       history is the list refusing to answer the question the route just asked —
       and picking a different scope sticks until you navigate somewhere the
       choice no longer applies. Derived rather than an effect, so there is no
       frame where the list shows one scope and the control reads another. */
    const [scopeChoice, setScopeChoice] = useState<{
        scope: ConversationScope;
        forAgent: string | null;
    } | null>(null);
    const moreDisclosed = useSyncExternalStore(
        subscribeToMoreDisclosed,
        getMoreDisclosed,
        getMoreDisclosedOnServer,
    );
    const { data: podsData, isLoading: isLoadingPods } = useAccessiblePods({
        enabled: podSwitcherOpen,
    });
    const podAccess = usePodAccess(podId);
    const canUseConversations = podAccess.canAccessRoute('conversations');
    const canWriteConversations = podAccess.can('conversation.write');
    const canUseAgents = podAccess.canAccessRoute('agents');
    const canUseWorkflows = podAccess.canAccessRoute('workflows');
    const canUseConnectors = podAccess.canAccessRoute('connectors');
    const canUseData = podAccess.canAccessRoute('data');
    const canUseDocs = podAccess.canAccessRoute('files');
    const canUseApps = podAccess.canAccessRoute('apps');
    const canUsePodSettings = podAccess.canAccessRoute('settings');
    const canCreateAgents = podAccess.can('agent.create');
    const canCreateApps = podAccess.can('app.create');
    const canCreateWorkflows = podAccess.can('workflow.create');
    const canCreateTables = podAccess.can('datastore.table.create');
    const canUpdatePod = podAccess.can('pod.update');
    const basePath = `/pod/${podId}`;
    const isActive = (href: string) => pathname === href || pathname.startsWith(`${href}/`);

    // The agent whose page is open, if any. `/agents/<name>` only; `/agents/new`
    // has no conversations to scope to.
    const routeAgentName = useMemo(() => {
        const match = pathname.match(/^\/pod\/[^/]+\/agents\/([^/]+)$/);
        const name = match ? decodeURIComponent(match[1]) : null;
        return name && name !== 'new' ? name : null;
    }, [pathname]);
    const conversationScope: ConversationScope = scopeChoice && scopeChoice.forAgent === routeAgentName
        ? scopeChoice.scope
        : (routeAgentName ? 'agent' : 'assistant');
    const setConversationScope = (scope: ConversationScope) => {
        setScopeChoice({ scope, forAgent: routeAgentName });
    };
    const isConversationRoute = isActive(`${basePath}/conversations`);

    // The agents rail. This query is already warm — the Agents place prefetches
    // it on pointer-intent and home's presence row reads the same list — so the
    // rail renders straight from cache rather than growing a skeleton of its own.
    // One slot goes to Lem, so the faces cap a row shorter.
    //
    // Only the agents you can *talk to*. An agent that declares typed inputs is
    // something another resource calls with arguments — it belongs beside the
    // functions and workflows that call it, not in a cast of people you open a
    // conversation with. `takes_input` is the server's answer because the list
    // endpoint does not carry `input_schema`; when it is absent (older backend)
    // the filter fails open rather than emptying the rail.
    const { data: sidebarAgentsData } = useAgents(canUseAgents ? podId : undefined);
    const allSidebarAgents = useMemo(
        () => sidebarAgentsData?.items || [],
        [sidebarAgentsData?.items],
    );
    const sidebarAgents = useMemo(
        () => allSidebarAgents
            .filter((agent) => agent.takes_input !== true)
            .slice(0, SIDEBAR_AGENT_LIMIT - 1),
        [allSidebarAgents],
    );

    // Recents draw the face of whoever ran them, so the history needs to resolve
    // an agent id. It reads the *unfiltered* list: a conversation with a
    // structured agent is still a conversation you had, and hiding that agent
    // from the rail is not a reason to strip its face off the row.
    const agentsById = useMemo(() => {
        const byId = new Map<string, Agent>();
        allSidebarAgents.forEach((agent) => {
            if (agent.id) byId.set(agent.id, agent);
        });
        return byId;
    }, [allSidebarAgents]);

    const {
        conversations: controllerConversations,
        openedConversationId,
    } = useAIAssistant();
    const {
        data: conversationHistory,
        isLoading: isLoadingConversationHistory,
        refetch: refetchConversationHistory,
    } = useScopedConversations(
        {
            podId,
            agentName: conversationScope === 'agent'
                ? routeAgentName ?? undefined
                : conversationScope === 'assistant'
                    ? POD_DEFAULT_AGENT_SELECTOR
                    : undefined,
        },
        { limit: SIDEBAR_CONVERSATION_LIMIT, enabled: canUseConversations },
    );

    // Leaving a conversation is the moment the live copy of it stops being
    // live, and the moment the merge above stops trusting it. Nothing else
    // refreshes this list — it is not polled and does not refetch on focus — so
    // without this the row you just left would keep whatever it was doing when
    // you looked away. One list request per switch buys every row a truthful
    // resting state.
    const previouslyOpenedConversationIdRef = useRef<string | null | undefined>(undefined);
    useEffect(() => {
        const previous = previouslyOpenedConversationIdRef.current;
        previouslyOpenedConversationIdRef.current = openedConversationId;

        // `undefined` is the first pass, where the query is already in flight.
        if (previous === undefined || previous === openedConversationId) return;
        if (!canUseConversations) return;

        void refetchConversationHistory();
    }, [canUseConversations, openedConversationId, refetchConversationHistory]);
    // The controller can hold conversations the capped query missed, so trim
    // after merging — otherwise a brand new conversation could push the list
    // past the limit it is meant to hold.
    const scopedControllerConversations = useMemo(
        () => {
            if (conversationScope === 'assistant') {
                return controllerConversations.filter((conversation) => !conversation.agent_id);
            }
            // The controller holds ids; the route holds a name. Only the rows it
            // can actually attribute are kept — an unresolvable id under an agent
            // scope would be a conversation claiming to be this agent's on no
            // evidence, which is worse than a row arriving a refetch late.
            if (conversationScope === 'agent') {
                const scopedAgentId = allSidebarAgents
                    .find((agent) => agent.name === routeAgentName)?.id;
                if (!scopedAgentId) return [];
                return controllerConversations
                    .filter((conversation) => conversation.agent_id === scopedAgentId);
            }
            return controllerConversations;
        },
        [allSidebarAgents, controllerConversations, conversationScope, routeAgentName],
    );
    const conversations = useMemo(
        () => mergeSidebarConversations(
            conversationHistory?.items || [],
            scopedControllerConversations,
            openedConversationId,
        ).slice(0, SIDEBAR_CONVERSATION_LIMIT),
        [conversationHistory?.items, openedConversationId, scopedControllerConversations],
    );

    const visibleConversations = useMemo(
        () => filterSidebarConversations(conversations, conversationFilter),
        [conversationFilter, conversations],
    );
    const hasFilter = conversationFilter.trim().length > 0;
    const hasVisibleConversations = visibleConversations.length > 0;

    /* Reserve the responder gutter for the whole list, or for none of it.
       Per-row it produced the worst of both: the assistant answers most pods'
       history and most rows rest, so the slot drew neither a face nor a dot and
       the titles simply sat 30px further right than every other row in the
       sidebar, indented past a column that was always empty. Deciding once per
       list keeps the titles aligned with each other — the reason to reserve it
       at all — and lets a pod that has nothing to draw stop paying for the
       column. It appears when the list has news in it, which is when it earns
       the space. */
    const showResponderSlot = useMemo(
        () => visibleConversations.some((conversation) => (
            getConversationMark(conversation, agentsById)?.kind === 'agent'
            || getConversationSignal(conversation).tone !== 'none'
        )),
        [agentsById, visibleConversations],
    );

    // The apps rail. Same story: the app index is one cached read shared with
    // home's apps panel and the Apps page, so the rail draws from cache.
    const { pages: sidebarAppPages } = useAppPages(canUseApps ? podId : '');
    const sidebarApps = useMemo(
        () => sidebarAppPages.slice(0, SIDEBAR_APP_LIMIT),
        [sidebarAppPages],
    );
    // Apps all open on the one viewer route, so the active row is decided by
    // the `page` query, not the pathname — the only row in this sidebar that
    // needs the search string to know where you are.
    const viewingAppSlug = pathname === `${basePath}/app/view`
        ? appPageSlugFromRouteParam(searchParams.get('page'))
        : null;

    const pods = podsData?.items || [];
    const podGroups = podsData?.groups || [];
    const showPodOrganizationLabels = podsData?.hasMultipleOrganizations;
    const assistantCreationCopy = assistantCreationKind ? ASSISTANT_CREATION_COPY[assistantCreationKind] : null;

    const canShowCreateMenu =
        canCreateAgents ||
        canCreateApps ||
        canCreateWorkflows ||
        canCreateTables ||
        canUpdatePod;

    const prefetchAgents = useCallback(() => {
        router.prefetch(`${basePath}/ai`);
        void queryClient.prefetchQuery(agentsQueryOptions(podId));
    }, [basePath, podId, queryClient, router]);

    const prefetchWorkflows = useCallback(() => {
        router.prefetch(`${basePath}/flows`);
        void queryClient.prefetchQuery(flowsQueryOptions(podId));
    }, [basePath, podId, queryClient, router]);

    const prefetchData = useCallback(() => {
        router.prefetch(`${basePath}/data`);
        void queryClient.ensureQueryData(tablesQueryOptions(podId)).then((tablesPage) => {
            const firstTableName = tablesPage.items[0]?.name;
            if (!firstTableName) return;

            void Promise.all([
                queryClient.prefetchQuery(tableQueryOptions(podId, firstTableName)),
                queryClient.prefetchQuery(tableRecordsQueryOptions(
                    podId,
                    DATASTORE_NAME,
                    firstTableName
                )),
            ]);
        });
    }, [basePath, podId, queryClient, router]);

    // The places are fixed and ordered the same on every route, so their
    // positions can be learned. Apps and agents are not places: the rails
    // above list them as things with their own pages, and the indexes live
    // behind each rail's "View all". What stays a place is what you browse
    // *through*, not what you open: data and docs.
    const primaryPlaces = [
        {
            href: `${basePath}/data`,
            label: 'Data',
            kind: 'data' as const,
            active: isActive(`${basePath}/data`) || isActive(`${basePath}/datastores`),
            visible: canUseData,
            onIntent: prefetchData,
        },
        {
            href: `${basePath}/files`,
            label: 'Docs',
            kind: 'docs' as const,
            active: isActive(`${basePath}/files`),
            visible: canUseDocs,
        },
    ].filter((place) => place.visible);

    // Setup surfaces: you author a workflow, wire a connector, or change a pod
    // setting when something changes, not on the way through. One disclosure
    // costs a slot; three permanent rows cost three.
    //
    // The index routes for apps and agents live here too, permanently. An empty
    // rail used to keep its header so its route stayed reachable, which put an
    // "Apps / View all" line above nothing at all — a section advertising itself
    // as absent, and on a new pod two of them stacked. A header with no rows
    // under it is not a preserved route, it is a broken one. The rails now
    // appear only when they have something to show, and the route someone needs
    // on an empty pod is a fixed one that does not move when content arrives.
    const morePlaces = [
        {
            href: `${basePath}/app/pages`,
            label: 'Apps',
            kind: 'apps' as const,
            active: isActive(`${basePath}/app`),
            // Only while the rail is not drawing apps. Keeping it here always —
            // for a nav position that never moves — put "Apps" on screen twice,
            // and when you were on an app route the *active* fill landed on the
            // buried copy inside `More` while the rail above it sat plain. A
            // stable position is worth less than one unambiguous answer to
            // "where am I".
            visible: canUseApps && sidebarApps.length === 0,
        },
        {
            href: `${basePath}/flows`,
            label: 'Workflows',
            kind: 'workflows' as const,
            active: isActive(`${basePath}/flows`),
            visible: canUseWorkflows,
            onIntent: prefetchWorkflows,
        },
        {
            href: `${basePath}/connectors`,
            label: 'Connectors',
            kind: 'connectors' as const,
            active: isActive(`${basePath}/connectors`),
            visible: canUseConnectors,
        },
        {
            href: `${basePath}/settings`,
            label: 'Pod settings',
            kind: 'settings' as const,
            active: isActive(`${basePath}/settings`),
            visible: canUsePodSettings,
        },
    ].filter((place) => place.visible);

    // A collapsed group holding the page you are on is a nav that cannot show
    // you where you are, so standing inside More forces it open. That is not a
    // preference being overridden — it is the one moment there is nothing to
    // prefer, so the row drops its toggle instead of offering a dead one. The
    // stored answer is untouched and takes over again when you navigate away.
    const moreHoldsActivePlace = morePlaces.some((place) => place.active);
    const moreExpanded = moreDisclosed || moreHoldsActivePlace;

    const openConversation = (conversationId: string) => {
        router.push(`${basePath}/conversations/${encodeURIComponent(conversationId)}`);
    };

    const startConversation = () => {
        if (!canWriteConversations) return;
        router.push(`${basePath}/conversations/new`);
    };

    const getManualCreationHref = (kind: AssistantCreationKind) => {
        if (kind === 'agent') return `${basePath}/agents/new`;
        if (kind === 'workflow') return `${basePath}/flows/new`;
        if (kind === 'app') return `${basePath}/conversations/new`;
        return `${basePath}/data?create=table`;
    };

    const openAssistantCreation = (kind: AssistantCreationKind) => {
        setAssistantCreationKind(kind);
        setAssistantCreationPrompt('');
    };

    const closeAssistantCreation = () => {
        setAssistantCreationKind(null);
        setAssistantCreationPrompt('');
    };

    const startAssistantCreation = () => {
        if (!assistantCreationKind || !canWriteConversations) return;
        const prompt = assistantCreationPrompt.trim();
        if (!prompt) return;

        const href = buildResourceCreationHref({
            podId,
            kind: assistantCreationKind,
            prompt,
            source: 'sidebar_new_menu',
        });

        closeAssistantCreation();
        router.push(href);
    };

    const startManualCreation = () => {
        if (!assistantCreationKind) return;
        const href = getManualCreationHref(assistantCreationKind);
        closeAssistantCreation();
        router.push(href);
    };

    // Hiding the field always drops the query too. A filter still narrowing the
    // list with no visible input is a list that looks like it lost rows.
    const closeFilter = () => {
        setConversationFilter('');
        setFilterOpen(false);
    };

    return (
        <aside className="flex h-full w-full shrink-0 flex-col overflow-hidden bg-[var(--pod-shell-bg)] text-[var(--text-secondary)]">
            <div className="flex h-12 shrink-0 items-center gap-1 border-b border-[color:color-mix(in_srgb,var(--border-subtle)_42%,transparent)] px-2.5">
                <div className="min-w-0 flex-1">
                    <DropdownMenu.Root open={podSwitcherOpen} onOpenChange={setPodSwitcherOpen}>
                        <DropdownMenu.Trigger asChild>
                            <button
                                type="button"
                                /* A hairline at rest, strengthening to the full
                                   edge and fill on hover. Transparent until
                                   hovered, this read as a label with a glyph
                                   after it — the pod's name is the one thing in
                                   the shell people never think to press, so it
                                   has to carry an edge before the pointer
                                   arrives. An edge and not a fill: the filled
                                   control directly below it is the action, and
                                   two fills stacked make identity compete with
                                   it. */
                                className="workspace-sidebar-trigger-button custom-focus-ring flex w-full min-w-0 items-center gap-2 rounded-md border border-[color:color-mix(in_srgb,var(--border-subtle)_52%,transparent)] bg-transparent px-1.5 py-1 text-left text-[var(--text-primary)] transition-colors hover:border-[var(--border-subtle)] hover:bg-[var(--surface-2)] data-[state=open]:border-[var(--border-subtle)] data-[state=open]:bg-[var(--surface-2)]"
                                aria-label={`Switch pod. Current pod: ${podName || 'Current pod'}`}
                                title="Switch pod"
                            >
                                {/* Keyed on the pod so the mark and the name are
                                    replaced, not relabelled, when you switch. The
                                    shell around them survives a switch intact, so
                                    without this the only evidence that anything
                                    happened is a word quietly changing. */}
                                <span key={podId} className="workspace-sidebar-identity flex min-w-0 flex-1 items-center gap-2">
                                    <ResourceIcon
                                        iconUrl={podIconUrl}
                                        alt={`${podName || 'Current pod'} icon`}
                                        label={podName || 'Current pod'}
                                        identityKind="team"
                                        identitySeed={podId}
                                        identitySize={28}
                                        className="h-7 w-7 shrink-0 rounded-md border-[color:color-mix(in_srgb,var(--border-subtle)_58%,transparent)] bg-transparent text-[var(--text-tertiary)]"
                                        fallback={<PodMark name={podName} />}
                                    />
                                    <span className="block min-w-0 flex-1 truncate text-sm font-medium leading-5 text-[var(--text-primary)]">
                                        {podName || 'Current pod'}
                                    </span>
                                </span>
                                <ChevronsUpDown className="h-4 w-4 shrink-0 text-[var(--text-tertiary)]" />
                            </button>
                        </DropdownMenu.Trigger>
                        <PodSwitcherMenu
                            pods={pods}
                            podGroups={podGroups}
                            isLoading={isLoadingPods}
                            showOrganizationLabels={showPodOrganizationLabels}
                            podId={podId}
                            router={router}
                            side="bottom"
                            onShare={() => setBundleShareOpen(true)}
                        />
                    </DropdownMenu.Root>
                </div>
                {canUseConversations ? (
                    <button
                        type="button"
                        onClick={() => (filterOpen ? closeFilter() : setFilterOpen(true))}
                        data-active={filterOpen ? 'true' : undefined}
                        className="lemma-shell-icon-button custom-focus-ring h-8 w-8 shrink-0 self-center text-[var(--text-tertiary)] data-[active=true]:text-[var(--text-primary)]"
                        aria-label="Filter conversations"
                        aria-expanded={filterOpen}
                        title="Filter conversations"
                    >
                        <Search className="h-4 w-4" strokeWidth={1.8} />
                    </button>
                ) : null}
                {/* Beside filter and collapse rather than in the nav list: this
                    is something that arrives on its own and wants a count, which
                    a navigation row cannot carry. */}
                <NotificationsBell podId={podId} />
                {onCollapse ? (
                    <button
                        type="button"
                        onClick={onCollapse}
                        className="lemma-shell-icon-button custom-focus-ring h-8 w-8 shrink-0 self-center text-[var(--text-tertiary)]"
                        aria-label="Collapse sidebar"
                        title="Collapse sidebar"
                    >
                        <PanelLeftClose className="h-4 w-4" strokeWidth={1.8} />
                    </button>
                ) : null}
            </div>

            {canWriteConversations || canShowCreateMenu ? (
                <div className="flex shrink-0 items-center gap-1 px-3 pt-3">
                    {canWriteConversations ? (
                        <button
                            type="button"
                            onClick={startConversation}
                            /* Rests at the fill it used to take on hover, so the
                               control is visible without being bolded or given
                               the brightest surface in the panel. */
                            className="workspace-sidebar-primary-action custom-focus-ring flex h-8 min-w-0 flex-1 items-center gap-2.5 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] px-2.5 text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-3)] hover:text-[var(--text-primary)]"
                        >
                            <Plus className="h-3.5 w-3.5 shrink-0" />
                            <span className="min-w-0 flex-1 truncate text-left">New conversation</span>
                        </button>
                    ) : null}
                    {canShowCreateMenu ? (
                        <DropdownMenu.Root>
                            <DropdownMenu.Trigger asChild>
                                <button
                                    type="button"
                                    className={cn(
                                        'workspace-sidebar-primary-action custom-focus-ring flex h-8 shrink-0 items-center justify-center rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] text-[var(--text-tertiary)] transition-colors hover:bg-[var(--surface-3)] hover:text-[var(--text-primary)] data-[state=open]:bg-[var(--surface-3)]',
                                        canWriteConversations ? 'w-8' : 'min-w-0 flex-1 gap-2.5 px-2.5',
                                    )}
                                    aria-label="Create something else"
                                    title="Create something else"
                                >
                                    {canWriteConversations ? null : (
                                        <span className="min-w-0 flex-1 truncate text-left">Create</span>
                                    )}
                                    <ChevronDown className="h-3.5 w-3.5 shrink-0" />
                                </button>
                            </DropdownMenu.Trigger>
                            <DropdownMenu.Portal>
                                <DropdownMenu.Content
                                    align="end"
                                    side="bottom"
                                    sideOffset={6}
                                    className="surface-panel z-50 w-56 p-1 shadow-[var(--shadow-lg)]"
                                >
                                    {canCreateAgents ? (
                                        <DropdownMenu.Item
                                            onSelect={() => openAssistantCreation('agent')}
                                            className="lemma-menu-row px-2"
                                        >
                                            <ProductIcon kind="agents" size="xs" />
                                            New agent
                                        </DropdownMenu.Item>
                                    ) : null}
                                    {canCreateApps ? (
                                        <DropdownMenu.Item
                                            onSelect={() => openAssistantCreation('app')}
                                            className="lemma-menu-row px-2"
                                        >
                                            <ProductIcon kind="apps" size="xs" />
                                            New app
                                        </DropdownMenu.Item>
                                    ) : null}
                                    {canCreateWorkflows ? (
                                        <DropdownMenu.Item
                                            onSelect={() => openAssistantCreation('workflow')}
                                            className="lemma-menu-row px-2"
                                        >
                                            <ProductIcon kind="workflows" size="xs" />
                                            New workflow
                                        </DropdownMenu.Item>
                                    ) : null}
                                    {canCreateTables ? (
                                        <DropdownMenu.Item
                                            onSelect={() => openAssistantCreation('table')}
                                            className="lemma-menu-row px-2"
                                        >
                                            <ProductIcon kind="tables" size="xs" />
                                            New table
                                        </DropdownMenu.Item>
                                    ) : null}
                                    {canUpdatePod ? (
                                        <>
                                            <DropdownMenu.Separator className="my-1 h-px bg-[var(--border-subtle)]" />
                                            <DropdownMenu.Item
                                                onSelect={() => setBundleImportOpen(true)}
                                                className="lemma-menu-row px-2"
                                            >
                                                <Upload className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" />
                                                Install a bundle
                                            </DropdownMenu.Item>
                                        </>
                                    ) : null}
                                </DropdownMenu.Content>
                            </DropdownMenu.Portal>
                        </DropdownMenu.Root>
                    ) : null}
                </div>
            ) : null}

            {/* The apps rail — the software this pod has shipped, one click
                from anywhere. Marks, not covers: the 16:9 cover is the card
                face on the index, a rail row wants the square mark the identity
                system seeds for inert things. The rail leads the sidebar
                because apps and agents are what this pod *is* — and leading it
                is the whole of the emphasis. It carries the app's hue class so
                the active bar can wear the app's own colour, and nothing else:
                a hue that paints every row on hover stops identifying anything,
                because on hover every app looks like the app under the pointer.
                One click opens the app in the viewer; the index is the "View
                all" beside the header, which stays put even with no apps so the
                route is never stranded. */}
            {canUseApps && sidebarApps.length > 0 ? (
                <div className="shrink-0">
                    <div className="flex items-center justify-between px-2 pt-2">
                        <span className="text-xs leading-5 text-[var(--text-tertiary)]">Apps</span>
                        <Link
                            href={`${basePath}/app/pages`}
                            className="custom-focus-ring rounded px-1 text-xs leading-5 text-[var(--text-tertiary)] transition-colors hover:text-[var(--text-secondary)]"
                        >
                            View all
                        </Link>
                    </div>
                    {sidebarApps.length > 0 ? (
                        <div className="px-3 pb-1 pt-1.5">
                            {sidebarApps.map((page) => (
                                <Link
                                    key={page.slug}
                                    href={`${basePath}/app/view?page=${encodeURIComponent(page.slug)}`}
                                    data-active={viewingAppSlug === page.slug ? 'true' : undefined}
                                    title={page.title}
                                    className={cn(
                                        'lemma-sidebar-row workspace-sidebar-resource-row custom-focus-ring',
                                        identityHueClass(null, page.slug),
                                    )}
                                >
                                    <ResourceIcon
                                        alt={`${page.title} icon`}
                                        label={page.title}
                                        identityKind="mark"
                                        identitySeed={page.slug}
                                        identityGlyph={AppWindow}
                                        identitySize={32}
                                        className="workspace-sidebar-resource-icon h-8 w-8 shrink-0 rounded-md"
                                    />
                                    <span className="min-w-0 flex-1 truncate">{page.title}</span>
                                </Link>
                            ))}
                        </div>
                    ) : null}
                </div>
            ) : null}

            {/* The agents rail — who works here, as faces rather than a count.
                A pod's agents are its cast, and a cast you can see is the whole
                point of the messenger sidebar this mirrors. Lem
                leads the rail: it is the responder every conversation already
                knows, so it wears the mark rather than a seeded face, and takes
                the brand violet hue rather than a seeded one. Faces get 32px:
                the size a being's rich motion turns on, so reaching for a row
                wakes the face up, and comfortably above the 20px floor where
                the status pip renders. Only agents you can talk to — see
                `sidebarAgents` — so the cast is people, not callable
                contracts. One click opens the agent's own page; the roster is
                the "View all" beside the header, which stays put even with no
                faces so the route is never stranded, and it lists every agent
                including the ones this rail filters out. */}
            {/* No empty-rail guard here: Lem always leads this rail,
                so it is never a header above nothing the way Apps could be. */}
            {canUseAgents ? (
                <div className="shrink-0">
                    <div className="flex items-center justify-between px-2 pt-2">
                        <span className="text-xs leading-5 text-[var(--text-tertiary)]">Agents</span>
                        <Link
                            href={`${basePath}/ai`}
                            onPointerEnter={prefetchAgents}
                            onFocus={prefetchAgents}
                            className="custom-focus-ring rounded px-1 text-xs leading-5 text-[var(--text-tertiary)] transition-colors hover:text-[var(--text-secondary)]"
                        >
                            View all
                        </Link>
                    </div>
                    <div className="px-3 pb-1 pt-1.5">
                        <Link
                            href={`${basePath}/ai/assistant`}
                            data-active={isActive(`${basePath}/ai/assistant`) ? 'true' : undefined}
                            title={DEFAULT_RESPONDER_NAME}
                            className="lemma-sidebar-row workspace-sidebar-resource-row lm-identity-hue-0 custom-focus-ring"
                        >
                            {/* Drawn by the same renderer as the agents below it,
                                at the same 32px, on the reserved seed. The tinted
                                tile is gone with the trademark that sat on it: a
                                being needs no ground, and the tile was the last
                                thing claiming this row was a different *kind* of
                                thing from the cast it leads. */}
                            <ResourceIdentity
                                seed={LEM_SEED}
                                label={DEFAULT_RESPONDER_NAME}
                                kind="being"
                                size={32}
                                className="workspace-sidebar-resource-icon h-8 w-8 shrink-0"
                            />
                            <span className="min-w-0 flex-1 truncate">{DEFAULT_RESPONDER_NAME}</span>
                        </Link>
                        {sidebarAgents.map((agent) => {
                            const displayName = formatAgentName(agent.name);
                            const href = `${basePath}/agents/${encodeURIComponent(agent.name)}`;
                            return (
                                <Link
                                    key={agent.name}
                                    href={href}
                                    data-active={isActive(href) ? 'true' : undefined}
                                    title={displayName}
                                    className={cn(
                                        'lemma-sidebar-row workspace-sidebar-resource-row custom-focus-ring',
                                        identityHueClass(agent.icon_url, agent.name),
                                    )}
                                >
                                    <ResourceIcon
                                        iconUrl={agent.icon_url}
                                        alt={`${displayName} icon`}
                                        label={displayName}
                                        identityKind="being"
                                        identitySeed={agent.name}
                                        identitySize={32}
                                        className="workspace-sidebar-resource-icon h-8 w-8 shrink-0 rounded-md"
                                    />
                                    <span className="min-w-0 flex-1 truncate">{displayName}</span>
                                </Link>
                            );
                        })}
                    </div>
                </div>
            ) : null}


            <Dialog open={assistantCreationKind !== null} onOpenChange={(open) => {
                if (!open) closeAssistantCreation();
            }}>
                <DialogContent className="w-[min(560px,calc(100vw-32px))] max-w-none gap-0 overflow-hidden rounded-lg border-[var(--border-subtle)] bg-[var(--card-bg)] p-0 shadow-[var(--shadow-lg)]">
                    <DialogHeader className="px-5 pb-4 pt-5 pr-12">
                        <div className="flex items-start gap-3">
                            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)]">
                                <ProductIcon kind={assistantCreationCopy?.iconKind || 'agents'} size="sm" />
                            </span>
                            <div className="min-w-0">
                                <p className="text-xs font-medium leading-4 text-[var(--text-tertiary)]">
                                    {assistantCreationCopy?.title || 'Create with assistant'}
                                </p>
                                <DialogTitle className="mt-1 text-xl leading-7">
                                    {assistantCreationCopy?.prompt || 'What should this do?'}
                                </DialogTitle>
                                <DialogDescription className="mt-1.5 max-w-[34rem] text-sm leading-6 text-[var(--text-tertiary)]">
                                    {assistantCreationCopy?.description}
                                </DialogDescription>
                            </div>
                        </div>
                    </DialogHeader>
                    <div className="space-y-3.5 px-5 pb-5">
                        <label className="block">
                            <span className="sr-only">{assistantCreationCopy?.prompt}</span>
                            <Textarea
                                value={assistantCreationPrompt}
                                onChange={(event) => setAssistantCreationPrompt(event.target.value)}
                                onKeyDown={(event) => {
                                    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
                                        event.preventDefault();
                                        startAssistantCreation();
                                    }
                                }}
                                placeholder={assistantCreationCopy?.placeholder}
                                className="form-field-control-flat min-h-[132px] resize-none rounded-lg px-3.5 py-3 text-sm leading-6"
                                disableFocusRing
                                autoFocus
                            />
                        </label>
                        {assistantCreationCopy?.examples.length ? (
                            <div className="flex flex-wrap gap-1.5">
                                {assistantCreationCopy.examples.map((example) => (
                                    <button
                                        key={example}
                                        type="button"
                                        onClick={() => setAssistantCreationPrompt(example)}
                                        className="workspace-sidebar-suggestion-chip-button custom-focus-ring rounded-md border border-[var(--border-subtle)] bg-[var(--surface-1)] px-2 py-1 text-xs leading-4 text-[var(--text-tertiary)] transition-colors hover:border-[var(--border-strong)] hover:bg-[var(--surface-2)] hover:text-[var(--text-primary)]"
                                    >
                                        {example}
                                    </button>
                                ))}
                            </div>
                        ) : null}
                    </div>
                    <DialogFooter className="items-center justify-between gap-2 border-t border-[color:color-mix(in_srgb,var(--border-subtle)_64%,transparent)] px-5 py-3.5 sm:flex-row sm:justify-between">
                        {assistantCreationCopy?.manualLabel ? (
                            <Button
                                type="button"
                                variant="quiet"
                                size="sm"
                                className="text-[var(--text-tertiary)]"
                                onClick={startManualCreation}
                            >
                                {assistantCreationCopy.manualLabel}
                            </Button>
                        ) : (
                            <span aria-hidden="true" />
                        )}
                        <Button variant="primary"
                            type="button"
                            size="sm"
                            className="px-3.5"
                            onClick={startAssistantCreation}
                            disabled={!canWriteConversations || !assistantCreationPrompt.trim()}
                        >
                            {assistantCreationCopy?.action || 'Create with assistant'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            <ShareSheet
                podId={podId}
                podName={podName}
                open={bundleShareOpen}
                onOpenChange={setBundleShareOpen}
                canPublish={canUpdatePod}
            />
            {canUpdatePod ? (
                <ImportDialog
                    podId={podId}
                    podName={podName}
                    open={bundleImportOpen}
                    onOpenChange={setBundleImportOpen}
                />
            ) : null}

            {canUseConversations ? (
                <>
                    {filterOpen ? (
                        <div className="shrink-0 px-3 pt-2">
                            <div className="workspace-sidebar-filter custom-focus-ring-within flex h-7 items-center gap-1.5 rounded-md px-2">
                                <input
                                    type="text"
                                    value={conversationFilter}
                                    onChange={(event) => setConversationFilter(event.target.value)}
                                    onKeyDown={(event) => {
                                        if (event.key === 'Escape') closeFilter();
                                    }}
                                    placeholder="Filter conversations"
                                    aria-label="Filter conversations"
                                    className="min-w-0 flex-1 border-0 bg-transparent p-0 text-sm text-[var(--text-primary)] outline-none placeholder:text-[var(--text-tertiary)]"
                                    autoFocus
                                />
                                <button
                                    type="button"
                                    onClick={closeFilter}
                                    className="custom-focus-ring shrink-0 rounded text-[var(--text-tertiary)] hover:text-[var(--text-primary)]"
                                    aria-label="Close filter"
                                >
                                    <X className="h-3.5 w-3.5" />
                                </button>
                            </div>
                        </div>
                    ) : null}

                    <div className="shrink-0 flex items-center justify-between px-2 pt-2">
                        <span className="text-xs leading-5 text-[var(--text-tertiary)]">Recents</span>
                        <div
                            className="flex items-center gap-1 text-xs leading-5"
                            role="group"
                            aria-label="Conversation scope"
                        >
                            {routeAgentName ? (
                                <>
                                    <button
                                        type="button"
                                        onClick={() => setConversationScope('agent')}
                                        className={cn(
                                            'custom-focus-ring max-w-24 truncate rounded px-1 transition-colors',
                                            conversationScope === 'agent'
                                                ? 'text-[var(--text-secondary)]'
                                                : 'text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]',
                                        )}
                                        aria-pressed={conversationScope === 'agent'}
                                        title={`${formatAgentName(routeAgentName)} conversations`}
                                    >
                                        {formatAgentName(routeAgentName)}
                                    </button>
                                    <span className="text-[var(--text-tertiary)]" aria-hidden="true">·</span>
                                </>
                            ) : null}
                            <button
                                type="button"
                                onClick={() => setConversationScope('assistant')}
                                className={cn(
                                    'custom-focus-ring rounded px-1 transition-colors',
                                    conversationScope === 'assistant'
                                        ? 'text-[var(--text-secondary)]'
                                        : 'text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]',
                                )}
                                aria-pressed={conversationScope === 'assistant'}
                                title={`${DEFAULT_RESPONDER_NAME}'s conversations`}
                            >
                                {DEFAULT_RESPONDER_NAME}
                            </button>
                            <span className="text-[var(--text-tertiary)]" aria-hidden="true">·</span>
                            <button
                                type="button"
                                onClick={() => setConversationScope('all')}
                                className={cn(
                                    'custom-focus-ring rounded px-1 transition-colors',
                                    conversationScope === 'all'
                                        ? 'text-[var(--text-secondary)]'
                                        : 'text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]',
                                )}
                                aria-pressed={conversationScope === 'all'}
                                title="All conversations"
                            >
                                All
                            </button>
                        </div>
                    </div>

                    <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3 pt-1.5">
                        {isLoadingConversationHistory && !hasVisibleConversations ? (
                            /* Rows at the row's own height, dot gutter included —
                               a one-line caption is a different box from the list
                               it becomes, and this rail is narrow enough that the
                               swap reads as the whole nav resettling. */
                            <div role="status" aria-label="Loading conversations">
                                {CONVERSATION_ROW_SKELETON_WIDTHS.map((width, index) => (
                                    <div
                                        key={index}
                                        className="lemma-sidebar-row workspace-sidebar-conversation-row"
                                        data-skeleton="true"
                                    >
                                        <span className="flex w-3.5 shrink-0 items-center justify-center">
                                            <Skeleton shape="circle" className="h-1.5 w-1.5" />
                                        </span>
                                        <Skeleton className={cn('h-3', width)} />
                                    </div>
                                ))}
                            </div>
                        ) : !hasVisibleConversations ? (
                            <div className="px-2 py-1.5 text-sm leading-5 text-[var(--text-tertiary)]">
                                {hasFilter
                                    ? 'No conversations match that.'
                                    : 'Start a conversation and it shows up here.'}
                            </div>
                        ) : (
                            <>
                                {visibleConversations.map((conversation) => (
                                    <ConversationRow
                                        key={conversation.id}
                                        conversation={conversation}
                                        active={isConversationRoute && openedConversationId === conversation.id}
                                        onOpen={() => openConversation(conversation.id)}
                                        mark={getConversationMark(conversation, agentsById)}
                                        showResponder={showResponderSlot}
                                    />
                                ))}
                            </>
                        )}
                        {!hasFilter ? (
                            <Link
                                href={`${basePath}/conversations`}
                                className={cn(
                                    'lemma-sidebar-row workspace-sidebar-conversation-row workspace-sidebar-show-more custom-focus-ring text-[var(--text-tertiary)] hover:text-[var(--text-primary)]',
                                    showResponderSlot ? 'workspace-sidebar-conversation-row-marked' : null,
                                )}
                            >
                                {/* Empty gutter so the label starts on the same
                                    x as the titles above it, at whichever width
                                    the list settled on. */}
                                <span
                                    className={cn('shrink-0', showResponderSlot ? 'w-5' : 'w-3.5')}
                                    aria-hidden="true"
                                />
                                <span className="min-w-0 flex-1 truncate">All conversations</span>
                            </Link>
                        ) : null}
                    </div>
                </>
            ) : (
                <div className="min-h-0 flex-1" />
            )}

            {/* The places, as a footer.
                They used to sit between the agents rail and the history, where
                they read as a demoted tail of the rail above rather than a band
                of their own: every other section carries a header and this one
                cannot plausibly have one ("Browse"? "Material"?), and the only
                rule near it fell on its *lower* edge, so it attached upward to
                the agents and detached from the history it was introducing.
                A footer needs no header, and the things you open — apps,
                agents, recents — become one contiguous column above it. The
                strip is pinned outside the scrolling history, so a long list of
                conversations can never push Data and Docs off the bottom. */}
            {primaryPlaces.length || morePlaces.length ? (
                <nav
                    aria-label="Pod places"
                    className="workspace-sidebar-places shrink-0 space-y-0.5 px-3 pb-2 pt-2"
                >
                    {primaryPlaces.map((place) => (
                        <PlaceLink key={place.href} {...place} />
                    ))}
                    {morePlaces.length ? (
                        <>
                            <MoreDisclosureRow
                                expanded={moreExpanded}
                                onToggle={moreHoldsActivePlace
                                    ? undefined
                                    : () => setMoreDisclosed(!moreDisclosed)}
                            />
                            {moreExpanded
                                ? morePlaces.map((place) => (
                                    <PlaceLink key={place.href} {...place} />
                                ))
                                : null}
                        </>
                    ) : null}
                </nav>
            ) : null}
            {/* Local settings is the desktop shell's own control centre, not a
                pod place, so it stays down here with the account rather than
                joining the nav above. */}
            <div className="shrink-0 px-3 pb-3 pt-1">
                <LocalSettingsButton className="mb-1.5" />
                <div className="flex items-center gap-1.5">
                    <Link
                        href="/home"
                        aria-label="Go to Lemma home"
                        title="Lemma home"
                        className="workspace-sidebar-trigger-button custom-focus-ring flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[var(--text-primary)] transition-colors hover:bg-[var(--surface-2)]"
                    >
                        <Logo size="xs" variant="mark-only" />
                    </Link>
                    <AccountMenu podId={podId} />
                </div>
            </div>
        </aside>
    );
}

/**
 * One line and a dot. State is carried by a 6px mark in a fixed left gutter
 * rather than by a second line of text — a title plus metadata per row turns a
 * list you scan into a list you read, and the whole point of this column is
 * that you can scan it.
 *
 * The mark is quiet by default. It only takes a colour when the conversation is
 * doing something now or wants something from you now, because a column where
 * most dots are lit is a column that cannot point at anything. What each state
 * is worth is decided in `getConversationSignal`, not here.
 */
/** Conversation titles vary in length, so the placeholders do too. */
const CONVERSATION_ROW_SKELETON_WIDTHS = ['w-3/5', 'w-5/12', 'w-2/3', 'w-1/2', 'w-7/12', 'w-1/2'];

/* Exported for the agent page's conversation rail: one conversation reads the
   same whether it is listed under the pod or under one agent — same halo, same
   row. The agent page passes no mark: every conversation there belongs to the
   agent whose page you are already on, so drawing its face fifteen times
   identifies nothing and it keeps the dot gutter. */
/* The status dot, for a row with no face to hang a pip on. */
function ConversationSignalDot({ signal }: { signal: ReturnType<typeof getConversationSignal> }) {
    if (signal.pulse) {
        /* Live work gets the ping halo the chat's status pill wears — a dot
           that breathes reads as *happening now* in a way a blinking dot never
           quite did. The tone stays the sidebar's own (delight gold), only the
           motion arrives. */
        return (
            <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inset-0 animate-ping rounded-full bg-[var(--delight)] opacity-40" />
                <span className="relative h-1.5 w-1.5 rounded-full bg-[var(--delight)]" />
            </span>
        );
    }

    /* A resting row keeps its hollow ring. Removing it — on the argument that
       "nothing is happening" is better said with nothing — read fine as a
       sentence and wrong on screen: the dot is not only a status report, it is
       the row's left anchor. Without one, a column of titles hangs off a blank
       gutter with nothing explaining the indent, and the history stops reading
       as a list at all. A mark that costs one faint circle and buys the
       list its left edge is worth the ink. */
    return (
        <span
            className={cn(
                'block h-1.5 w-1.5 rounded-full',
                signal.filled ? 'bg-current' : 'border opacity-45',
                // A resting dot takes a fixed border colour rather than
                // `currentColor`: the row's text brightens on hover and when
                // active, and a mark that brightens with the pointer reads as a
                // status that changed.
                signal.tone === 'none' && 'border-[var(--text-tertiary)]',
                signal.tone === 'live' && 'text-[var(--delight)]',
                signal.tone === 'warning' && 'text-[var(--state-warning)]',
                signal.tone === 'danger' && 'text-[var(--state-error)]',
            )}
        />
    );
}

export function ConversationRow({
    conversation,
    active,
    onOpen,
    mark,
    showResponder,
}: {
    conversation: Conversation;
    active: boolean;
    onOpen: () => void;
    mark?: ConversationMark | null;
    /** Reserve the responder slot. The sidebar sets it; the agent page does not. */
    showResponder?: boolean;
}) {
    const signal = getConversationSignal(conversation);
    /* Only an agent gets drawn. The assistant is the default responder — in most
       pods it answers everything — so drawing it put the Lemma mark on all
       fifteen rows at once, which is a logo wall, not identification. Removing
       the tile under it was half the fix and left the other half: the mark was
       still there, fifteen times. "Mark the exception" has to mean the default
       draws *nothing*. The slot stays reserved either way so every title keeps
       one left edge, and the empty ones carry the status dot they always had. */
    const face = showResponder && mark?.kind === 'agent' ? mark : null;

    return (
        <button
            type="button"
            onClick={onOpen}
            data-active={active ? 'true' : undefined}
            title={conversation.title || 'Untitled conversation'}
            className={cn(
                'lemma-sidebar-row workspace-sidebar-conversation-row custom-focus-ring',
                showResponder ? 'workspace-sidebar-conversation-row-marked' : null,
            )}
        >
            {showResponder ? (
                <span className="workspace-sidebar-conversation-mark shrink-0">
                    {face ? (
                        <>
                            <ResourceIcon
                                iconUrl={face.iconUrl}
                                alt=""
                                label={face.label}
                                identityKind="being"
                                identitySeed={face.seed}
                                identitySize={20}
                                className="h-5 w-5 shrink-0 rounded-md"
                            />
                            {/* The face carries *who*, the pip carries *what this
                                run is doing*. Separate objects on purpose: the
                                identity system's own `state` describes the agent,
                                and an agent with three runs in flight is not "the
                                state" of any one of them. */}
                            {signal.tone !== 'none' ? (
                                <span
                                    className="workspace-sidebar-conversation-pip"
                                    data-tone={signal.tone}
                                    data-pulse={signal.pulse ? 'true' : undefined}
                                    aria-hidden="true"
                                />
                            ) : null}
                        </>
                    ) : (
                        <span className="flex h-5 w-5 items-center justify-center" aria-hidden="true">
                            <ConversationSignalDot signal={signal} />
                        </span>
                    )}
                </span>
            ) : (
                <span className="flex w-3.5 shrink-0 items-center justify-center" aria-hidden="true">
                    <ConversationSignalDot signal={signal} />
                </span>
            )}
            <span className="min-w-0 flex-1 truncate">
                {conversation.title || 'Untitled conversation'}
            </span>
            {signal.label ? <span className="sr-only">{signal.label}</span> : null}
        </button>
    );
}

type PodSwitcherMenuProps = {
    pods: AccessiblePod[];
    podGroups: AccessiblePodGroup[];
    isLoading: boolean;
    showOrganizationLabels?: boolean;
    podId: string;
    router: ReturnType<typeof useRouter>;
    side: 'top' | 'bottom';
    onShare: () => void;
};

function PodSwitcherMenu(props: PodSwitcherMenuProps) {
    return (
        <DropdownMenu.Portal>
            {/* The query lives inside the portal, which is torn down on close,
                so it never survives into the next time the menu is opened — a
                list still silently narrowed by something you typed a day ago is
                a list that looks like it lost most of its rows. */}
            <PodSwitcherPanel {...props} />
        </DropdownMenu.Portal>
    );
}

function PodSwitcherPanel({
    pods,
    podGroups,
    isLoading,
    showOrganizationLabels,
    podId,
    router,
    side,
    onShare,
}: PodSwitcherMenuProps) {
    const [podFilter, setPodFilter] = useState('');
    const contentRef = useRef<HTMLDivElement>(null);
    const filterInputRef = useRef<HTMLInputElement>(null);

    const showFilter = shouldShowPodFilter(pods.length);

    const visiblePods = useMemo(
        () => filterSwitcherPods(pods, podFilter),
        [podFilter, pods],
    );
    const visibleGroups = useMemo(
        () => filterSwitcherPodGroups(podGroups, podFilter),
        [podFilter, podGroups],
    );

    const firstPodRow = () => contentRef.current?.querySelector<HTMLElement>('[data-pod-row]') ?? null;

    return (
        <DropdownMenu.Content
            ref={contentRef}
            align="start"
            side={side}
            sideOffset={8}
            className="surface-panel z-50 flex w-72 flex-col p-1 shadow-[var(--shadow-lg)]"
            onKeyDownCapture={(event) => {
                // With a field on screen a typed letter belongs to it. Radix
                // answers the same keystroke with its own invisible
                // typeahead — focus jumps down the list while the field
                // stays empty — so the key is claimed before that runs.
                if (!showFilter) return;
                const input = filterInputRef.current;
                if (!input || event.target === input) return;
                if (event.metaKey || event.ctrlKey || event.altKey) return;
                if (event.key.length !== 1) return;

                event.preventDefault();
                event.stopPropagation();
                input.focus();
                setPodFilter((current) => current + event.key);
            }}
        >
            <DropdownMenu.Item
                onSelect={onShare}
                className="lemma-menu-row shrink-0"
            >
                <Share2 className="h-3.5 w-3.5" />
                Share this pod
            </DropdownMenu.Item>
            <DropdownMenu.Separator className="my-1 h-px shrink-0 bg-[var(--border-subtle)]" />
            <div className="shrink-0 px-2 py-1.5 type-eyebrow">
                Switch pod
            </div>
            {showFilter ? (
                <div className="shrink-0 px-1 pb-1">
                    <div className="workspace-sidebar-filter custom-focus-ring-within flex h-7 items-center gap-1.5 rounded-md px-2">
                        <Search className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" strokeWidth={1.8} />
                        <input
                            ref={filterInputRef}
                            type="text"
                            value={podFilter}
                            onChange={(event) => setPodFilter(event.target.value)}
                            onKeyDown={(event) => {
                                // Tab and Escape belong to the menu around the
                                // field. Escape especially: the dismiss layer
                                // takes it off the document in the capture
                                // phase, so no amount of stopping it here would
                                // keep the menu open — and it need not, since
                                // the query dies with the panel anyway.
                                if (event.key === 'Tab' || event.key === 'Escape') return;

                                if (event.key === 'ArrowDown') {
                                    event.preventDefault();
                                    firstPodRow()?.focus();
                                } else if (event.key === 'Enter' && podFilter.trim()) {
                                    // Only once something has been typed: with
                                    // an empty field the top row is whichever
                                    // pod happens to sort first, and switching
                                    // workspaces is not a thing to do by
                                    // accident. Clicking the row rather than
                                    // routing by hand keeps one way into a pod —
                                    // the menu closes and the link is followed
                                    // exactly as if it had been picked by
                                    // pointer.
                                    event.preventDefault();
                                    firstPodRow()?.click();
                                }

                                event.stopPropagation();
                            }}
                            placeholder="Find a pod"
                            aria-label="Find a pod"
                            className="min-w-0 flex-1 border-0 bg-transparent p-0 text-sm text-[var(--text-primary)] outline-none placeholder:text-[var(--text-tertiary)]"
                        />
                    </div>
                </div>
            ) : null}
            <div className="min-h-0 max-h-96 overflow-y-auto">
                {isLoading ? (
                    <div className="px-2 py-2 text-sm text-[var(--text-tertiary)]">Loading pods…</div>
                ) : pods.length === 0 ? (
                    <div className="px-2 py-2 text-sm text-[var(--text-tertiary)]">No pods yet.</div>
                ) : visiblePods.length === 0 ? (
                    <div className="px-2 py-2 text-sm text-[var(--text-tertiary)]">No pods match that.</div>
                ) : null}
                {showOrganizationLabels ? (
                    visibleGroups.map((group) => (
                        <div key={group.organization.id}>
                            <div className="truncate px-2 pt-2 pb-1 text-xs font-medium uppercase tracking-normal text-[var(--text-tertiary)]">
                                {group.organization.name}
                            </div>
                            {group.pods.map((pod) => (
                                <PodSwitcherMenuItem key={pod.id} pod={pod} podId={podId} />
                            ))}
                        </div>
                    ))
                ) : (
                    visiblePods.map((pod) => (
                        <PodSwitcherMenuItem key={pod.id} pod={pod} podId={podId} />
                    ))
                )}
            </div>
            <DropdownMenu.Separator className="my-1 h-px shrink-0 bg-[var(--border-subtle)]" />
            <DropdownMenu.Item asChild>
                <Link
                    href="/home"
                    className="lemma-menu-row shrink-0"
                >
                    <Home className="h-3.5 w-3.5" />
                    Manage pods
                </Link>
            </DropdownMenu.Item>
            {/* A menu row, skinned like every other menu row. Painted gold
                it read as a warning sitting next to "Manage pods" — and
                gold is delight, not action (design.md §1). The row is the
                target; it does not need a colour to say so. */}
            <DropdownMenu.Item
                onSelect={() => router.push('/create-pod')}
                className="lemma-menu-row shrink-0"
            >
                <Plus className="h-3.5 w-3.5" />
                New pod
            </DropdownMenu.Item>
        </DropdownMenu.Content>
    );
}

function PodSwitcherMenuItem({
    pod,
    podId,
}: {
    pod: AccessiblePod;
    podId: string;
}) {
    const isCurrent = pod.id === podId;
    const label = toPodDisplayLabel(pod.name);

    return (
        <DropdownMenu.Item asChild>
            <Link
                href={`/pod/${pod.id}`}
                data-pod-row=""
                data-current={isCurrent ? 'true' : undefined}
                aria-current={isCurrent ? 'page' : undefined}
                /* Every row wears the pod's own mark, and the pod you are
                   standing in is filled rather than annotated at the far end of
                   the row. That is what makes this a picker of places instead
                   of a column of words: you can see which one you are in, and
                   picking another moves a fill you were already watching. */
                className="lemma-menu-row lemma-pod-switcher-row lemma-menu-row-between"
            >
                <span className="flex min-w-0 flex-1 items-center gap-2">
                    <ResourceIcon
                        iconUrl={pod.icon_url}
                        alt=""
                        label={label}
                        identityKind="team"
                        identitySeed={pod.id}
                        identitySize={24}
                        className="h-6 w-6 shrink-0 rounded-md border-[color:color-mix(in_srgb,var(--border-subtle)_58%,transparent)] bg-transparent text-[var(--text-tertiary)]"
                        fallback={<PodMark name={pod.name} />}
                    />
                    <span className="truncate">{label}</span>
                </span>
                {isCurrent ? (
                    <span className="flex shrink-0 items-center gap-1.5 text-xs text-[var(--text-tertiary)]">
                        {/* "You are here" is a selected state, and selected
                            states read from the accent channel (design.md §2). */}
                        <Check className="h-3.5 w-3.5 text-[var(--action-primary)]" />
                        Current
                    </span>
                ) : null}
            </Link>
        </DropdownMenu.Item>
    );
}

/**
 * The lid on the setup places. Without `onToggle` it renders as a plain group
 * label — the group is already open around the page you are on, so there is
 * nothing to press.
 */
function MoreDisclosureRow({
    expanded,
    onToggle,
}: {
    expanded: boolean;
    onToggle?: () => void;
}) {
    const body = (
        <span className="flex min-w-0 items-center gap-2.5">
            {/* Exactly the `xs` product-icon box, so every label in the nav —
                grouped or not — starts on the same x. */}
            <span className="flex h-3.5 w-3.5 shrink-0 items-center justify-center">
                <ChevronDown
                    className={cn(
                        'h-3 w-3 transition-transform duration-150',
                        expanded ? 'rotate-0' : '-rotate-90',
                    )}
                />
            </span>
            <span className="truncate">More</span>
        </span>
    );

    if (!onToggle) {
        return (
            <div className="lemma-sidebar-row lemma-sidebar-row-base font-normal text-[var(--text-tertiary)]">
                {body}
            </div>
        );
    }

    return (
        <button
            type="button"
            onClick={onToggle}
            aria-expanded={expanded}
            className="lemma-sidebar-row lemma-sidebar-row-base custom-focus-ring font-normal text-[var(--text-tertiary)] transition-colors hover:text-[var(--text-primary)]"
        >
            {body}
        </button>
    );
}

function PlaceLink(props: {
    href: string;
    label: string;
    kind: ProductIconKind;
    active?: boolean;
    onIntent?: () => void;
}) {
    const { href, label, kind, active, onIntent } = props;

    return (
        <Link
            href={href}
            onPointerEnter={onIntent}
            onFocus={onIntent}
            onTouchStart={onIntent}
            data-active={active ? 'true' : undefined}
            className="lemma-product-nav-item lemma-sidebar-row lemma-sidebar-row-base custom-focus-ring group font-normal"
        >
            <span className="flex min-w-0 items-center gap-2.5">
                <ProductIcon kind={kind} size="xs" state={active ? 'selected' : 'default'} />
                <span className="truncate">{label}</span>
            </span>
        </Link>
    );
}
