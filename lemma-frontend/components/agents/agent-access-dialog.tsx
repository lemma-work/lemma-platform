'use client';

import { useMemo, useState, type ReactNode } from 'react';
import Image from 'next/image';

import {
    Bot,
    Check,
    Code,
    FolderOpen,
    Globe2,
    Image as ImageIcon,
    ListTodo,
    MessageCircle,
    Plug,
    Search,
    Sparkles,
    SquareTerminal,
    Table as TableIcon,
    Timer,
    Volume2,
    Wrench,
    type LemmaIcon,
} from '@/components/ui/icons';
import { Button } from '@/components/ui/button';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from '@/components/ui/select';
import { FolderPickerLevel } from '@/components/pod/folder-picker-tree';
import { ROOT_DIRECTORY, ancestorFolderPaths, folderDisplayPath } from '@/lib/files/folder-picker';
import { useAgents } from '@/lib/hooks/use-agents';
import { useAccounts, useAuthConfigs, useConnectors } from '@/lib/hooks/use-connectors';
import { useTables } from '@/lib/hooks/use-datastores';
import { useFunctions } from '@/lib/hooks/use-functions';
import { usePod } from '@/lib/hooks/use-pods';
import { formatAgentName } from '@/lib/utils/agents';
import { ConnectorMode, AccessMode, ToolSet, type Agent, type ConnectorAccessConfig, type Table } from '@/lib/types';

/**
 * What one agent is allowed to touch.
 *
 * The keyring, not a settings page. Every category is a shelf on the left with
 * a live count; picking one shows *everything available* in that category as a
 * row you switch on, rather than an empty list plus a dropdown hiding the
 * options. So "what can this agent reach" and "what could it reach" are the
 * same screen, and the answer is never behind a menu.
 *
 * Choices land on the draft agent through `onUpdate`; the page that owns the
 * agent is what saves them.
 */

type AccessCategoryId = 'tools' | 'connectors' | 'tables' | 'folders' | 'functions' | 'agents';

type AccessCategory = {
    id: AccessCategoryId;
    /** Rail label — the one noun this concept goes by everywhere in the product. */
    label: string;
    icon: LemmaIcon;
    /** What granting this category actually lets the agent do. */
    blurb: string;
};

const CATEGORIES: AccessCategory[] = [
    { id: 'tools', label: 'Tools', icon: Wrench, blurb: 'Built-in abilities every conversation can draw on.' },
    { id: 'connectors', label: 'Connectors', icon: Plug, blurb: 'Outside apps this agent can act in, and whose account it uses.' },
    { id: 'tables', label: 'Tables', icon: TableIcon, blurb: 'Pod data it can read, and what it may change.' },
    { id: 'folders', label: 'Folders', icon: FolderOpen, blurb: 'Documents it can search, read, and cite.' },
    { id: 'functions', label: 'Functions', icon: Code, blurb: 'Deterministic code it can run instead of guessing.' },
    { id: 'agents', label: 'Agents', icon: Bot, blurb: 'Teammates it can hand work to and wait on.' },
];

/**
 * Toolsets, said in terms of what the agent gains. The enum name is an
 * implementation detail — `WORKSPACE_CLI` told nobody anything.
 */
const TOOL_COPY: Record<string, { label: string; description: string; icon: LemmaIcon }> = {
    WORKSPACE_CLI: {
        label: 'Workspace',
        description: 'Run shell commands and Python in its own sandbox.',
        icon: SquareTerminal,
    },
    POD: {
        label: 'Pod data',
        description: 'Read and write this pod’s tables and files directly.',
        icon: TableIcon,
    },
    WEB_SEARCH: {
        label: 'Web search',
        description: 'Look things up on the open web.',
        icon: Globe2,
    },
    SKILLS: {
        label: 'Skills',
        description: 'Load procedures from the pod’s skill library.',
        icon: Sparkles,
    },
    USER_INTERACTION: {
        label: 'Ask a person',
        description: 'Ask questions, request approval, and show results back.',
        icon: MessageCircle,
    },
    SUBAGENTS: {
        label: 'Delegation',
        description: 'Spawn other agents and collect what they find.',
        icon: Bot,
    },
    TODO: {
        label: 'Task list',
        description: 'Keep a checklist across long pieces of work.',
        icon: ListTodo,
    },
    SPEECH: {
        label: 'Speech',
        description: 'Listen and speak on voice surfaces.',
        icon: Volume2,
    },
    SNOOZE: {
        label: 'Sleep and resume',
        description: 'Pause mid-task for a while, then pick up where it left off.',
        icon: Timer,
    },
    VIEW_IMAGE: {
        label: 'Vision',
        description: 'Look at images and screenshots in its workspace.',
        icon: ImageIcon,
    },
    CONNECTORS: {
        // This is the ability, not the list. Which apps it may reach is the
        // Connectors category, and both are required to act in one.
        label: 'Connected apps',
        description: 'Call connected apps directly, without a sandbox. Pick which ones under Connectors.',
        icon: Plug,
    },
};

/** Ordered so the abilities most agents want are decided first. */
const TOOL_ORDER: string[] = [
    'WORKSPACE_CLI',
    'POD',
    'WEB_SEARCH',
    'SKILLS',
    'USER_INTERACTION',
    'SUBAGENTS',
    'TODO',
    'SPEECH',
    'SNOOZE',
    'VIEW_IMAGE',
    'CONNECTORS',
];

const EACH_PERSON_ACCOUNT = '__each_person__';

function toolCopy(tool: string) {
    return TOOL_COPY[tool] ?? {
        label: tool.toLowerCase().replace(/[_-]+/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase()),
        description: '',
        icon: Wrench,
    };
}

/** What a toolset is called wherever it is shown — summary chips included. */
export function toolSetLabel(tool: string) {
    return toolCopy(tool).label;
}

function matches(query: string, ...fields: (string | null | undefined)[]) {
    const needle = query.trim().toLowerCase();
    if (!needle) return true;
    return fields.some((field) => (field || '').toLowerCase().includes(needle));
}

/**
 * One switchable thing. The whole row is the switch; anything that configures
 * the grant (which account, read or write) sits beside it, outside the button —
 * a control nested inside a button is neither clickable nor announced.
 */
function AccessRow({
    selected,
    missing = false,
    icon,
    title,
    description,
    onToggle,
    aside,
}: {
    selected: boolean;
    /** The grant survives, the thing it names does not. */
    missing?: boolean;
    icon: ReactNode;
    title: ReactNode;
    description?: ReactNode;
    onToggle: () => void;
    aside?: ReactNode;
}) {
    return (
        <div className="agent-access-row" data-selected={selected} data-missing={missing}>
            <button
                type="button"
                className="agent-access-row-main"
                aria-pressed={selected}
                onClick={onToggle}
            >
                <span className="agent-access-check" data-checked={selected}>
                    {selected ? <Check className="h-3 w-3" weight="bold" /> : null}
                </span>
                <span className="agent-access-row-icon">{icon}</span>
                <span className="agent-access-row-text">
                    <span className="agent-access-row-title">{title}</span>
                    {description ? <span className="agent-access-row-description">{description}</span> : null}
                </span>
            </button>
            {aside ? <div className="agent-access-row-aside">{aside}</div> : null}
        </div>
    );
}

function PaneEmpty({ children }: { children: ReactNode }) {
    return <p className="agent-access-pane-empty">{children}</p>;
}

/**
 * A grant naming something the pod no longer has.
 *
 * These would otherwise be invisible — counted on the rail, absent from the
 * list, impossible to revoke. Shown first, dimmed, and switchable off.
 */
function OrphanRows({
    names,
    note,
    icon,
    onRemove,
}: {
    names: string[];
    note: string;
    icon: ReactNode;
    onRemove: (name: string) => void;
}) {
    if (names.length === 0) return null;

    return (
        <>
            {names.map((name) => (
                <AccessRow
                    key={`orphan-${name}`}
                    selected
                    missing
                    icon={icon}
                    title={name}
                    description={note}
                    onToggle={() => onRemove(name)}
                />
            ))}
        </>
    );
}

export function AgentAccessDialog({
    open,
    onOpenChange,
    agent,
    onUpdate,
}: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    agent: Agent;
    onUpdate: (data: Partial<Agent>) => void;
}) {
    const podId = agent.pod_id;
    const [category, setCategory] = useState<AccessCategoryId>('tools');
    const [query, setQuery] = useState('');
    const [expandedFolders, setExpandedFolders] = useState<Set<string>>(
        () => new Set((agent.accessible_folders || []).flatMap((entry) => ancestorFolderPaths(entry.folder_path))),
    );

    const { data: pod } = usePod(podId);
    const organizationId = pod?.organization_id;
    const { data: connectors = [] } = useConnectors({ limit: 100, enabled: open });
    const { data: authConfigs = [] } = useAuthConfigs({ organizationId, limit: 100, enabled: open });
    const { data: accounts = [] } = useAccounts({ organizationId, limit: 100, enabled: open });
    const { data: tablesData } = useTables(podId, undefined, { enabled: open });
    const { data: functionsData } = useFunctions(open ? podId : undefined);
    const { data: agentsData } = useAgents(open ? podId : undefined);

    const selectedTools = agent.tool_sets || [];
    const selectedConnectors = agent.accessible_connectors || [];
    const selectedTables = agent.accessible_tables || [];
    const selectedFolders = agent.accessible_folders || [];
    const selectedFolderPaths = selectedFolders.map((entry) => entry.folder_path);
    const selectedFunctions = agent.function_names || [];
    const selectedAgents = agent.agent_names || [];

    const counts: Record<AccessCategoryId, number> = {
        tools: selectedTools.length,
        connectors: selectedConnectors.length,
        tables: selectedTables.length,
        folders: selectedFolders.length,
        functions: selectedFunctions.length,
        agents: selectedAgents.length,
    };
    const grantedTotal = Object.values(counts).reduce((sum, count) => sum + count, 0);

    // A connector only counts as available once its auth config is live —
    // offering one that cannot authenticate is offering a dead end.
    const activeConnectorIds = new Set(
        authConfigs.filter((config) => config.status === 'ACTIVE').map((config) => config.connector_id),
    );
    const availableConnectors = useMemo(
        () => connectors
            .filter((connector) => activeConnectorIds.has(connector.id))
            .sort((left, right) => (left.title || left.id).localeCompare(right.title || right.id)),
        // eslint-disable-next-line react-hooks/exhaustive-deps -- Set identity churns every render; the ids it holds are what matter.
        [connectors, authConfigs],
    );
    const tables: Table[] = useMemo(
        () => [...(tablesData?.items || [])].sort((left, right) => left.name.localeCompare(right.name)),
        [tablesData],
    );
    const functions = useMemo(
        () => [...(functionsData?.items || [])].sort((left, right) => left.name.localeCompare(right.name)),
        [functionsData],
    );
    // An agent is never its own sub-agent.
    const otherAgents = useMemo(
        () => (agentsData?.items || [])
            .filter((candidate) => candidate.name !== agent.name)
            .sort((left, right) => left.name.localeCompare(right.name)),
        [agentsData, agent.name],
    );

    // Grants whose subject is gone (or, for connectors, no longer connected).
    // Held back until the catalog has actually loaded, so a pending request is
    // never mistaken for a deleted table.
    const orphanConnectors = connectors.length > 0
        ? selectedConnectors
            .map((entry) => entry.app_name)
            .filter((name) => !availableConnectors.some((connector) => connector.id === name))
        : [];
    const orphanTables = tablesData
        ? selectedTables
            .map((entry) => entry.table_name)
            .filter((name) => !tables.some((table) => table.name === name))
        : [];
    const orphanFunctions = functionsData
        ? selectedFunctions.filter((name) => !functions.some((fn) => fn.name === name))
        : [];
    const orphanAgents = agentsData
        ? selectedAgents.filter((name) => !otherAgents.some((candidate) => candidate.name === name))
        : [];

    const activeCategory = CATEGORIES.find((entry) => entry.id === category) ?? CATEGORIES[0];

    const toggleTool = (tool: ToolSet) => {
        const next = selectedTools.includes(tool)
            ? selectedTools.filter((entry) => entry !== tool)
            : [...selectedTools, tool];
        onUpdate({ tool_sets: next });
    };

    const toggleConnector = (connectorId: string) => {
        const next = selectedConnectors.some((entry) => entry.app_name === connectorId)
            ? selectedConnectors.filter((entry) => entry.app_name !== connectorId)
            : [...selectedConnectors, { app_name: connectorId, mode: ConnectorMode.DYNAMIC } as ConnectorAccessConfig];
        onUpdate({ accessible_connectors: next });
    };

    /**
     * Mode and account are one decision, so they are one control: "each
     * person's own" or a specific shared account. Picking an account is what
     * makes the grant fixed — there is no separate mode to get wrong.
     */
    const setConnectorAccount = (connectorId: string, accountId: string) => {
        onUpdate({
            accessible_connectors: selectedConnectors.map((entry) => (
                entry.app_name === connectorId
                    ? accountId === EACH_PERSON_ACCOUNT
                        ? { ...entry, mode: ConnectorMode.DYNAMIC, account_id: undefined }
                        : { ...entry, mode: ConnectorMode.FIXED, account_id: accountId }
                    : entry
            )),
        });
    };

    const toggleTable = (name: string) => {
        const next = selectedTables.some((entry) => entry.table_name === name)
            ? selectedTables.filter((entry) => entry.table_name !== name)
            : [...selectedTables, { table_name: name, mode: AccessMode.WRITE }];
        onUpdate({ accessible_tables: next });
    };

    const setTableMode = (name: string, mode: AccessMode) => {
        onUpdate({
            accessible_tables: selectedTables.map((entry) => (
                entry.table_name === name ? { ...entry, mode } : entry
            )),
        });
    };

    const toggleFolder = (path: string) => {
        const next = selectedFolderPaths.includes(path)
            ? selectedFolders.filter((entry) => entry.folder_path !== path)
            // Read by default: a folder is linked to be searched and cited far
            // more often than to be written into.
            : [...selectedFolders, { folder_path: path, mode: AccessMode.READ }];
        onUpdate({ accessible_folders: next });
    };

    const setFolderMode = (path: string, mode: AccessMode) => {
        onUpdate({
            accessible_folders: selectedFolders.map((entry) => (
                entry.folder_path === path ? { ...entry, mode } : entry
            )),
        });
    };

    const toggleExpandedFolder = (path: string) => {
        setExpandedFolders((previous) => {
            const next = new Set(previous);
            if (!next.delete(path)) next.add(path);
            return next;
        });
    };

    const toggleFunction = (name: string) => {
        const next = selectedFunctions.includes(name)
            ? selectedFunctions.filter((entry) => entry !== name)
            : [...selectedFunctions, name];
        onUpdate({ function_names: next } as Partial<Agent>);
    };

    const toggleAgent = (name: string) => {
        const next = selectedAgents.includes(name)
            ? selectedAgents.filter((entry) => entry !== name)
            : [...selectedAgents, name];
        onUpdate({ agent_names: next } as Partial<Agent>);
    };

    const handleCategoryChange = (next: AccessCategoryId) => {
        setCategory(next);
        setQuery('');
    };

    // The folder tree browses rather than lists, so it brings its own
    // navigation; a search box above it would filter nothing.
    const isSearchable = category !== 'folders';

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            {/* Box sizing rides on utilities so it beats DialogContent's own
                `max-w-lg`; everything inside is styled in agent-access.css. */}
            <DialogContent className="agent-access-dialog w-[min(52rem,calc(100vw-2rem))] max-w-[min(52rem,calc(100vw-2rem))] h-[min(38rem,calc(100dvh-2rem))] max-h-[min(38rem,calc(100dvh-2rem))] gap-0 p-0">
                <DialogHeader className="agent-access-dialog-header text-left">
                    <DialogTitle>Access</DialogTitle>
                    <DialogDescription>
                        Everything {agent.name ? <strong className="agent-access-subject">{agent.name}</strong> : 'this agent'} can
                        reach. Anything left off does not exist as far as it knows.
                    </DialogDescription>
                </DialogHeader>

                <div className="agent-access-layout">
                    <nav className="agent-access-rail" aria-label="Access categories">
                        {CATEGORIES.map((entry) => {
                            const Icon = entry.icon;
                            const count = counts[entry.id];

                            return (
                                <button
                                    key={entry.id}
                                    type="button"
                                    className="agent-access-rail-item"
                                    data-active={entry.id === category}
                                    aria-current={entry.id === category ? 'true' : undefined}
                                    onClick={() => handleCategoryChange(entry.id)}
                                >
                                    <Icon className="h-4 w-4 shrink-0" />
                                    <span className="agent-access-rail-label">{entry.label}</span>
                                    <span className="agent-access-rail-count" data-empty={count === 0}>
                                        {count}
                                    </span>
                                </button>
                            );
                        })}
                    </nav>

                    <section className="agent-access-pane" aria-label={activeCategory.label}>
                        <header className="agent-access-pane-header">
                            <p className="agent-access-pane-blurb">{activeCategory.blurb}</p>
                            {isSearchable ? (
                                <div className="agent-access-search">
                                    <Search className="agent-access-search-icon h-4 w-4" />
                                    <Input
                                        type="search"
                                        value={query}
                                        onChange={(event) => setQuery(event.target.value)}
                                        placeholder={`Search ${activeCategory.label.toLowerCase()}`}
                                        aria-label={`Search ${activeCategory.label.toLowerCase()}`}
                                        className="h-8 pl-8 pr-2.5 text-xs"
                                    />
                                </div>
                            ) : null}
                        </header>

                        <div className="agent-access-pane-body">
                            {category === 'tools' ? (
                                <div className="agent-access-list">
                                    {TOOL_ORDER
                                        .filter((tool) => Object.values(ToolSet).includes(tool as ToolSet))
                                        .concat(
                                            Object.values(ToolSet).filter((tool) => !TOOL_ORDER.includes(tool)),
                                        )
                                        .filter((tool) => matches(query, toolCopy(tool).label, toolCopy(tool).description))
                                        .map((tool) => {
                                            const copy = toolCopy(tool);
                                            const Icon = copy.icon;

                                            return (
                                                <AccessRow
                                                    key={tool}
                                                    selected={selectedTools.includes(tool as ToolSet)}
                                                    icon={<Icon className="h-4 w-4" />}
                                                    title={copy.label}
                                                    description={copy.description}
                                                    onToggle={() => toggleTool(tool as ToolSet)}
                                                />
                                            );
                                        })}
                                </div>
                            ) : null}

                            {category === 'connectors' ? (
                                availableConnectors.length === 0 && orphanConnectors.length === 0 ? (
                                    <PaneEmpty>
                                        No connectors are set up in this organization yet. Connect one and it shows up here.
                                    </PaneEmpty>
                                ) : (
                                    <div className="agent-access-list">
                                        <OrphanRows
                                            names={orphanConnectors}
                                            note="Not connected in this organization."
                                            icon={<Plug className="h-4 w-4" />}
                                            onRemove={toggleConnector}
                                        />
                                        {availableConnectors
                                            .filter((connector) => matches(query, connector.title, connector.name, connector.id))
                                            .map((connector) => {
                                                const config = selectedConnectors.find((entry) => entry.app_name === connector.id);
                                                const connectorAccounts = accounts.filter(
                                                    (account) => account.connector_id === connector.id,
                                                );
                                                const accountValue = config?.mode === ConnectorMode.FIXED && config.account_id
                                                    ? config.account_id
                                                    : EACH_PERSON_ACCOUNT;

                                                return (
                                                    <AccessRow
                                                        key={connector.id}
                                                        selected={Boolean(config)}
                                                        icon={connector.icon ? (
                                                            <Image
                                                                src={connector.icon}
                                                                alt=""
                                                                width={16}
                                                                height={16}
                                                                unoptimized
                                                                className="h-4 w-4 object-contain"
                                                            />
                                                        ) : <Plug className="h-4 w-4" />}
                                                        title={connector.title || connector.name || connector.id}
                                                        description={connector.description}
                                                        onToggle={() => toggleConnector(connector.id)}
                                                        aside={config ? (
                                                            <Select
                                                                value={accountValue}
                                                                onValueChange={(next) => setConnectorAccount(connector.id, next)}
                                                            >
                                                                <SelectTrigger className="agent-access-select h-8 text-xs">
                                                                    <SelectValue />
                                                                </SelectTrigger>
                                                                <SelectContent align="end">
                                                                    <SelectItem value={EACH_PERSON_ACCOUNT}>
                                                                        Each person’s own
                                                                    </SelectItem>
                                                                    {connectorAccounts.length > 0 ? (
                                                                        <SelectGroup>
                                                                            <SelectLabel>One shared account</SelectLabel>
                                                                            {connectorAccounts.map((account) => (
                                                                                <SelectItem key={account.id} value={account.id}>
                                                                                    {account.display_name || account.email || account.id.slice(0, 8)}
                                                                                </SelectItem>
                                                                            ))}
                                                                        </SelectGroup>
                                                                    ) : null}
                                                                </SelectContent>
                                                            </Select>
                                                        ) : null}
                                                    />
                                                );
                                            })}
                                    </div>
                                )
                            ) : null}

                            {category === 'tables' ? (
                                tables.length === 0 && orphanTables.length === 0 ? (
                                    <PaneEmpty>This pod has no tables yet.</PaneEmpty>
                                ) : (
                                    <div className="agent-access-list">
                                        <OrphanRows
                                            names={orphanTables}
                                            note="No longer in this pod."
                                            icon={<TableIcon className="h-4 w-4" />}
                                            onRemove={toggleTable}
                                        />
                                        {tables
                                            .filter((table) => matches(query, table.name))
                                            .map((table) => {
                                                const entry = selectedTables.find((candidate) => candidate.table_name === table.name);

                                                return (
                                                    <AccessRow
                                                        key={table.name}
                                                        selected={Boolean(entry)}
                                                        icon={<TableIcon className="h-4 w-4" />}
                                                        title={table.name}
                                                        // Row-level security changes what the agent
                                                        // actually sees inside a table it can read, so
                                                        // it belongs next to the grant.
                                                        description={table.enable_rls ? 'Row-level security on' : undefined}
                                                        onToggle={() => toggleTable(table.name)}
                                                        aside={entry ? (
                                                            <div className="segmented-control">
                                                                <button
                                                                    type="button"
                                                                    className="segmented-control-item"
                                                                    data-active={entry.mode === AccessMode.READ}
                                                                    onClick={() => setTableMode(table.name, AccessMode.READ)}
                                                                >
                                                                    Read
                                                                </button>
                                                                <button
                                                                    type="button"
                                                                    className="segmented-control-item"
                                                                    data-active={entry.mode === AccessMode.WRITE}
                                                                    onClick={() => setTableMode(table.name, AccessMode.WRITE)}
                                                                >
                                                                    Write
                                                                </button>
                                                            </div>
                                                        ) : null}
                                                    />
                                                );
                                            })}
                                    </div>
                                )
                            ) : null}

                            {category === 'folders' ? (
                                <div className="agent-access-tree">
                                    {/* The tree browses; it is where a folder gets
                                        picked. Read or write is decided below, on the
                                        granted list, so a deleted folder still has a
                                        row you can revoke. */}
                                    <div className="agent-access-tree-picker">
                                        <FolderPickerLevel
                                            podId={podId}
                                            directoryPath={ROOT_DIRECTORY}
                                            depth={0}
                                            selected={selectedFolderPaths}
                                            expandedPaths={expandedFolders}
                                            onToggleFolder={toggleFolder}
                                            onToggleExpanded={toggleExpandedFolder}
                                        />
                                    </div>

                                    {selectedFolders.length > 0 ? (
                                        <div className="agent-access-granted">
                                            <p className="agent-access-granted-label">Granted</p>
                                            <div className="agent-access-list">
                                                {selectedFolders.map((entry) => (
                                                    <AccessRow
                                                        key={entry.folder_path}
                                                        selected
                                                        icon={<FolderOpen className="h-4 w-4" />}
                                                        title={folderDisplayPath(entry.folder_path)}
                                                        onToggle={() => toggleFolder(entry.folder_path)}
                                                        aside={(
                                                            <div className="segmented-control">
                                                                <button
                                                                    type="button"
                                                                    className="segmented-control-item"
                                                                    data-active={entry.mode === AccessMode.READ}
                                                                    onClick={() => setFolderMode(entry.folder_path, AccessMode.READ)}
                                                                >
                                                                    Read
                                                                </button>
                                                                <button
                                                                    type="button"
                                                                    className="segmented-control-item"
                                                                    data-active={entry.mode === AccessMode.WRITE}
                                                                    onClick={() => setFolderMode(entry.folder_path, AccessMode.WRITE)}
                                                                >
                                                                    Write
                                                                </button>
                                                            </div>
                                                        )}
                                                    />
                                                ))}
                                            </div>
                                        </div>
                                    ) : null}
                                </div>
                            ) : null}

                            {category === 'functions' ? (
                                functions.length === 0 && orphanFunctions.length === 0 ? (
                                    <PaneEmpty>This pod has no functions yet.</PaneEmpty>
                                ) : (
                                    <div className="agent-access-list">
                                        <OrphanRows
                                            names={orphanFunctions}
                                            note="No longer in this pod."
                                            icon={<Code className="h-4 w-4" />}
                                            onRemove={toggleFunction}
                                        />
                                        {functions
                                            .filter((fn) => matches(query, fn.name, fn.description))
                                            .map((fn) => (
                                                <AccessRow
                                                    key={fn.name}
                                                    selected={selectedFunctions.includes(fn.name)}
                                                    icon={<Code className="h-4 w-4" />}
                                                    title={fn.name}
                                                    description={fn.description}
                                                    onToggle={() => toggleFunction(fn.name)}
                                                />
                                            ))}
                                    </div>
                                )
                            ) : null}

                            {category === 'agents' ? (
                                otherAgents.length === 0 && orphanAgents.length === 0 ? (
                                    <PaneEmpty>No other agents in this pod yet.</PaneEmpty>
                                ) : (
                                    <div className="agent-access-list">
                                        <OrphanRows
                                            names={orphanAgents}
                                            note="No longer in this pod."
                                            icon={<Bot className="h-4 w-4" />}
                                            onRemove={toggleAgent}
                                        />
                                        {otherAgents
                                            .filter((candidate) => matches(query, candidate.name, candidate.description))
                                            .map((candidate) => (
                                                <AccessRow
                                                    key={candidate.name}
                                                    selected={selectedAgents.includes(candidate.name)}
                                                    icon={<Bot className="h-4 w-4" />}
                                                    title={formatAgentName(candidate.name)}
                                                    description={candidate.description}
                                                    onToggle={() => toggleAgent(candidate.name)}
                                                />
                                            ))}
                                    </div>
                                )
                            ) : null}
                        </div>
                    </section>
                </div>

                <DialogFooter className="agent-access-dialog-footer flex-row items-center">
                    <p className="agent-access-total">
                        {grantedTotal === 0
                            ? 'Nothing granted yet.'
                            : `${grantedTotal} ${grantedTotal === 1 ? 'grant' : 'grants'} on the keyring.`}
                    </p>
                    <Button variant="quiet" type="button" size="sm" onClick={() => onOpenChange(false)}>
                        Done
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
