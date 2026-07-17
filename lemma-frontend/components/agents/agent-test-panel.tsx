'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAssistantController, useConversationMessages } from 'lemma-sdk/react';
import { useAgent } from '@/lib/hooks/use-agents';
import { useMessages } from '@/lib/hooks/use-assistants';
import { useAccounts, useConnectors, useAuthConfigs, useCreateConnectRequest } from '@/lib/hooks/use-connectors';
import { usePod } from '@/lib/hooks/use-pods';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { buildScopedConversationHref } from '@/lib/assistant/conversation-composer-context';
import { useInfiniteScroll } from '@/lib/hooks/use-infinite-scroll';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/shared/empty-state';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { AssistantExperienceView } from '@/components/lemma/assistant/assistant-experience';
import type { AssistantControllerView } from '@/components/lemma/assistant/assistant-types';
import { Agent, ConnectorMode } from '@/lib/types';
import type { Message } from '@/lib/types';
import { cn } from '@/lib/utils';
import {
    isRecord,
    latestAssistantText,
    taskConversationOutput,
} from '@/lib/utils/agent-output';
import {
    AlertCircle,
    Link as LinkIcon,
    Loader2,
    Maximize2,
    MessageSquare,
    Play,
    Plus,
    Search,
    X,
} from '@/components/ui/icons';

interface AgentTestPanelProps {
    podId: string;
    agentName: string;
    agentOverride?: Agent | null;
    openConversationId?: string | null;
    openConversationRequestKey?: number;
    isFullView?: boolean;
    onToggleFullView?: () => void;
    onClose?: () => void;
}

function getSchemaOrderedKeys(schema: unknown): string[] {
    if (!isRecord(schema) || !isRecord(schema.properties)) return [];
    return Object.keys(schema.properties);
}

function hasSchemaProperties(schema: unknown): boolean {
    return getSchemaOrderedKeys(schema).length > 0;
}

function formatFieldLabel(key: string): string {
    return key
        .replace(/[_-]+/g, ' ')
        .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
        .replace(/\s+/g, ' ')
        .trim()
        .replace(/\b\w/g, (char) => char.toUpperCase());
}

function getSchemaPropertyType(property: Record<string, unknown>): string {
    const type = property.type;
    if (Array.isArray(type)) return type.find((entry): entry is string => typeof entry === 'string' && entry !== 'null') || 'string';
    if (typeof type === 'string') return type;
    if (Array.isArray(property.enum)) return 'enum';
    return 'string';
}

function coerceFormValue(value: unknown, property: unknown): unknown {
    if (!isRecord(property)) return value;
    if (value === '') return undefined;

    const type = getSchemaPropertyType(property);
    if (type === 'number' || type === 'integer') {
        const parsed = typeof value === 'number' ? value : Number(value);
        return Number.isFinite(parsed) ? parsed : value;
    }
    if (type === 'boolean') {
        if (typeof value === 'boolean') return value;
        if (typeof value === 'string') return value === 'true';
    }
    if ((type === 'array' || type === 'object') && typeof value === 'string') {
        try {
            return JSON.parse(value);
        } catch {
            return value;
        }
    }

    return value;
}

function buildInputDataFromForm(formData: Record<string, unknown>, schema: unknown): Record<string, unknown> {
    const properties = isRecord(schema) && isRecord(schema.properties) ? schema.properties : {};

    return Object.fromEntries(
        Object.entries(formData)
            .map(([key, value]) => [key, coerceFormValue(value, properties[key])] as const)
            .filter(([, value]) => typeof value !== 'undefined')
    );
}

function formatAgentRunInput(inputData: Record<string, unknown>): string {
    const keys = Object.keys(inputData);
    if (keys.length === 0) return 'Run this agent.';

    const promptKey = ['prompt', 'message', 'content'].find((key) => keys.includes(key));
    if (promptKey && keys.length === 1 && typeof inputData[promptKey] === 'string') {
        return String(inputData[promptKey]).trim();
    }

    return `Run input:\n${JSON.stringify(inputData, null, 2)}`;
}

function getRequiredKeys(schema: unknown): string[] {
    if (!isRecord(schema) || !Array.isArray(schema.required)) return [];
    return schema.required.filter((item): item is string => typeof item === 'string');
}

function formatRelativeTime(value?: string | null): string | null {
    if (!value) return null;
    const date = new Date(value);
    const timestamp = date.getTime();
    if (Number.isNaN(timestamp)) return null;

    const diffMs = timestamp - Date.now();
    const diffMinutes = Math.round(diffMs / (60 * 1000));
    const absMinutes = Math.abs(diffMinutes);

    if (absMinutes < 1) return 'just now';
    if (absMinutes < 60) return `${absMinutes}m ${diffMinutes < 0 ? 'ago' : 'from now'}`;

    const diffHours = Math.round(absMinutes / 60);
    if (diffHours < 24) return `${diffHours}h ${diffMinutes < 0 ? 'ago' : 'from now'}`;

    const diffDays = Math.round(diffHours / 24);
    return `${diffDays}d ${diffMinutes < 0 ? 'ago' : 'from now'}`;
}

export function AgentTestPanel({
    podId,
    agentName,
    agentOverride,
    openConversationId,
    openConversationRequestKey,
    isFullView,
    onToggleFullView,
    onClose,
}: AgentTestPanelProps) {
    const router = useRouter();
    const { data: fetchedAgent } = useAgent(podId, agentName);
    const agent = agentOverride ?? fetchedAgent;
    const { data: pod } = usePod(podId);
    const organizationId = pod?.organization_id;
    const { data: allAccounts } = useAccounts({ organizationId, limit: 100 });
    const { data: authConfigs = [] } = useAuthConfigs({ organizationId, limit: 100 });
    const { data: allConnectors } = useConnectors({ limit: 100 });
    const createConnectRequest = useCreateConnectRequest(organizationId);

    const [formData, setFormData] = useState<Record<string, unknown>>({});
    const [isConnecting, setIsConnecting] = useState<Record<string, boolean>>({});
    const [isStartingRun, setIsStartingRun] = useState(false);
    const [searchQuery, setSearchQuery] = useState('');

    const client = useMemo(() => getLemmaClient(podId), [podId]);
    const hasInputSchema = hasSchemaProperties(agent?.input_schema);
    const hasOutputSchema = hasSchemaProperties(agent?.output_schema);
    const isConversationalAgent = Boolean(agent) && !hasInputSchema && !hasOutputSchema;
    const inputPropertyEntries = useMemo(() => {
        if (!hasInputSchema || !isRecord(agent?.input_schema) || !isRecord(agent.input_schema.properties)) return [];
        return Object.entries(agent.input_schema.properties);
    }, [agent?.input_schema, hasInputSchema]);
    const requiredKeys = useMemo(() => getRequiredKeys(agent?.input_schema), [agent?.input_schema]);
    const missingRequiredKeys = requiredKeys.filter((key) => {
        const value = formData[key];
        return value === null || typeof value === 'undefined' || value === '';
    });

    const controller = useAssistantController({
        client,
        podId,
        agentName: agent?.name || agentName,
        enabled: Boolean(agent),
    });
    const conversationMessages = useConversationMessages({
        client,
        podId,
        agentName: agent?.name || agentName,
        conversationId: controller.openedConversationId,
        enabled: Boolean(agent && controller.openedConversationId),
        autoLoad: true,
        autoResume: true,
        syncOnTurnEnd: true,
        limit: 100,
    });
    const refreshConversationMessages = conversationMessages.refresh;
    const openedConversationId = controller.openedConversationId;
    const openConversation = controller.openConversation;
    const settledConversationRef = useRef<string | null>(null);
    const handledOpenRequestRef = useRef<string | number | null>(null);
    const { data: rawMessagesData, refetch: refetchRawMessages } = useMessages(podId, openedConversationId || '', { limit: 100 });
    const rawMessages = useMemo(
        () => (rawMessagesData as { items?: Message[] } | undefined)?.items || [],
        [rawMessagesData],
    );
    const activeConversation = useMemo(
        () => controller.conversations.find((conversation) => conversation.id === openedConversationId) ?? null,
        [controller.conversations, openedConversationId],
    );
    const controllerView = useMemo<AssistantControllerView>(() => {
        const base = controller as unknown as AssistantControllerView;
        const rawById = new Map(rawMessages.map((message) => [message.id, message]));

        return {
            ...base,
            messages: base.messages.map((message) => {
                const raw = rawById.get(message.id);
                if (!raw) return message;

                return {
                    ...message,
                    metadata: (raw.metadata as Record<string, unknown> | null | undefined) ?? message.metadata,
                    message_metadata: (raw.message_metadata as Record<string, unknown> | null | undefined) ?? message.message_metadata,
                    tool_name: raw.tool_name ?? message.tool_name,
                    tool_call_id: raw.tool_call_id ?? message.tool_call_id,
                };
            }),
        };
    }, [controller, rawMessages]);
    const finalOutputMessages = useMemo(
        () => [...rawMessages, ...controllerView.messages],
        [controllerView.messages, rawMessages],
    );
    const conversationOutputText = conversationMessages.finalOutputText || conversationMessages.outputText;
    const assistantText = conversationOutputText || latestAssistantText(finalOutputMessages);
    const parsedOutput = taskConversationOutput(activeConversation);

    useEffect(() => {
        if (!agent || !openConversationId) return;

        const requestKey = openConversationRequestKey ?? openConversationId;
        if (handledOpenRequestRef.current === requestKey) return;
        handledOpenRequestRef.current = requestKey;

        if (openedConversationId !== openConversationId) {
            openConversation(openConversationId);
        }

        void refreshConversationMessages({ conversationId: openConversationId, limit: 100 });
    }, [
        openedConversationId,
        agent,
        openConversationId,
        openConversationRequestKey,
        refreshConversationMessages,
        openConversation,
    ]);

    useEffect(() => {
        const conversationId = controller.openedConversationId;
        if (!conversationId) {
            settledConversationRef.current = null;
            return;
        }

        if (controller.isOpenedConversationRunning) {
            settledConversationRef.current = null;
            return;
        }

        if (settledConversationRef.current === conversationId) return;
        settledConversationRef.current = conversationId;
        void Promise.allSettled([
            refetchRawMessages(),
            refreshConversationMessages({ conversationId, limit: 100 }),
        ]);
    }, [controller.isOpenedConversationRunning, controller.openedConversationId, refetchRawMessages, refreshConversationMessages]);

    useEffect(() => {
        const conversationId = controller.openedConversationId;
        const hasVisibleOutput = hasOutputSchema ? Boolean(parsedOutput) : Boolean(parsedOutput || assistantText);
        if (!conversationId || controller.isOpenedConversationRunning || hasVisibleOutput) return;

        let cancelled = false;
        let attempts = 0;
        let timeoutId: ReturnType<typeof setTimeout> | null = null;

        const refreshUntilOutputArrives = () => {
            timeoutId = setTimeout(() => {
                if (cancelled) return;
                attempts += 1;
                void Promise.allSettled([
                    refetchRawMessages(),
                    refreshConversationMessages({ conversationId, limit: 100 }),
                ]).finally(() => {
                    if (!cancelled && attempts < 6) refreshUntilOutputArrives();
                });
            }, attempts === 0 ? 450 : 900);
        };

        refreshUntilOutputArrives();

        return () => {
            cancelled = true;
            if (timeoutId) clearTimeout(timeoutId);
        };
    }, [
        assistantText,
        controller.isOpenedConversationRunning,
        controller.openedConversationId,
        hasOutputSchema,
        parsedOutput,
        refetchRawMessages,
        refreshConversationMessages,
    ]);

    const resolveConnector = useCallback((appName: string) =>
        allConnectors?.find(
            (connector) =>
                connector.id === appName
                || connector.name === appName
        ),
    [allConnectors]);

    const handleConnect = async (appId: string) => {
        try {
            setIsConnecting(prev => ({ ...prev, [appId]: true }));
            const response = await createConnectRequest.mutateAsync({ connectorId: appId });
            if (response.authorization_url) {
                window.open(response.authorization_url, '_blank');
            }
        } catch (error) {
            console.error('Failed to initiate connection:', error);
        } finally {
            setIsConnecting(prev => ({ ...prev, [appId]: false }));
        }
    };

    const handleRun = async () => {
        if (missingRequiredKeys.length > 0) return;

        setIsStartingRun(true);
        try {
            const payload = buildInputDataFromForm(formData, agent?.input_schema);
            await controller.sendMessage(formatAgentRunInput(payload), { forceNewConversation: true });
            const conversationId = controller.openedConversationId;
            if (conversationId) {
                void Promise.allSettled([
                    refetchRawMessages(),
                    refreshConversationMessages({ conversationId, limit: 100 }),
                ]);
            }
        } catch (error) {
            console.error('Failed to start agent run:', error);
        } finally {
            setIsStartingRun(false);
        }
    };

    const handleNewRun = () => {
        setFormData({});
        controller.clearMessages();
    };

    const openActiveConversationPage = useCallback(() => {
        if (controller.openedConversationId) {
            router.push(buildScopedConversationHref({
                podId,
                conversationId: controller.openedConversationId,
                agentName: agent?.name || agentName,
            }));
            return;
        }

        onToggleFullView?.();
    }, [agent?.name, agentName, controller.openedConversationId, onToggleFullView, podId, router]);

    const visibleApps = (agent?.accessible_connectors || []).filter(config => {
        const app = resolveConnector(config.app_name);
        const isEnabled = authConfigs.some((authConfig) => authConfig.connector_id === app?.id && authConfig.status === 'ACTIVE');
        const accounts = allAccounts?.filter(acc => acc.connector_id === app?.id) || [];
        const isConnected = accounts.length > 0;
        const isFixed = config.mode === ConnectorMode.FIXED;

        if (isFixed) return false;
        if (!isEnabled) return false;
        if (isConnected) return false;

        return true;
    });

    const normalizedSearch = searchQuery.trim().toLowerCase();
    const filteredConversations = controller.conversations.filter((conversation) => {
        const title = conversation.title || 'Untitled run';
        return !normalizedSearch
            || conversation.id.toLowerCase().includes(normalizedSearch)
            || title.toLowerCase().includes(normalizedSearch);
    });

    const hasActiveRun = Boolean(controller.openedConversationId);
    const historyScrollRef = useRef<HTMLDivElement | null>(null);
    const historySentinelRef = useInfiniteScroll({
        hasMore: controller.hasMoreConversations,
        isLoading: controller.isLoadingConversations || controller.isLoadingMoreConversations,
        onLoadMore: () => { void controller.loadMoreConversations(); },
        rootRef: historyScrollRef,
    });
    const historyPanel = (
        <aside className="agent-test-history-panel flex h-full min-h-0 w-full flex-col border-r border-[color:var(--row-border)] bg-[var(--row-bg)] lg:w-[340px] lg:shrink-0">
            <div className="agent-test-history-header shrink-0 bg-[var(--card-bg)] px-3 py-3">
                <div className="mb-3 flex items-center justify-between gap-3">
                    <div>
                        <h3 className="text-sm font-semibold text-[var(--text-primary)]">History</h3>
                        <p className="text-xs text-[var(--text-tertiary)]">{filteredConversations.length} run{filteredConversations.length === 1 ? '' : 's'}</p>
                    </div>
                    {hasActiveRun ? (
                        <Button
                            variant="ghost"
                            size="sm"
                            onClick={handleNewRun}
                            className="h-7 gap-1.5 px-2 text-xs text-[var(--text-tertiary)] hover:text-[var(--text-primary)]"
                            title="New run"
                        >
                            <Plus className="h-3.5 w-3.5" />
                            New
                        </Button>
                    ) : null}
                </div>
                <div className="relative">
                    <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--text-tertiary)]" />
                    <Input
                        placeholder="Search runs..."
                        value={searchQuery}
                        onChange={(event) => setSearchQuery(event.target.value)}
                        className="agent-test-search-field h-8 bg-[var(--bg-canvas)] pl-8 text-xs"
                    />
                </div>
            </div>

            <div ref={historyScrollRef} className="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-2">
                {filteredConversations.length === 0 ? (
                    <EmptyState
                        variant="compact"
                        icon={<MessageSquare className="h-4 w-4" />}
                        title="No runs yet"
                        description="Start the agent once and the run will appear here."
                        className="agent-test-history-empty h-full"
                    />
                ) : (
                  <>
                    {filteredConversations.map((conversation) => {
                    const isActive = controller.openedConversationId === conversation.id;
                    return (
                        <button
                            key={conversation.id}
                            type="button"
                            className={cn(
                                'agent-test-history-button w-full cursor-pointer rounded-lg border px-3 py-2.5 text-left transition-colors',
                                isActive
                                    ? 'tone-card-action bg-[var(--card-bg)] shadow-[var(--shadow-xs)]'
                                    : 'border-transparent bg-transparent hover:border-[color:var(--row-border)] hover:bg-[var(--card-bg)]'
                            )}
                            onClick={() => {
                                controller.openConversation(conversation.id);
                                void refreshConversationMessages({ conversationId: conversation.id, limit: 100 });
                            }}
                        >
                            <div className="flex items-start justify-between gap-3">
                                <div className="min-w-0">
                                    <p className="truncate text-sm font-medium text-[var(--text-primary)]">{conversation.title || 'Untitled run'}</p>
                                    <p className="mt-1 font-mono text-xs text-[var(--text-tertiary)]">#{conversation.id.slice(0, 8)}</p>
                                </div>
                                <span className="shrink-0 text-xs text-[var(--text-tertiary)]">
                                    {formatRelativeTime(conversation.updated_at || conversation.created_at)}
                                </span>
                            </div>
                        </button>
                    );
                    })}
                    <div ref={historySentinelRef} aria-hidden className="h-px" />
                    {controller.isLoadingMoreConversations && (
                        <div className="flex items-center justify-center py-3 text-[var(--text-tertiary)]">
                            <Loader2 className="h-4 w-4 animate-spin opacity-40" />
                        </div>
                    )}
                  </>
                )}
            </div>
        </aside>
    );

    if (isConversationalAgent) {
        return (
            <div className={cn('agent-test-panel grid h-full min-h-0 items-stretch bg-[var(--card-bg)]', isFullView ? 'grid-cols-1' : 'surface-split-2 lg:grid-cols-[340px_minmax(0,1fr)]')}>
                {!isFullView ? historyPanel : null}
                <div className="h-full min-h-0 min-w-0">
                    <AssistantExperienceView
                        controller={controllerView}
                        title={agent?.name || agentName}
                        subtitle="Conversation agent"
                        badge={null}
                        placeholder={`Message ${agent?.name || agentName}`}
                        appearance="minimal"
                        density="compact"
                        chromeStyle="flat"
                        radius="none"
                        statusPlacement="inline"
                        showConversationList={false}
                        showModelPicker={false}
                        showNewConversationButton
                        finalOutput={parsedOutput}
                        outputSchema={agent?.output_schema}
                        headerActions={(
                            <>
                                {onToggleFullView || controller.openedConversationId ? (
                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        onClick={openActiveConversationPage}
                                        className="h-8 w-8 rounded-md text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]"
                                        aria-label="Open full conversation"
                                        title="Open full conversation"
                                    >
                                        <Maximize2 className="h-4 w-4" />
                                    </Button>
                                ) : null}
                                {onClose ? (
                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        onClick={onClose}
                                        className="h-8 w-8 rounded-md text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]"
                                        aria-label="Close conversation"
                                        title="Close conversation"
                                    >
                                        <X className="h-4 w-4" />
                                    </Button>
                                ) : null}
                            </>
                        )}
                        emptyStateSuggestions={[
                            { text: 'Help me think through this pod' },
                            { text: 'Draft the next response' },
                            { text: 'Summarize what matters here' },
                        ]}
                        className="agent-test-assistant h-full min-h-0 rounded-none border-0 bg-[var(--card-bg)] shadow-none"
                    />
                </div>
            </div>
        );
    }

    return (
        <div className={cn('agent-test-panel grid h-full min-h-0 items-stretch bg-[var(--card-bg)]', isFullView ? 'grid-cols-1' : 'surface-split-2 lg:grid-cols-[340px_minmax(0,1fr)]')}>
            {!isFullView ? historyPanel : null}
            <div className="flex min-h-0 min-w-0 flex-col bg-[var(--card-bg)]">
                <div className="agent-test-run-header sticky top-0 z-10 flex h-14 shrink-0 items-center justify-between bg-[var(--card-bg)] px-3">
                    <div className="min-w-0">
                        <p className="type-eyebrow-medium">Run setup</p>
                        <h3 className="truncate text-sm font-medium text-[var(--text-primary)]">{agent?.name || agentName}</h3>
                    </div>

                    <div className="flex items-center gap-2">
                        {onToggleFullView || controller.openedConversationId ? (
                            <Button
                                variant="ghost"
                                size="icon"
                                onClick={openActiveConversationPage}
                                className="h-8 w-8 rounded-md text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]"
                                aria-label="Open full conversation"
                                title="Open full conversation"
                            >
                                <Maximize2 className="h-4 w-4" />
                            </Button>
                        ) : null}
                        {hasActiveRun ? (
                            <Button
                                variant="ghost"
                                size="sm"
                                onClick={handleNewRun}
                                className="h-7 gap-1.5 px-2 text-xs text-[var(--text-tertiary)] hover:text-[var(--text-primary)]"
                                title="New run"
                            >
                                <Plus className="h-3.5 w-3.5" />
                                New
                            </Button>
                        ) : null}
                        {onClose ? (
                            <>
                                <div className="mx-1 h-4 w-px bg-[var(--bg-muted)]" />
                                <Button variant="ghost" size="icon" onClick={onClose} className="h-8 w-8 rounded-md text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]">
                                    <X className="h-4 w-4" />
                                </Button>
                            </>
                        ) : null}
                    </div>
                </div>

                <div className="min-h-0 flex-1 overflow-hidden">
                    {hasActiveRun ? (
                    <div className="flex h-full min-h-0 flex-col bg-[var(--bg-canvas)]">
                        <div className="min-h-0 flex-1">
                            <AssistantExperienceView
                                controller={controllerView}
                                title="Follow-up"
                                subtitle="Continue from this result"
                                placeholder="Follow up on this run..."
                                appearance="minimal"
                                density="compact"
                                chromeStyle="flat"
                                radius="none"
                                statusPlacement="inline"
                                showConversationList={false}
                                showHeader={false}
                                showModelPicker={false}
                                showNewConversationButton={false}
                                finalOutput={parsedOutput}
                                outputSchema={agent?.output_schema}
                                className="h-full min-h-0 rounded-none border-0 bg-[var(--card-bg)] shadow-none"
                            />
                        </div>
                    </div>
                    ) : (
                    <div className="agent-test-run-start h-full overflow-y-auto">
                        <div className="agent-test-run-start-inner">
                            <div className="agent-test-start-panel">
                                <div className="agent-test-run-form-head">
                                    <div>
                                        <p className="type-eyebrow-medium">New run</p>
                                        <h4>Inputs</h4>
                                    </div>
                                </div>

                                {visibleApps.length > 0 ? (
                                    <div className="agent-test-run-connections space-y-3">
                                        <Label className="text-xs font-semibold text-[var(--text-tertiary)] uppercase tracking-wider">Connections Required</Label>
                                        {visibleApps.map(config => {
                                            const app = resolveConnector(config.app_name);
                                            return (
                                                <div key={config.app_name} className="state-surface-warning flex items-center justify-between rounded-md p-2 py-1">
                                                    <div className="flex items-center gap-2">
                                                        <AlertCircle className="w-3.5 h-3.5 text-[var(--state-warning)]" />
                                                        <span className="text-xs text-[var(--text-secondary)] font-medium">{app?.title || app?.name || 'Connector'}</span>
                                                    </div>
                                                    <Button
                                                        variant="ghost"
                                                        size="sm"
                                                        className="h-6 bg-[var(--button-secondary-bg)] text-xs hover:bg-[var(--button-secondary-bg-hover)]"
                                                        onClick={() => app?.id && handleConnect(app.id)}
                                                        disabled={isConnecting[app?.id || '']}
                                                    >
                                                        {isConnecting[app?.id || ''] ? <Loader2 className="w-3 h-3 animate-spin" /> : <LinkIcon className="w-3 h-3 mr-1" />}
                                                        Connect
                                                    </Button>
                                                </div>
                                            );
                                        })}
                                    </div>
                                ) : null}

                                {hasInputSchema ? (
                                    inputPropertyEntries.map(([key, field]) => {
                                        const property = isRecord(field) ? field : {};
                                        const type = getSchemaPropertyType(property);
                                        const enumOptions: unknown[] = Array.isArray(property.enum) ? property.enum : [];

                                        return (
                                            <div key={key} className="agent-test-run-field">
                                                <Label className="agent-test-run-label">
                                                    {formatFieldLabel(key)}
                                                    {requiredKeys.includes(key) ? <span className="text-[var(--state-error)] ml-0.5">*</span> : null}
                                                </Label>
                                                {typeof property.description === 'string' ? <p className="text-xs text-[var(--text-tertiary)]">{property.description}</p> : null}

                                                {enumOptions.length > 0 ? (
                                                    <Select
                                                        value={typeof formData[key] === 'undefined' ? undefined : String(formData[key])}
                                                        onValueChange={(value) => setFormData(prev => ({ ...prev, [key]: value }))}
                                                    >
                                                        <SelectTrigger className="agent-test-run-input h-9 text-sm">
                                                            <SelectValue placeholder={`Choose ${key}...`} />
                                                        </SelectTrigger>
                                                        <SelectContent>
                                                            {enumOptions.map((option) => (
                                                                <SelectItem key={String(option)} value={String(option)}>{String(option)}</SelectItem>
                                                            ))}
                                                        </SelectContent>
                                                    </Select>
                                                ) : type === 'boolean' ? (
                                                    <Select
                                                        value={typeof formData[key] === 'boolean' ? String(formData[key]) : undefined}
                                                        onValueChange={(value) => setFormData(prev => ({ ...prev, [key]: value === 'true' }))}
                                                    >
                                                        <SelectTrigger className="agent-test-run-input h-9 text-sm">
                                                            <SelectValue placeholder={`Choose ${key}...`} />
                                                        </SelectTrigger>
                                                        <SelectContent>
                                                            <SelectItem value="true">True</SelectItem>
                                                            <SelectItem value="false">False</SelectItem>
                                                        </SelectContent>
                                                    </Select>
                                                ) : type === 'number' || type === 'integer' ? (
                                                    <Input
                                                        type="number"
                                                        step={type === 'integer' ? 1 : 'any'}
                                                        value={String(formData[key] ?? '')}
                                                        onChange={(event) => setFormData(prev => ({ ...prev, [key]: event.target.value }))}
                                                        placeholder={`Enter ${key}...`}
                                                        className="agent-test-run-input h-9 text-sm"
                                                    />
                                                ) : type === 'array' || type === 'object' ? (
                                                    <textarea
                                                        value={String(formData[key] ?? '')}
                                                        onChange={(event) => setFormData(prev => ({ ...prev, [key]: event.target.value }))}
                                                        placeholder={type === 'array' ? '["example"]' : '{"key":"value"}'}
                                                        className="agent-test-field agent-test-run-input min-h-24 w-full resize-y rounded-md px-3 py-2 text-sm text-[var(--text-primary)] outline-none placeholder:text-[var(--text-tertiary)]"
                                                    />
                                                ) : (
                                                    <Input
                                                        value={String(formData[key] ?? '')}
                                                        onChange={(event) => setFormData(prev => ({ ...prev, [key]: event.target.value }))}
                                                        placeholder={`Enter ${key}...`}
                                                        className="agent-test-run-input h-9 text-sm"
                                                    />
                                                )}
                                            </div>
                                        );
                                    })
                                ) : (
                                    <div className="agent-test-run-no-input">
                                        This agent does not need input. Start the run and review the result here.
                                    </div>
                                )}

                                {missingRequiredKeys.length > 0 ? (
                                    <p className="agent-test-run-error text-xs">
                                        Missing: {missingRequiredKeys.map(formatFieldLabel).join(', ')}
                                    </p>
                                ) : null}

                                <div className="agent-test-run-action-row">
                                    <span className="agent-test-run-action-copy">
                                        {missingRequiredKeys.length > 0 ? 'Add the required inputs to launch.' : 'Ready when you are.'}
                                    </span>
                                    <Button
                                        onClick={handleRun}
                                        disabled={isStartingRun || controller.isOpenedConversationRunning || missingRequiredKeys.length > 0}
                                        className="agent-test-run-button"
                                    >
                                        {isStartingRun || controller.isOpenedConversationRunning ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : <Play className="w-4 h-4 mr-2" />}
                                        Start run
                                    </Button>
                                </div>
                            </div>
                        </div>
                    </div>
                    )}
                </div>
            </div>
        </div>
    );
}
