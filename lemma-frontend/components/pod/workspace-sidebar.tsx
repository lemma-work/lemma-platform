'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useCallback, useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
    Check,
    ChevronDown,
    Home,
    LogOut,
    PanelLeftClose,
    Plus,
    Search,
    Share2,
    Upload,
    User,
    X,
} from '@/components/ui/icons';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';

import { useAIAssistant } from '@/components/ai/ai-assistant-context';
import { Logo } from '@/components/brand/logo';
import { LocalSettingsButton } from '@/components/desktop/local-settings-button';
import { ShareSheet } from '@/components/bundle/share-sheet';
import { ImportDialog } from '@/components/bundle/import-dialog';
import { ProductIcon, type ProductIconKind } from '@/components/pod/product-icon';
import { ResourceIcon } from '@/components/shared/resource-icon';
import { ThemeToggle } from '@/components/theme/theme-toggle';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
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
import { agentsQueryOptions } from '@/lib/hooks/use-agents';
import {
    tableQueryOptions,
    tableRecordsQueryOptions,
    tablesQueryOptions,
} from '@/lib/hooks/use-datastores';
import { flowsQueryOptions } from '@/lib/hooks/use-flows';
import { useAccessiblePods, type AccessiblePodGroup } from '@/lib/hooks/use-pods';
import { useProfile } from '@/lib/hooks/use-user';
import { useScopedConversations } from '@/lib/hooks/use-assistants';
import {
    filterSidebarConversations,
    mergeSidebarConversations,
    SIDEBAR_CONVERSATION_LIMIT,
} from '@/lib/assistant/sidebar-conversations';
import { getAppRecipeExamples } from '@/lib/recipes/recipes';
import type { Conversation } from '@/lib/types';
import { getConversationStatusView } from '@/lib/utils/conversations';
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

type AssistantCreationKind = 'agent' | 'app' | 'workflow' | 'table';

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

function getAssistantCreationInstructions(kind: AssistantCreationKind): string {
    const resourceLabel = kind === 'table' ? 'datastore table' : kind === 'app' ? 'app app' : kind;
    const action = kind === 'agent'
        ? 'Create a useful agent with clear instructions, appropriate resource access, and a name that fits this pod.'
        : kind === 'app'
            ? 'Start by understanding the operator workflow, then create a minimal useful Lemma app app with the right data, pages, and interactions.'
            : kind === 'workflow'
            ? 'Create a useful workflow with a clear trigger or manual start, practical steps, and a name that fits this pod.'
            : 'Create a useful datastore table with a practical schema, readable field names, and a name that fits this pod.';

    return [
        `You are helping create a Lemma ${resourceLabel} in the current pod.`,
        'Use the user-visible message as the product intent. Do not repeat these hidden instructions back to the user.',
        'Inspect relevant pod context and existing resources before creating anything.',
        action,
        'Ask at most one concise clarification only if creating the resource would otherwise be risky or materially wrong.',
        'After creation, summarize what was created and display or link the resource when possible.',
    ].join('\n');
}

function toDisplayLabel(value: string | null | undefined) {
    const cleaned = (value || '')
        .replace(/[_-]+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();

    if (!cleaned) return 'Untitled';

    return cleaned
        .split(' ')
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ');
}

/**
 * The sidebar is the pod's activity spine: identity, one way to act, the
 * conversation history, and a fixed set of places. Its shape never changes with
 * the route — resource lists belong to the page that owns them, not to a second
 * copy in the nav.
 */
export function WorkspaceSidebar({ podId, podName, podIconUrl, onCollapse }: WorkspaceSidebarProps) {
    const pathname = usePathname();
    const router = useRouter();
    const queryClient = useQueryClient();
    const [assistantCreationKind, setAssistantCreationKind] = useState<AssistantCreationKind | null>(null);
    const [assistantCreationPrompt, setAssistantCreationPrompt] = useState('');
    const [bundleShareOpen, setBundleShareOpen] = useState(false);
    const [bundleImportOpen, setBundleImportOpen] = useState(false);
    const [podSwitcherOpen, setPodSwitcherOpen] = useState(false);
    const [conversationFilter, setConversationFilter] = useState('');
    const [filterOpen, setFilterOpen] = useState(false);
    const { data: podsData, isLoading: isLoadingPods } = useAccessiblePods({
        enabled: podSwitcherOpen,
    });
    const { data: profile } = useProfile();
    const podAccess = usePodAccess(podId);
    const canUseConversations = podAccess.canAccessRoute('conversations');
    const canWriteConversations = podAccess.can('conversation.write');
    const canUseAgents = podAccess.canAccessRoute('agents');
    const canUseWorkflows = podAccess.canAccessRoute('workflows');
    const canUseConnectors = podAccess.canAccessRoute('connectors');
    const canUseData = podAccess.canAccessRoute('data');
    const canUseDocs = podAccess.canAccessRoute('files');
    const canUseApps = podAccess.canAccessRoute('apps');
    const canCreateAgents = podAccess.can('agent.create');
    const canCreateApps = podAccess.can('app.create');
    const canCreateWorkflows = podAccess.can('workflow.create');
    const canCreateTables = podAccess.can('datastore.table.create');
    const canUpdatePod = podAccess.can('pod.update');
    const basePath = `/pod/${podId}`;
    const isActive = (href: string) => pathname === href || pathname.startsWith(`${href}/`);
    const isConversationRoute = isActive(`${basePath}/conversations`);
    const {
        conversations: controllerConversations,
        openedConversationId,
    } = useAIAssistant();
    const {
        data: conversationHistory,
        isLoading: isLoadingConversationHistory,
    } = useScopedConversations(
        { podId },
        { limit: SIDEBAR_CONVERSATION_LIMIT, enabled: canUseConversations },
    );
    // The controller can hold conversations the capped query missed, so trim
    // after merging — otherwise a brand new conversation could push the list
    // past the limit it is meant to hold.
    const conversations = useMemo(
        () => mergeSidebarConversations(
            conversationHistory?.items || [],
            controllerConversations,
        ).slice(0, SIDEBAR_CONVERSATION_LIMIT),
        [controllerConversations, conversationHistory?.items],
    );

    const visibleConversations = useMemo(
        () => filterSidebarConversations(conversations, conversationFilter),
        [conversationFilter, conversations],
    );
    const hasFilter = conversationFilter.trim().length > 0;
    const hasVisibleConversations = visibleConversations.length > 0;

    const pods = podsData?.items || [];
    const podGroups = podsData?.groups || [];
    const showPodOrganizationLabels = podsData?.hasMultipleOrganizations;
    const initials = profile?.first_name && profile?.last_name
        ? `${profile.first_name[0]}${profile.last_name[0]}`
        : profile?.email?.[0].toUpperCase() || 'U';
    const profileDisplayName = profile?.first_name
        ? `${profile.first_name} ${profile.last_name || ''}`.trim()
        : profile?.email?.split('@')[0] || 'User';
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
    // positions can be learned. Settings is reachable from the topbar and the
    // account menu, so it does not take a slot here.
    const places = [
        {
            href: `${basePath}/app/pages`,
            label: 'Apps',
            kind: 'apps' as const,
            active: isActive(`${basePath}/app`),
            visible: canUseApps,
        },
        {
            href: `${basePath}/ai`,
            label: 'Agents',
            kind: 'agents' as const,
            active: isActive(`${basePath}/ai`) || isActive(`${basePath}/agents`),
            visible: canUseAgents,
            onIntent: prefetchAgents,
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
        {
            href: `${basePath}/connectors`,
            label: 'Connectors',
            kind: 'connectors' as const,
            active: isActive(`${basePath}/connectors`),
            visible: canUseConnectors,
        },
    ].filter((place) => place.visible);

    // Route to the dedicated /logout screen so the user gets immediate
    // "Signing you out…" feedback while the session is torn down.
    const handleLogout = () => {
        router.push('/logout');
    };

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

        const params = new URLSearchParams();
        params.set('assistantMessage', prompt);
        params.set('conversationInstructions', getAssistantCreationInstructions(assistantCreationKind));
        params.set('conversationMetadata', JSON.stringify({
            source: 'sidebar_new_menu',
            intent: 'create_resource',
            resource_type: assistantCreationKind,
        }));

        closeAssistantCreation();
        router.push(`${basePath}/conversations/new?${params.toString()}`);
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
                                className="workspace-sidebar-trigger-button custom-focus-ring flex w-full min-w-0 items-center gap-2 rounded-md border border-transparent bg-transparent px-1.5 py-1 text-left text-[var(--text-primary)] transition-colors hover:border-[var(--border-subtle)] hover:bg-[var(--surface-2)] data-[state=open]:border-[var(--border-subtle)] data-[state=open]:bg-[var(--surface-2)]"
                                aria-label={`Switch pod. Current pod: ${podName || 'Current pod'}`}
                            >
                                <ResourceIcon
                                    iconUrl={podIconUrl}
                                    alt={`${podName || 'Current pod'} icon`}
                                    label={podName || 'Current pod'}
                                    className="h-6 w-6 shrink-0 rounded-md border-[color:color-mix(in_srgb,var(--border-subtle)_58%,transparent)] bg-transparent text-[var(--text-tertiary)]"
                                    fallback={
                                        <span className="lemma-pod-badge">
                                            {(podName || 'Pod')
                                                .trim()
                                                .split(/\s+/)
                                                .slice(0, 2)
                                                .map((part) => part.charAt(0).toUpperCase())
                                                .join('') || 'P'}
                                        </span>
                                    }
                                />
                                <span className="block min-w-0 flex-1 truncate text-sm font-medium leading-5 text-[var(--text-primary)]">
                                    {podName || 'Current pod'}
                                </span>
                                <ChevronDown className="h-4 w-4 shrink-0 text-[var(--text-tertiary)]" />
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
                            className="workspace-sidebar-primary-action custom-focus-ring flex h-8 min-w-0 flex-1 items-center gap-2 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-1)] px-2.5 text-sm font-medium text-[var(--text-primary)] transition-colors hover:border-[var(--border-strong)] hover:bg-[var(--surface-2)]"
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
                                        'workspace-sidebar-primary-action custom-focus-ring flex h-8 shrink-0 items-center justify-center rounded-md border border-[var(--border-subtle)] bg-[var(--surface-1)] text-[var(--text-tertiary)] transition-colors hover:border-[var(--border-strong)] hover:bg-[var(--surface-2)] hover:text-[var(--text-primary)] data-[state=open]:bg-[var(--surface-2)]',
                                        canWriteConversations ? 'w-8' : 'min-w-0 flex-1 gap-2 px-2.5 text-sm',
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
                                    {canWriteConversations ? (
                                        <DropdownMenu.Item
                                            onSelect={() => router.push(`${basePath}/recipes`)}
                                            className="lemma-menu-row px-2"
                                        >
                                            <ProductIcon kind="apps" size="xs" />
                                            Add from a starter
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

                    <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3 pt-2">
                        {isLoadingConversationHistory && !hasVisibleConversations ? (
                            /* Rows at the row's own height, dot gutter included —
                               a one-line caption is a different box from the list
                               it becomes, and this rail is narrow enough that the
                               swap reads as the whole nav resettling. */
                            <div role="status" aria-label="Loading conversations">
                                <div className="px-2 pb-1 text-xs leading-5 text-[var(--text-tertiary)]">
                                    Recents
                                </div>
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
                                <div className="px-2 pb-1 text-xs leading-5 text-[var(--text-tertiary)]">
                                    Recents
                                </div>
                                {visibleConversations.map((conversation) => (
                                    <ConversationRow
                                        key={conversation.id}
                                        conversation={conversation}
                                        active={isConversationRoute && openedConversationId === conversation.id}
                                        onOpen={() => openConversation(conversation.id)}
                                    />
                                ))}
                            </>
                        )}
                        {!hasFilter ? (
                            <Link
                                href={`${basePath}/conversations`}
                                className="lemma-sidebar-row workspace-sidebar-conversation-row workspace-sidebar-show-more custom-focus-ring text-[var(--text-tertiary)] hover:text-[var(--text-primary)]"
                            >
                                {/* Empty dot gutter so the label starts on the
                                    same x as the titles above it. */}
                                <span className="w-3.5 shrink-0" aria-hidden="true" />
                                <span className="min-w-0 flex-1 truncate">All conversations</span>
                            </Link>
                        ) : null}
                    </div>
                </>
            ) : (
                <div className="min-h-0 flex-1" />
            )}

            <div className="workspace-sidebar-places shrink-0 px-3 pb-3 pt-3">
                <LocalSettingsButton className="mb-1" />
                <div className="space-y-0.5">
                    {places.map((place) => (
                        <PlaceLink key={place.href} {...place} />
                    ))}
                </div>
            </div>

            <div className="flex shrink-0 items-center gap-1.5 border-t border-[color:color-mix(in_srgb,var(--border-subtle)_62%,transparent)] px-3 pb-3 pt-2">
                <Link
                    href="/home"
                    aria-label="Go to Lemma home"
                    title="Lemma home"
                    className="workspace-sidebar-trigger-button custom-focus-ring flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[var(--text-primary)] transition-colors hover:bg-[var(--surface-2)]"
                >
                    <Logo size="xs" variant="mark-only" />
                </Link>
                <DropdownMenu.Root>
                    <DropdownMenu.Trigger asChild>
                        <button
                            className="workspace-sidebar-trigger-button custom-focus-ring flex h-9 min-w-0 flex-1 items-center gap-2 rounded-lg px-1.5 text-left transition-colors hover:bg-[var(--surface-2)]"
                            aria-label={`Open account menu for ${profileDisplayName}`}
                            title={profileDisplayName}
                        >
                            <Avatar className="h-7 w-7 border border-[var(--border-subtle)]">
                                <AvatarFallback className="bg-[var(--surface-2)] text-xs text-[var(--text-secondary)]">
                                    {profile ? initials : <User className="h-4 w-4" />}
                                </AvatarFallback>
                            </Avatar>
                            <span className="min-w-0 flex-1 truncate text-sm font-medium text-[var(--text-primary)]">
                                {profileDisplayName}
                            </span>
                        </button>
                    </DropdownMenu.Trigger>
                    <DropdownMenu.Portal>
                        <DropdownMenu.Content
                            align="start"
                            side="top"
                            sideOffset={8}
                            className="surface-panel z-50 w-56 py-1 shadow-[var(--shadow-lg)]"
                        >
                            <div className="px-3 py-2">
                                <p className="truncate text-sm font-medium text-[var(--text-primary)]">
                                    {profile?.first_name ? `${profile.first_name} ${profile.last_name || ''}`.trim() : profile?.email}
                                </p>
                                <p className="truncate text-xs text-[var(--text-tertiary)]">{profile?.email}</p>
                            </div>
                            <DropdownMenu.Separator className="my-1 h-px bg-[var(--border-subtle)]" />
                            <DropdownMenu.Item asChild>
                                <Link
                                    href="/profile"
                                    className="lemma-menu-row px-3"
                                >
                                    <User className="h-4 w-4" />
                                    Profile settings
                                </Link>
                            </DropdownMenu.Item>
                            <DropdownMenu.Separator className="my-1 h-px bg-[var(--border-subtle)]" />
                            <DropdownMenu.Item
                                onSelect={handleLogout}
                                className="hover-state-error focus-state-error flex cursor-pointer items-center gap-2 px-3 py-2 text-sm text-[var(--state-error)] outline-none transition-colors"
                            >
                                <LogOut className="h-4 w-4" />
                                Log out
                            </DropdownMenu.Item>
                        </DropdownMenu.Content>
                    </DropdownMenu.Portal>
                </DropdownMenu.Root>
                <ThemeToggle variant="icon" />
            </div>
        </aside>
    );
}

/**
 * One line and a dot. State is carried by a 6px mark in a fixed left gutter
 * rather than by a second line of text — a title plus metadata per row turns a
 * list you scan into a list you read, and the whole point of this column is
 * that you can scan it.
 */
/** Conversation titles vary in length, so the placeholders do too. */
const CONVERSATION_ROW_SKELETON_WIDTHS = ['w-3/5', 'w-5/12', 'w-2/3', 'w-1/2', 'w-7/12', 'w-1/2'];

function ConversationRow({
    conversation,
    active,
    onOpen,
}: {
    conversation: Conversation;
    active: boolean;
    onOpen: () => void;
}) {
    const statusView = getConversationStatusView(conversation.status);
    const filled = statusView.isActive || statusView.isAwaiting || statusView.state === 'failed';

    return (
        <button
            type="button"
            onClick={onOpen}
            data-active={active ? 'true' : undefined}
            title={conversation.title || 'Untitled conversation'}
            className="lemma-sidebar-row workspace-sidebar-conversation-row custom-focus-ring"
        >
            <span className="flex w-3.5 shrink-0 items-center justify-center" aria-hidden="true">
                <span
                    className={cn(
                        'block h-1.5 w-1.5 rounded-full',
                        filled ? 'bg-current' : 'border border-current opacity-45',
                        statusView.tone === 'live' && 'text-[var(--delight)]',
                        statusView.tone === 'warning' && 'text-[var(--state-warning)]',
                        statusView.tone === 'danger' && 'text-[var(--state-error)]',
                        statusView.isActive && 'lemma-live-pulse',
                    )}
                />
            </span>
            <span className="min-w-0 flex-1 truncate">
                {conversation.title || 'Untitled conversation'}
            </span>
            {filled ? <span className="sr-only">{statusView.label}</span> : null}
        </button>
    );
}

function PodSwitcherMenu({
    pods,
    podGroups,
    isLoading,
    showOrganizationLabels,
    podId,
    router,
    side,
    onShare,
}: {
    pods: Array<{ id: string; name: string }>;
    podGroups: AccessiblePodGroup[];
    isLoading: boolean;
    showOrganizationLabels?: boolean;
    podId: string;
    router: ReturnType<typeof useRouter>;
    side: 'top' | 'bottom';
    onShare: () => void;
}) {
    return (
        <DropdownMenu.Portal>
            <DropdownMenu.Content
                align="start"
                side={side}
                sideOffset={8}
                className="surface-panel z-50 flex w-72 flex-col p-1 shadow-[var(--shadow-lg)]"
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
                <div className="min-h-0 max-h-96 overflow-y-auto">
                    {isLoading ? (
                        <div className="px-2 py-2 text-sm text-[var(--text-tertiary)]">Loading pods…</div>
                    ) : pods.length === 0 ? (
                        <div className="px-2 py-2 text-sm text-[var(--text-tertiary)]">No pods yet.</div>
                    ) : null}
                    {showOrganizationLabels ? (
                        podGroups.map((group) => group.pods.length > 0 ? (
                            <div key={group.organization.id}>
                                <div className="px-2 pt-2 pb-1 text-xs font-medium uppercase tracking-normal text-[var(--text-tertiary)]">
                                    {group.organization.name}
                                </div>
                                {group.pods.map((pod) => (
                                    <PodSwitcherMenuItem key={pod.id} pod={pod} podId={podId} />
                                ))}
                            </div>
                        ) : null)
                    ) : (
                        pods.map((pod) => (
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
                <DropdownMenu.Item
                    onSelect={() => router.push('/create-pod')}
                    className="flex shrink-0 cursor-pointer items-center gap-2 rounded-lg px-2 py-2 text-sm font-medium text-[var(--delight)] outline-none transition-colors hover:bg-[var(--delight-soft)]"
                >
                    <Plus className="h-3.5 w-3.5" />
                    New pod
                </DropdownMenu.Item>
            </DropdownMenu.Content>
        </DropdownMenu.Portal>
    );
}

function PodSwitcherMenuItem({
    pod,
    podId,
}: {
    pod: { id: string; name: string };
    podId: string;
}) {
    return (
        <DropdownMenu.Item asChild>
            <Link
                href={`/pod/${pod.id}`}
                className="lemma-menu-row lemma-menu-row-between"
            >
                <span className="truncate">{toDisplayLabel(pod.name)}</span>
                {pod.id === podId ? (
                    <span className="flex shrink-0 items-center gap-1.5 text-xs text-[var(--text-tertiary)]">
                        <Check className="h-3.5 w-3.5 text-[var(--delight)]" />
                        Current
                    </span>
                ) : null}
            </Link>
        </DropdownMenu.Item>
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
            <span className="flex min-w-0 items-center gap-3">
                <ProductIcon kind={kind} size="xs" state={active ? 'selected' : 'default'} />
                <span className="truncate">{label}</span>
            </span>
        </Link>
    );
}
