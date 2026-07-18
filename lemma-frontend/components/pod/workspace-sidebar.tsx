'use client';

import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
    Check,
    ChevronDown,
    Home,
    LogOut,
    PanelLeftClose,
    Plus,
    Share2,
    Upload,
    User,
} from '@/components/ui/icons';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';

import { useAIAssistant } from '@/components/ai/ai-assistant-context';
import { Logo } from '@/components/brand/logo';
import { FileTypeIcon } from '@/components/documents/file-type-icon';
import { ShareSheet } from '@/components/bundle/share-sheet';
import { ImportDialog } from '@/components/bundle/import-dialog';
import { ProductIcon, type ProductIconKind } from '@/components/pod/product-icon';
import { SidebarEmptyState } from '@/components/shared/empty-state';
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
import { useApp } from '@/components/app/app-context';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { cn } from '@/lib/utils';
import { agentsQueryOptions, useAgents } from '@/lib/hooks/use-agents';
import {
    tableQueryOptions,
    tableRecordsQueryOptions,
    tablesQueryOptions,
    useDatastoreFiles,
    useTables,
} from '@/lib/hooks/use-datastores';
import { flowsQueryOptions, useFlows } from '@/lib/hooks/use-flows';
import { useAccessiblePods, type AccessiblePodGroup } from '@/lib/hooks/use-pods';
import { useProfile } from '@/lib/hooks/use-user';
import { useScopedConversations } from '@/lib/hooks/use-assistants';
import { mergeSidebarConversations } from '@/lib/assistant/sidebar-conversations';
import { getAppRecipeExamples } from '@/lib/recipes/recipes';
import type { DatastoreFile } from '@/lib/types';
import { getConversationStatusView } from '@/lib/utils/conversations';

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
const PERSONAL_FILES_ROOT = '/me';
const PERSONAL_FILES_LABEL = 'Personal files';

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

function isFolder(file: DatastoreFile): boolean {
    return file.kind === 'FOLDER';
}

function getFilePath(file: DatastoreFile): string {
    return file.path || file.id;
}

function isPersonalRootPath(path: string | null | undefined): boolean {
    if (!path) return false;
    const normalized = path.startsWith('/') ? path : `/${path}`;
    return normalized === PERSONAL_FILES_ROOT;
}

function getDocEntryLabel(file: DatastoreFile): string {
    return isFolder(file) && isPersonalRootPath(getFilePath(file)) ? PERSONAL_FILES_LABEL : file.name;
}

export function WorkspaceSidebar({ podId, podName, podIconUrl, onCollapse }: WorkspaceSidebarProps) {
    const pathname = usePathname();
    const router = useRouter();
    const queryClient = useQueryClient();
    const searchParams = useSearchParams();
    const searchParamsString = searchParams.toString();
    const [assistantCreationKind, setAssistantCreationKind] = useState<AssistantCreationKind | null>(null);
    const [assistantCreationPrompt, setAssistantCreationPrompt] = useState('');
    const [bundleShareOpen, setBundleShareOpen] = useState(false);
    const [bundleImportOpen, setBundleImportOpen] = useState(false);
    const [podSwitcherOpen, setPodSwitcherOpen] = useState(false);
    const { pages = [] } = useApp();
    const { data: podsData, isLoading: isLoadingPods } = useAccessiblePods({
        enabled: podSwitcherOpen,
    });
    const { data: profile } = useProfile();
    const podAccess = usePodAccess(podId);
    const canUseConversations = podAccess.canAccessRoute('conversations');
    const canWriteConversations = podAccess.can('conversation.write');
    const canUseAgents = podAccess.canAccessRoute('agents');
    const canUseWorkflows = podAccess.canAccessRoute('workflows');
    const canUseSchedules = podAccess.canAccessRoute('schedules');
    const canUseConnectors = podAccess.canAccessRoute('connectors');
    const canUseData = podAccess.canAccessRoute('data');
    const canUseDocs = podAccess.canAccessRoute('files');
    const canUseApps = podAccess.canAccessRoute('apps');
    const canUseSurfaces = podAccess.canAccessRoute('surfaces');
    const canUseSettings = podAccess.canAccessRoute('settings');
    const canCreateAgents = podAccess.can('agent.create');
    const canCreateApps = podAccess.can('app.create');
    const canCreateWorkflows = podAccess.can('workflow.create');
    const canCreateSchedules = podAccess.can('schedule.create');
    const canCreateTables = podAccess.can('datastore.table.create');
    const basePath = `/pod/${podId}`;
    const isActive = (href: string) => pathname === href || pathname.startsWith(`${href}/`);
    const isDocsRoute = isActive(`${basePath}/files`);
    const isAgentsRoute = isActive(`${basePath}/ai`) || isActive(`${basePath}/agents`);
    const isWorkflowsRoute = isActive(`${basePath}/flows`);
    const isDataRoute = isActive(`${basePath}/data`) || isActive(`${basePath}/datastores`);
    const isAppsRoute = isActive(`${basePath}/app`);
    const isConnectorsRoute = isActive(`${basePath}/connectors`);
    const isKitsRoute = isActive(`${basePath}/kits`) || isActive(`${basePath}/recipes`);
    const isSchedulesRoute = isActive(`${basePath}/schedules`);
    const isConversationRoute = isActive(`${basePath}/conversations`);
    const isPodHome = pathname === basePath || pathname === `${basePath}/`;
    const hasRouteWorktree = isDocsRoute || isAgentsRoute || isWorkflowsRoute || isDataRoute || isAppsRoute || isConnectorsRoute || isKitsRoute || isSchedulesRoute;
    const { data: agentsData } = useAgents(canUseAgents && isAgentsRoute ? podId : undefined);
    const { data: tablesData } = useTables(canUseData && isDataRoute ? podId : undefined);
    const { data: flowsData } = useFlows(canUseWorkflows && isWorkflowsRoute ? podId : undefined);
    const {
        conversations: controllerConversations,
        openedConversationId,
        isLoadingConversations,
    } = useAIAssistant();
    const shouldLoadSidebarHistory = canUseConversations && !isConversationRoute;
    const {
        data: sidebarConversationHistory,
        isLoading: isLoadingSidebarConversationHistory,
    } = useScopedConversations(
        { podId },
        { limit: 20, enabled: shouldLoadSidebarHistory },
    );
    const conversations = useMemo(
        () => mergeSidebarConversations(
            sidebarConversationHistory?.items || [],
            controllerConversations,
        ),
        [controllerConversations, sidebarConversationHistory?.items],
    );

    const pods = podsData?.items || [];
    const podGroups = podsData?.groups || [];
    const showPodOrganizationLabels = podsData?.hasMultipleOrganizations;
    const agents = agentsData?.items || [];
    const tables = tablesData?.items || [];
    const flows = flowsData || [];
    const initials = profile?.first_name && profile?.last_name
        ? `${profile.first_name[0]}${profile.last_name[0]}`
        : profile?.email?.[0].toUpperCase() || 'U';
    const profileDisplayName = profile?.first_name
        ? `${profile.first_name} ${profile.last_name || ''}`.trim()
        : profile?.email?.split('@')[0] || 'User';
    const assistantCreationCopy = assistantCreationKind ? ASSISTANT_CREATION_COPY[assistantCreationKind] : null;

    const canShowCreateMenu = canWriteConversations || canCreateAgents || canCreateApps || canCreateWorkflows || canCreateSchedules || canCreateTables;
    const visibleConversations = canUseConversations ? conversations.slice(0, hasRouteWorktree ? 3 : 7) : [];
    const docsFolderPath = isDocsRoute ? searchParams.get('folder') : null;
    const docsDirectoryPath = isDocsRoute ? (docsFolderPath || '/') : '/';
    const selectedDocPath = isDocsRoute ? searchParams.get('file') : null;
    const { data: routeDocsFilesData, isLoading: isLoadingRouteDocsFiles } = useDatastoreFiles(
        podId,
        canUseDocs && isDocsRoute ? DATASTORE_NAME : undefined,
        {
            directory_path: docsDirectoryPath,
            limit: 200,
        }
    );

    const docsEntries = useMemo(() => {
        return [...(routeDocsFilesData?.items || [])].sort((left, right) => {
            if (isFolder(left) !== isFolder(right)) return isFolder(left) ? -1 : 1;
            return left.name.localeCompare(right.name);
        });
    }, [routeDocsFilesData?.items]);

    const updateQuery = (
        updates: Record<string, string | null>,
        options: { history?: 'push' | 'replace'; targetPath?: string } = {}
    ) => {
        const nextParams = new URLSearchParams(searchParamsString);
        Object.entries(updates).forEach(([key, value]) => {
            if (value === null || value === '') nextParams.delete(key);
            else nextParams.set(key, value);
        });
        const nextQuery = nextParams.toString();
        const nextUrl = `${options.targetPath || pathname}${nextQuery ? `?${nextQuery}` : ''}`;
        if (options.history === 'replace') router.replace(nextUrl, { scroll: false });
        else router.push(nextUrl, { scroll: false });
    };

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

    const rails = [
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
            href: `${basePath}/schedules`,
            label: 'Schedules',
            kind: 'schedules' as const,
            active: isActive(`${basePath}/schedules`),
            visible: canUseSchedules,
        },
        {
            href: `${basePath}/connectors`,
            label: 'Connectors',
            kind: 'connectors' as const,
            active: isConnectorsRoute,
            visible: canUseConnectors,
        },
        {
            href: `${basePath}/surfaces`,
            label: 'Surfaces',
            kind: 'surfaces' as const,
            active: isActive(`${basePath}/surfaces`) || isActive(`${basePath}/channels`),
            visible: canUseSurfaces,
        },
        {
            href: `${basePath}/settings`,
            label: 'Settings',
            kind: 'settings' as const,
            active: isActive(`${basePath}/settings`),
            visible: canUseSettings,
        },
    ].filter((rail) => rail.visible);

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

    const startFullPageConversation = () => {
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

    const openDocsFolder = (folderPath: string | null) => {
        updateQuery(
            {
                namespace: null,
                folder: folderPath,
                file: null,
            },
            { targetPath: `${basePath}/files` }
        );
    };

    const openDocFile = (filePath: string) => {
        updateQuery({ namespace: null, file: filePath }, { targetPath: `${basePath}/files` });
    };

    const renderRouteWorktree = () => {
        if (isDocsRoute && canUseDocs) {
            return (
                <RouteWorktree>
                    <div className="space-y-0.5">
                        {docsFolderPath ? (
                            <button
                                type="button"
                                onClick={() => openDocsFolder(null)}
                                className="lemma-sidebar-row lemma-sidebar-row-sm custom-focus-ring text-[var(--text-tertiary)]"
                            >
                                <ProductIcon kind="folders" size="xs" />
                                Back to Files
                            </button>
                        ) : null}
                        {isLoadingRouteDocsFiles ? (
                            <div className="px-2 py-1.5 text-xs text-[var(--text-tertiary)]">Loading files</div>
                        ) : docsEntries.length === 0 ? (
                            <SidebarEmptyState>
                                No files here yet.
                            </SidebarEmptyState>
                        ) : (
                            docsEntries.map((entry) => {
                                const folder = isFolder(entry);
                                const path = getFilePath(entry);
                                const label = getDocEntryLabel(entry);
                                return (
                                    <button
                                        key={entry.id}
                                        type="button"
                                        title={path}
                                        onClick={() => folder ? openDocsFolder(path) : openDocFile(path)}
                                        data-active={selectedDocPath === path ? 'true' : undefined}
                                        className="lemma-sidebar-row lemma-sidebar-row-sm custom-focus-ring"
                                    >
                                        {folder ? (
                                            <ProductIcon
                                                kind="folders"
                                                size="xs"
                                                state={selectedDocPath === path ? 'selected' : 'default'}
                                            />
                                        ) : (
                                            <FileTypeIcon filename={label} size="sm" />
                                        )}
                                        <span className="min-w-0 flex-1 truncate">{label}</span>
                                    </button>
                                );
                            })
                        )}
                    </div>
                </RouteWorktree>
            );
        }

        if (isAgentsRoute && canUseAgents) {
            return (
                <RouteWorktree>
                    {agents.map((agent) => (
                        <WorktreeLink
                            key={agent.name || agent.id}
                            href={`${basePath}/agents/${encodeURIComponent(agent.name || agent.id)}`}
                            label={toDisplayLabel(agent.name || agent.id)}
                            kind="agents"
                            active={pathname.endsWith(`/agents/${encodeURIComponent(agent.name || agent.id)}`)}
                        />
                    ))}
                    {agents.length === 0 ? <WorktreeEmpty label="No agents yet" /> : null}
                </RouteWorktree>
            );
        }

        if (isWorkflowsRoute && canUseWorkflows) {
            return (
                <RouteWorktree>
                    {flows.map((flow) => (
                        <WorktreeLink
                            key={flow.name || flow.id}
                            href={`${basePath}/flows/${encodeURIComponent(flow.name || flow.id)}`}
                            label={toDisplayLabel(flow.name || flow.id)}
                            kind="workflows"
                            active={pathname.endsWith(`/flows/${encodeURIComponent(flow.name || flow.id)}`)}
                        />
                    ))}
                    {flows.length === 0 ? <WorktreeEmpty label="No workflows yet" /> : null}
                </RouteWorktree>
            );
        }

        if (isDataRoute && canUseData) {
            return (
                <RouteWorktree>
                    {tables.map((table) => (
                        <WorktreeLink
                            key={table.name}
                            href={`${basePath}/data?tab=${encodeURIComponent(table.name)}`}
                            label={toDisplayLabel(table.name)}
                            kind="tables"
                            active={searchParams.get('tab') === table.name}
                        />
                    ))}
                    {tables.length === 0 ? <WorktreeEmpty label="No tables yet" /> : null}
                </RouteWorktree>
            );
        }

        if (isAppsRoute && canUseApps) {
            return (
                <RouteWorktree>
                    {pages.map((page) => (
                        <WorktreeLink
                            key={page.slug}
                            href={`${basePath}/app/view?page=${encodeURIComponent(page.slug)}`}
                            label={toDisplayLabel(page.title || page.slug)}
                            kind="apps"
                            active={searchParams.get('page') === page.slug}
                        />
                    ))}
                    {pages.length === 0 ? <WorktreeEmpty label="No app pages yet" /> : null}
                </RouteWorktree>
            );
        }

        if (isConnectorsRoute && canUseConnectors) {
            return null;
        }

        if (isKitsRoute) {
            return (
                <RouteWorktree>
                    <WorktreeEmpty label="Preview a kit, then install or customize it." />
                </RouteWorktree>
            );
        }

        if (isSchedulesRoute && canUseSchedules) {
            return (
                <RouteWorktree>
                    <WorktreeEmpty label="Schedule list is on the main page" />
                </RouteWorktree>
            );
        }

        return null;
    };

    const routeWorktree = renderRouteWorktree();

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
                                <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                                    <span className="whitespace-nowrap text-xs font-medium leading-none text-[var(--text-tertiary)]">
                                        Switch pod
                                    </span>
                                    <span className="block truncate text-sm font-medium leading-4 text-[var(--text-primary)]">
                                        {podName || 'Current pod'}
                                    </span>
                                </span>
                                <ChevronDown className="h-4 w-4 shrink-0 text-[var(--text-secondary)]" />
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
                        />
                    </DropdownMenu.Root>
                </div>
                <button
                    type="button"
                    onClick={() => setBundleShareOpen(true)}
                    className="lemma-shell-icon-button custom-focus-ring h-8 w-8 shrink-0 self-center text-[var(--text-tertiary)] hover:text-[var(--action-primary)]"
                    aria-label="Share pod"
                    title="Share pod"
                >
                    <Share2 className="h-4 w-4" strokeWidth={1.8} />
                </button>
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
            <div className="px-3 pb-3 pt-3">
                <Link
                    href={basePath}
                    data-active={isPodHome ? 'true' : undefined}
                    aria-current={isPodHome ? 'page' : undefined}
                    className="lemma-sidebar-row lemma-sidebar-row-sm custom-focus-ring mb-0.5 font-medium text-[var(--text-secondary)]"
                >
                    <Home className="h-3.5 w-3.5 shrink-0" weight={isPodHome ? 'fill' : 'regular'} />
                    <span className="min-w-0 flex-1 truncate">Home</span>
                </Link>
                {canShowCreateMenu ? (
                    <div className="flex items-center gap-2">
                        <DropdownMenu.Root>
                            <DropdownMenu.Trigger asChild>
                                <button
                                    type="button"
                                    className="lemma-sidebar-row lemma-sidebar-row-sm lemma-sidebar-row-inline custom-focus-ring flex-1 font-medium text-[var(--text-secondary)]"
                                >
                                    <Plus className="h-3.5 w-3.5 shrink-0" />
                                    <span className="min-w-0 flex-1 truncate">New</span>
                                </button>
                            </DropdownMenu.Trigger>
                            <DropdownMenu.Portal>
                                <DropdownMenu.Content
                                    align="start"
                                    side="bottom"
                                    sideOffset={8}
                                    className="surface-panel z-50 w-56 p-1 shadow-[var(--shadow-lg)]"
                                >
                                    {canWriteConversations ? (
                                        <DropdownMenu.Item
                                            onSelect={startFullPageConversation}
                                            className="lemma-menu-row px-2"
                                        >
                                            <ProductIcon kind="conversation" size="xs" />
                                            New conversation
                                        </DropdownMenu.Item>
                                    ) : null}
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
                                    {canCreateSchedules ? (
                                        <DropdownMenu.Item
                                            onSelect={() => router.push(`${basePath}/schedules/new`)}
                                            className="lemma-menu-row px-2"
                                        >
                                            <ProductIcon kind="schedules" size="xs" />
                                            New schedule
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
                                            Browse recipes
                                        </DropdownMenu.Item>
                                    ) : null}
                                    <DropdownMenu.Separator className="my-1 h-px bg-[var(--border-subtle)]" />
                                    <DropdownMenu.Item
                                        onSelect={() => setBundleImportOpen(true)}
                                        className="lemma-menu-row px-2"
                                    >
                                        <Upload className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" />
                                        Install a bundle
                                    </DropdownMenu.Item>
                                </DropdownMenu.Content>
                            </DropdownMenu.Portal>
                        </DropdownMenu.Root>
                    </div>
                ) : null}
            </div>

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
                                variant="ghost"
                                size="sm"
                                className="text-[var(--text-tertiary)]"
                                onClick={startManualCreation}
                            >
                                {assistantCreationCopy.manualLabel}
                            </Button>
                        ) : (
                            <span aria-hidden="true" />
                        )}
                        <Button
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
            />
            {canShowCreateMenu ? (
                <ImportDialog
                    podId={podId}
                    podName={podName}
                    open={bundleImportOpen}
                    onOpenChange={setBundleImportOpen}
                />
            ) : null}

            <div className="min-h-0 flex-1 overflow-y-auto px-3">
                {routeWorktree}

                <div className={cn('space-y-px pb-3', routeWorktree && 'mt-4 border-t border-[var(--border-subtle)] pt-3')}>
                    {(isLoadingConversations || isLoadingSidebarConversationHistory) && visibleConversations.length === 0 ? (
                        <div className="px-2 py-1.5 text-xs text-[var(--text-tertiary)]">Loading conversations</div>
                    ) : null}
                    {canWriteConversations ? (
                        <button
                            type="button"
                            onClick={startConversation}
                            className="lemma-sidebar-row lemma-sidebar-row-sm custom-focus-ring font-normal text-[var(--text-tertiary)]"
                        >
                            <span className="min-w-0 flex-1 truncate">Start a conversation</span>
                        </button>
                    ) : null}
                    {visibleConversations.map((conversation) => {
                        const statusView = getConversationStatusView(conversation.status);
                        const showStatusLabel = statusView.isActive || statusView.isAwaiting || statusView.state === 'failed';

                        return (
                            <button
                                key={conversation.id}
                                type="button"
                                onClick={() => openConversation(conversation.id)}
                                data-active={isConversationRoute && openedConversationId === conversation.id ? 'true' : undefined}
                                className="lemma-sidebar-row lemma-sidebar-row-sm custom-focus-ring font-normal"
                            >
                                <span className="min-w-0 flex-1 truncate">{conversation.title || 'Untitled conversation'}</span>
                                {showStatusLabel ? (
                                    <span
                                        className={cn(
                                            'shrink-0 text-xs',
                                            statusView.tone === 'live' && 'text-[var(--delight)]',
                                            statusView.tone === 'warning' && 'text-[var(--state-warning)]',
                                            statusView.tone === 'danger' && 'text-[var(--state-error)]'
                                        )}
                                    >
                                        {statusView.dotLabel}
                                    </span>
                                ) : null}
                            </button>
                        );
                    })}
                </div>
            </div>

            <div className="shrink-0 border-t border-[color:color-mix(in_srgb,var(--border-subtle)_62%,transparent)] px-3 pb-3 pt-3">
                <div className="space-y-0.5">
                    {rails.map((rail) => (
                        <RailLink key={rail.href} {...rail} />
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

function PodSwitcherMenu({
    pods,
    podGroups,
    isLoading,
    showOrganizationLabels,
    podId,
    router,
    side,
}: {
    pods: Array<{ id: string; name: string }>;
    podGroups: AccessiblePodGroup[];
    isLoading: boolean;
    showOrganizationLabels?: boolean;
    podId: string;
    router: ReturnType<typeof useRouter>;
    side: 'top' | 'bottom';
}) {
    return (
        <DropdownMenu.Portal>
            <DropdownMenu.Content
                align="start"
                side={side}
                sideOffset={8}
                className="surface-panel z-50 flex w-72 flex-col p-1 shadow-[var(--shadow-lg)]"
            >
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

function RouteWorktree({
    children,
}: {
    children: ReactNode;
}) {
    return (
        <div>
            <div className="space-y-2">
                {children}
            </div>
        </div>
    );
}

function WorktreeLink(props: {
    href: string;
    label: string;
    kind?: ProductIconKind;
    active?: boolean;
}) {
    const { href, label, kind = 'docs', active } = props;

    return (
        <Link
            href={href}
            data-active={active ? 'true' : undefined}
            className="lemma-product-nav-item lemma-sidebar-row lemma-sidebar-row-sm custom-focus-ring group"
        >
            <ProductIcon kind={kind} size="xs" state={active ? 'selected' : 'default'} />
            <span className="min-w-0 flex-1 truncate">{label}</span>
        </Link>
    );
}

function WorktreeEmpty({ label }: { label: string }) {
    return (
        <SidebarEmptyState>{label}</SidebarEmptyState>
    );
}

function RailLink(props: {
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
