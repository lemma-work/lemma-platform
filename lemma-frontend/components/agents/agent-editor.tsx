'use client';

import { ReactNode, useEffect, useState, useSyncExternalStore } from 'react';
import { Agent, ConnectorMode, TableAccessMode, ToolSet } from '@/lib/types';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
    Bot,
    Box,
    Code,
    Code2,
    Database,
    FileText,
    Folder,
    Globe2,
    Plus,
    Table as TableIcon,
    Wrench,
    X,
} from 'lucide-react';
import Editor from '@monaco-editor/react';
import { SchemaBuilder } from '@/components/agents/schema-builder';
import { MarkdownEditor } from '@/components/documents/markdown-editor';
import { TourLayer } from '@/components/education/coachmark';
import { ConceptHint } from '@/components/education/concept-hint';
import { AgentsSelector, ConnectorsSelector, DatastoresSelector, FoldersSelector, FunctionsSelector, PropertyRow, formatAccessLabel } from '@/components/pod/resource-selectors';
import { useTheme } from 'next-themes';
import { AgentAvatarPicker } from '@/components/agents/agent-avatar-picker';
import { resolveDefaultAgentRuntime } from '@/components/agents/agent-runtime-helpers';
import { RuntimeModelPicker } from '@/components/lemma/assistant/model-picker';
import { useAgentRuntimes, useAvailableAgentRuntimeHarnesses } from '@/lib/hooks/use-agent-runtime';
import { usePod } from '@/lib/hooks/use-pods';
import { ResourceVisibilitySelect, type ResourceVisibilityValue } from '@/components/shared/resource-visibility';

interface AgentEditorProps {
    podId?: string;
    agent: Agent;
    onUpdate: (data: Partial<Agent>) => void;
    isNameEditable?: boolean;
    shareUrl?: string;
    onShareVisibilityChange?: (visibility: ResourceVisibilityValue) => void | Promise<void>;
}

function getSchemaPropertyNames(schema: unknown): string[] {
    if (!schema || typeof schema !== 'object' || Array.isArray(schema)) return [];
    const properties = (schema as { properties?: unknown }).properties;
    if (!properties || typeof properties !== 'object' || Array.isArray(properties)) return [];
    return Object.keys(properties);
}

function removeSchemaProperty(schema: unknown, key: string): Record<string, unknown> {
    const source = schema && typeof schema === 'object' && !Array.isArray(schema)
        ? { ...(schema as Record<string, unknown>) }
        : {};
    const properties = source.properties && typeof source.properties === 'object' && !Array.isArray(source.properties)
        ? { ...(source.properties as Record<string, unknown>) }
        : {};
    delete properties[key];

    const required = Array.isArray(source.required)
        ? source.required.filter((item) => item !== key)
        : source.required;

    return {
        ...source,
        type: source.type || 'object',
        properties,
        ...(Array.isArray(required) ? { required } : {}),
    };
}

function takeWithOverflow<T>(items: T[], limit = 6): { visible: T[]; overflow: number } {
    return {
        visible: items.slice(0, limit),
        overflow: Math.max(0, items.length - limit),
    };
}

function SummaryChip({
    icon,
    label,
    onRemove,
}: {
    icon?: ReactNode;
    label: string;
    onRemove?: () => void;
}) {
    return (
        <span className="chip chip-sm max-w-full gap-1.5">
            {icon ? <span className="shrink-0 text-[var(--text-tertiary)]">{icon}</span> : null}
            <span className="truncate">{label}</span>
            {onRemove ? (
                <button
                    type="button"
                    onClick={onRemove}
                    className="resource-remove-button -mr-1 h-5 w-5 rounded-full"
                    aria-label={`Remove ${label}`}
                    title={`Remove ${label}`}
                >
                    <X className="h-3.5 w-3.5" />
                </button>
            ) : null}
        </span>
    );
}

function EmptyInline({ children }: { children: ReactNode }) {
    return <span className="text-sm text-[var(--text-tertiary)]">{children}</span>;
};

const accessSectionClassName = "surface-panel gap-3 p-4 md:!grid-cols-1";

export function AgentEditor({
    podId,
    agent,
    onUpdate,
    isNameEditable = false,
    shareUrl,
    onShareVisibilityChange,
}: AgentEditorProps) {
    const [title, setTitle] = useState(agent.name);
    const [description, setDescription] = useState(agent.description || '');
    const [instruction, setInstruction] = useState((agent.instruction || '').replace(/\s+$/g, ''));
    const [schemaMode, setSchemaMode] = useState<'builder' | 'json'>('builder');
    const [schemaTab, setSchemaTab] = useState<'input' | 'output'>('input');
    const [isPictureDialogOpen, setIsPictureDialogOpen] = useState(false);
    const [isAccessDialogOpen, setIsAccessDialogOpen] = useState(false);
    const [isContractDialogOpen, setIsContractDialogOpen] = useState(false);
    const { resolvedTheme } = useTheme();
    const { data: pod } = usePod(podId);
    const { data: runtimeCatalog } = useAgentRuntimes(pod?.organization_id);
    const { data: availableHarnesses } = useAvailableAgentRuntimeHarnesses();
    const defaultRuntime = resolveDefaultAgentRuntime(runtimeCatalog, pod?.config?.default_profile_id, availableHarnesses);
    const mounted = useSyncExternalStore(
        () => () => { },
        () => true,
        () => false
    );

    useEffect(() => {
        if (agent.name !== title) setTitle(agent.name);
        if ((agent.description || '') !== description) setDescription(agent.description || '');
        const trimmedInstruction = (agent.instruction || '').replace(/\s+$/g, '');
        if (trimmedInstruction !== instruction) setInstruction(trimmedInstruction);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [agent]);

    const normalizeInstruction = (value: string) =>
        value.replace(/[ \t]+$/gm, '').replace(/\n{3,}$/g, '\n\n').replace(/\s+$/g, '');

    useEffect(() => {
        const normalized = normalizeInstruction(instruction);
        const current = normalizeInstruction(agent.instruction || '');
        if (normalized === current) return;

        const timer = setTimeout(() => {
            onUpdate({ instruction: normalized });
        }, 450);

        return () => clearTimeout(timer);
    }, [agent.instruction, instruction, onUpdate]);

    const handleBlur = (field: keyof Agent, value: string) => {
        if (value !== agent[field]) {
            onUpdate({ [field]: value });
        }
    };

    const handleSchemaChange = (type: 'input_schema' | 'output_schema', newSchema: Record<string, unknown>) => {
        onUpdate({ [type]: newSchema });
    };

    const monacoTheme = mounted && resolvedTheme === 'dark' ? 'vs-dark' : 'vs-light';
    const linkedResourcesCount =
        (agent.tool_sets?.length || 0) +
        (agent.accessible_connectors?.length || 0) +
        (agent.accessible_tables?.length || 0) +
        (agent.accessible_folders?.length || 0) +
        (agent.function_names?.length || 0) +
        (agent.agent_names?.length || 0);
    const inputVariables = getSchemaPropertyNames(agent.input_schema);
    const outputVariables = getSchemaPropertyNames(agent.output_schema);
    const toolChips = takeWithOverflow(agent.tool_sets || []);
    const appChips = takeWithOverflow((agent.accessible_connectors || []).map((entry) => {
        const account = entry.account_id ? ` · ${entry.account_id.slice(0, 6)}` : '';
        const mode = entry.mode === ConnectorMode.DYNAMIC ? 'choose account' : 'fixed';
        return `${entry.app_name}${account ? account : ` · ${mode}`}`;
    }));
    const tableChips = takeWithOverflow((agent.accessible_tables || []).map((entry) => {
        const mode = entry.mode === TableAccessMode.READ ? 'Read' : 'Write';
        return `${entry.table_name} · ${mode}`;
    }));
    const folderChips = takeWithOverflow(agent.accessible_folders || []);
    const functionChips = takeWithOverflow(agent.function_names || []);
    const agentToolChips = takeWithOverflow(agent.agent_names || []);

    return (
        <div className="agent-detail-editor h-full min-h-0 overflow-hidden bg-[var(--bg-canvas)]">
            <TourLayer tour="agent-editor" />
            <div className="surface-split-2 surface-split-2-rows grid h-full min-h-0 grid-cols-1 grid-rows-[minmax(0,0.45fr)_minmax(0,0.55fr)] lg:grid-cols-[390px_minmax(0,1fr)] lg:grid-rows-1">
                <aside className="agent-detail-editor-sidebar min-h-0 overflow-y-auto border-b border-[var(--border-subtle)] bg-[var(--surface-1)] p-5 lg:border-b-0 lg:border-r">
                    <div className="space-y-6">
                        <section className="inspector-section">
                            <div className="inspector-section-header">
                                <h3 className="inspector-section-title">Profile</h3>
                                <Button type="button" variant="secondary" size="sm" onClick={() => setIsPictureDialogOpen(true)}>
                                    Picture
                                </Button>
                            </div>
                            {isNameEditable ? (
                                <Input
                                    type="text"
                                    value={title}
                                    onChange={(e) => setTitle(e.target.value)}
                                    onBlur={() => handleBlur('name', title)}
                                    placeholder="Untitled Agent"
                                    className="font-medium"
                                />
                            ) : (
                                <div className="form-field-control flex min-h-16 flex-col justify-center px-3 py-2.5">
                                    <div className="type-eyebrow">
                                        Identifier
                                    </div>
                                    <div className="mt-1 break-all text-sm font-medium text-[var(--text-primary)]">
                                        {agent.name || 'Untitled Agent'}
                                    </div>
                                </div>
                            )}
                            <Textarea
                                value={description}
                                onChange={(e) => setDescription(e.target.value)}
                                onBlur={() => handleBlur('description', description)}
                                placeholder="What work does this agent own?"
                                className="min-h-[104px] resize-y text-[var(--text-secondary)]"
                            />
                        </section>

                        <section className="inspector-section" data-edu="agent-runtime">
                            <div className="inspector-section-header">
                                <div>
                                    <h3 className="inspector-section-title flex items-center gap-1.5">Model<ConceptHint concept="runtime" /></h3>
                                    <p className="inspector-section-meta">Agent Runtime and model</p>
                                </div>
                            </div>
                            <RuntimeModelPicker
                                catalog={runtimeCatalog}
                                availableHarnesses={availableHarnesses}
                                defaultRuntime={defaultRuntime}
                                value={agent.agent_runtime ?? null}
                                onChange={(agentRuntime) => onUpdate({ agent_runtime: agentRuntime })}
                                title="Agent model"
                                description="The model this agent runs on, unless overridden in a conversation."
                                scopeHint="Default for this agent"
                                manageHref={pod?.organization_id ? `/organizations/${pod.organization_id}/settings/agent-runtimes` : undefined}
                            />
                        </section>

                        <section className="inspector-section" data-edu="agent-access">
                            <div className="inspector-section-header">
                                <div>
                                    <h3 className="inspector-section-title flex items-center gap-1.5">Access<ConceptHint concept="scope" /></h3>
                                    <p className="inspector-section-meta">{linkedResourcesCount ? `${linkedResourcesCount} linked` : 'Nothing linked'}</p>
                                </div>
                                <Button type="button" variant="secondary" size="sm" className="gap-1.5" onClick={() => setIsAccessDialogOpen(true)}>
                                    <Plus className="h-3.5 w-3.5" />
                                    Manage
                                </Button>
                            </div>

                            <SummaryRow label="Tools">
                                {toolChips.visible.length === 0 ? (
                                    <EmptyInline>No tools</EmptyInline>
                                ) : (
                                    <>
                                        {toolChips.visible.map((tool) => (
                                            <SummaryChip
                                                key={tool}
                                                icon={<Wrench className="h-3.5 w-3.5" />}
                                                label={formatAccessLabel(tool)}
                                                onRemove={() => onUpdate({ tool_sets: (agent.tool_sets || []).filter((item) => item !== tool) })}
                                            />
                                        ))}
                                        {toolChips.overflow ? <SummaryChip label={`+${toolChips.overflow}`} /> : null}
                                    </>
                                )}
                            </SummaryRow>

                            <SummaryRow label="Apps">
                                {appChips.visible.length === 0 ? (
                                    <EmptyInline>No apps</EmptyInline>
                                ) : (
                                    <>
                                        {appChips.visible.map((label) => <SummaryChip key={label} icon={<Globe2 className="h-3.5 w-3.5" />} label={label} />)}
                                        {appChips.overflow ? <SummaryChip label={`+${appChips.overflow}`} /> : null}
                                    </>
                                )}
                            </SummaryRow>

                            <SummaryRow label="Tables">
                                {tableChips.visible.length === 0 ? (
                                    <EmptyInline>No tables</EmptyInline>
                                ) : (
                                    <>
                                        {tableChips.visible.map((label) => <SummaryChip key={label} icon={<Database className="h-3.5 w-3.5" />} label={label} />)}
                                        {tableChips.overflow ? <SummaryChip label={`+${tableChips.overflow}`} /> : null}
                                    </>
                                )}
                            </SummaryRow>

                            <SummaryRow label="Files">
                                {folderChips.visible.length === 0 ? (
                                    <EmptyInline>No folders</EmptyInline>
                                ) : (
                                    <>
                                        {folderChips.visible.map((label) => <SummaryChip key={label} icon={<Folder className="h-3.5 w-3.5" />} label={label} />)}
                                        {folderChips.overflow ? <SummaryChip label={`+${folderChips.overflow}`} /> : null}
                                    </>
                                )}
                            </SummaryRow>

                            <SummaryRow label="Functions">
                                {functionChips.visible.length === 0 ? (
                                    <EmptyInline>No functions</EmptyInline>
                                ) : (
                                    <>
                                        {functionChips.visible.map((label) => <SummaryChip key={label} icon={<Code2 className="h-3.5 w-3.5" />} label={label} />)}
                                        {functionChips.overflow ? <SummaryChip label={`+${functionChips.overflow}`} /> : null}
                                    </>
                                )}
                            </SummaryRow>

                            <SummaryRow label="Agent Tools">
                                {agentToolChips.visible.length === 0 ? (
                                    <EmptyInline>No agent tools</EmptyInline>
                                ) : (
                                    <>
                                        {agentToolChips.visible.map((label) => <SummaryChip key={label} icon={<Bot className="h-3.5 w-3.5" />} label={label} />)}
                                        {agentToolChips.overflow ? <SummaryChip label={`+${agentToolChips.overflow}`} /> : null}
                                    </>
                                )}
                            </SummaryRow>
                        </section>

                        <section className="inspector-section" data-edu="agent-sharing">
                            <ResourceVisibilitySelect
                                value={agent.visibility}
                                podId={podId}
                                resourceType="agent"
                                resourceId={agent.id}
                                resourceLabel="agents"
                                resourceName={agent.name}
                                shareUrl={shareUrl}
                                onChange={async (visibility) => {
                                    if (onShareVisibilityChange) {
                                        await onShareVisibilityChange(visibility);
                                    }
                                    onUpdate({ visibility });
                                }}
                            />
                        </section>

                        <section className="inspector-section" data-edu="agent-variables">
                            <div className="inspector-section-header">
                                <h3 className="inspector-section-title flex items-center gap-1.5">Variables<ConceptHint concept="variable" /></h3>
                                <Button type="button" variant="secondary" size="sm" className="gap-1.5" onClick={() => setIsContractDialogOpen(true)}>
                                    <Plus className="h-3.5 w-3.5" />
                                    Edit
                                </Button>
                            </div>

                            <SummaryRow label="Input">
                                {inputVariables.length === 0 ? (
                                    <EmptyInline>No variables</EmptyInline>
                                ) : (
                                    inputVariables.map((name) => (
                                        <SummaryChip
                                            key={name}
                                            label={name}
                                            onRemove={() => onUpdate({ input_schema: removeSchemaProperty(agent.input_schema, name) })}
                                        />
                                    ))
                                )}
                            </SummaryRow>
                            <SummaryRow label="Output">
                                {outputVariables.length === 0 ? (
                                    <EmptyInline>Freeform</EmptyInline>
                                ) : (
                                    outputVariables.map((name) => (
                                        <SummaryChip
                                            key={name}
                                            icon={<FileText className="h-3.5 w-3.5" />}
                                            label={name}
                                            onRemove={() => onUpdate({ output_schema: removeSchemaProperty(agent.output_schema, name) })}
                                        />
                                    ))
                                )}
                            </SummaryRow>
                        </section>
                    </div>
                </aside>

                <main className="agent-detail-editor-main flex min-h-0 min-w-0 overflow-hidden bg-[var(--surface-1)]">
                    <div className="flex h-full min-h-0 w-full flex-col overflow-hidden bg-[var(--surface-1)]">
                        <div className="shrink-0 px-5 py-3" data-edu="agent-instructions">
                            <h3 className="text-sm font-medium text-[var(--text-primary)]">Instructions</h3>
                        </div>
                        <MarkdownEditor
                            content={instruction}
                            onChange={setInstruction}
                            placeholder="Tell the agent how to behave, what to do, and which rules to follow..."
                            className="min-h-0 flex-1 overflow-y-auto"
                            editorClassName="min-h-full bg-transparent px-6 py-5 text-base leading-7 shadow-none [&_p]:my-0 [&_p+p]:mt-4"
                        />
                    </div>
                </main>
            </div>

            <Dialog open={isPictureDialogOpen} onOpenChange={setIsPictureDialogOpen}>
                <DialogContent className="max-h-[86vh] max-w-xl overflow-y-auto">
                    <DialogHeader>
                        <DialogTitle>Display picture</DialogTitle>
                        <DialogDescription>Choose a small visual marker for this agent.</DialogDescription>
                    </DialogHeader>
                    <AgentAvatarPicker
                        name={title || agent.name || 'Agent'}
                        value={agent.icon_url}
                        onChange={(iconUrl) => onUpdate({ icon_url: iconUrl || undefined })}
                    />
                </DialogContent>
            </Dialog>

            <Dialog open={isAccessDialogOpen} onOpenChange={setIsAccessDialogOpen}>
                <DialogContent className="agent-access-dialog max-h-[86vh] max-w-4xl overflow-hidden p-0">
                    <DialogHeader className="agent-access-dialog-header border-b border-[var(--border-subtle)] px-5 py-4">
                        <DialogTitle>Manage access</DialogTitle>
                        <DialogDescription>Choose the tools, apps, tables, and folders this agent can use.</DialogDescription>
                    </DialogHeader>
                    <div className="resource-dialog-body">
                        <div className="resource-configuration-stack">
                            <PropertyRow label="Tools" icon={Box} className={accessSectionClassName}>
                                <div className="access-tool-grid">
                                    {Object.values(ToolSet).map((tool) => {
                                        const isSelected = (agent.tool_sets || []).includes(tool);

                                        return (
                                            <label key={tool} className="access-tool-option resource-list-row" data-selected={isSelected}>
                                                <Checkbox
                                                    id={`tool-${tool}`}
                                                    checked={isSelected}
                                                    onCheckedChange={(checked) => {
                                                        const current = agent.tool_sets || [];
                                                        const next = checked
                                                            ? [...current, tool]
                                                            : current.filter((t) => t !== tool);
                                                        onUpdate({ tool_sets: next });
                                                    }}
                                                />
                                                <span>{formatAccessLabel(tool)}</span>
                                            </label>
                                        );
                                    })}
                                </div>
                            </PropertyRow>

                            <ConnectorsSelector
                                podId={agent.pod_id}
                                selected={agent.accessible_connectors || []}
                                className={accessSectionClassName}
                                onChange={(configs) => onUpdate({ accessible_connectors: configs })}
                            />

                            <DatastoresSelector
                                podId={agent.pod_id}
                                selected={(agent.accessible_tables || []).map((entry) => entry.table_name)}
                                className={accessSectionClassName}
                                modeByName={Object.fromEntries(
                                    (agent.accessible_tables || []).map((entry) => [entry.table_name, entry.mode])
                                )}
                                onChange={(names) => {
                                    const modeByTable = new Map(
                                        (agent.accessible_tables || []).map((entry) => [entry.table_name, entry.mode])
                                    );
                                    onUpdate({
                                        accessible_tables: names.map((table_name) => ({
                                            table_name,
                                            mode: modeByTable.get(table_name) ?? TableAccessMode.WRITE,
                                        })),
                                    });
                                }}
                                onModeChange={(name, mode) => {
                                    onUpdate({
                                        accessible_tables: (agent.accessible_tables || []).map((entry) =>
                                            entry.table_name === name ? { ...entry, mode } : entry
                                        ),
                                    });
                                }}
                            />

                            <FoldersSelector
                                podId={agent.pod_id}
                                selected={agent.accessible_folders || []}
                                className={accessSectionClassName}
                                onChange={(folderIds) => onUpdate({ accessible_folders: folderIds })}
                            />

                            <FunctionsSelector
                                podId={agent.pod_id}
                                selected={agent.function_names || []}
                                onChange={(names) => onUpdate({ function_names: names } as Partial<Agent>)}
                            />

                            <AgentsSelector
                                podId={agent.pod_id}
                                selected={agent.agent_names || []}
                                onChange={(names) => onUpdate({ agent_names: names } as Partial<Agent>)}
                            />
                        </div>
                    </div>
                </DialogContent>
            </Dialog>

            <Dialog open={isContractDialogOpen} onOpenChange={setIsContractDialogOpen}>
                <DialogContent className="max-h-[86vh] max-w-4xl overflow-hidden p-0">
                    <DialogHeader className="border-b border-[var(--border-subtle)] px-5 py-4">
                        <DialogTitle>Edit contract</DialogTitle>
                        <DialogDescription>Define structured variables only when the agent needs them.</DialogDescription>
                    </DialogHeader>
                    <div className="max-h-[calc(86vh-96px)] overflow-y-auto px-5 py-5">
                        <div className="mb-3 flex items-center justify-between">
                            <h3 className="flex items-center gap-1 text-sm font-semibold text-[var(--text-primary)]">
                                <Code className="h-3.5 w-3.5" />
                                Schemas
                            </h3>
                            <div className="segmented-control">
                                <button
                                    onClick={() => setSchemaMode('builder')}
                                    className="segmented-control-item min-w-0 px-2"
                                    data-active={schemaMode === 'builder'}
                                    title="Visual Builder"
                                >
                                    <TableIcon className="h-4 w-4" />
                                </button>
                                <button
                                    onClick={() => setSchemaMode('json')}
                                    className="segmented-control-item min-w-0 px-2"
                                    data-active={schemaMode === 'json'}
                                    title="JSON Editor"
                                >
                                    <Code className="h-4 w-4" />
                                </button>
                            </div>
                        </div>

                        <Tabs value={schemaTab} onValueChange={(value) => setSchemaTab(value as 'input' | 'output')}>
                            <TabsList>
                                <TabsTrigger value="input">Variables</TabsTrigger>
                                <TabsTrigger value="output">Output</TabsTrigger>
                            </TabsList>

                            <TabsContent value="input" className="mt-3">
                                {schemaMode === 'builder' ? (
                                    <SchemaBuilder
                                        value={agent.input_schema || {}}
                                        onChange={(s) => handleSchemaChange('input_schema', s)}
                                    />
                                ) : (
                                    <div className="h-96 overflow-hidden rounded-lg bg-[var(--bg-canvas)] shadow-[var(--shadow-xs)]">
                                        <Editor
                                            height="100%"
                                            defaultLanguage="json"
                                            theme={monacoTheme}
                                            value={JSON.stringify(agent.input_schema || {}, null, 2)}
                                            onChange={(val) => {
                                                try {
                                                    if (val) handleSchemaChange('input_schema', JSON.parse(val));
                                                } catch {
                                                    // Ignore parse errors while typing
                                                }
                                            }}
                                            options={{ minimap: { enabled: false }, fontSize: 12, wordWrap: 'on' }}
                                        />
                                    </div>
                                )}
                            </TabsContent>

                            <TabsContent value="output" className="mt-3">
                                {schemaMode === 'builder' ? (
                                    <SchemaBuilder
                                        value={agent.output_schema || {}}
                                        onChange={(s) => handleSchemaChange('output_schema', s)}
                                    />
                                ) : (
                                    <div className="h-96 overflow-hidden rounded-lg bg-[var(--bg-canvas)] shadow-[var(--shadow-xs)]">
                                        <Editor
                                            height="100%"
                                            defaultLanguage="json"
                                            theme={monacoTheme}
                                            value={JSON.stringify(agent.output_schema || {}, null, 2)}
                                            onChange={(val) => {
                                                try {
                                                    if (val) handleSchemaChange('output_schema', JSON.parse(val));
                                                } catch {
                                                    // Ignore parse errors while typing
                                                }
                                            }}
                                            options={{ minimap: { enabled: false }, fontSize: 12, wordWrap: 'on' }}
                                        />
                                    </div>
                                )}
                            </TabsContent>
                        </Tabs>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    );
}

function SummaryRow({
    label,
    children,
}: {
    label: string;
    children: ReactNode;
}) {
    return (
        <div className="resource-summary-row">
            <div className="resource-summary-label">{label}</div>
            <div className="resource-summary-list">{children}</div>
        </div>
    );
}
