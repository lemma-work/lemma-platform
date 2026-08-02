import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import type { CreateAgentInput, UpdateAgentInput } from 'lemma-sdk';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { ConnectorMode, ResourceType, AccessMode, type Agent, type ConnectorAccessConfig, type CreateAgentData, type FolderAccessEntry, type ResourcePermissionGrant, type TableAccessEntry, type UpdateAgentData } from '@/lib/types';

interface AgentListResponse {
    items: Agent[];
    limit: number;
    next_page_cursor?: string | null;
    next_page_token?: string | null;
}

function toSdkAgentPayload<T extends CreateAgentData | UpdateAgentData>(data: T): CreateAgentInput | UpdateAgentInput {
    const rest = { ...data } as Partial<CreateAgentData & UpdateAgentData>;
    const toolSetsAlias = rest.tool_sets;
    const toolsets = rest.toolsets;
    delete rest.tool_sets;
    delete rest.toolsets;
    delete rest.accessible_connectors;
    delete rest.accessible_folders;
    delete rest.accessible_tables;
    delete rest.accessible_functions;
    delete rest.accessible_agents;
    return {
        ...rest,
        toolsets: toolsets ?? toolSetsAlias ?? undefined,
    };
}

function tablePermissionIds(mode: AccessMode | string | undefined): string[] {
    if (mode === AccessMode.READ) {
        return ['datastore.table.read', 'datastore.record.read'];
    }
    return ['datastore.table.read', 'datastore.record.read', 'datastore.record.write'];
}

function grantsToTableAccess(grants: ResourcePermissionGrant[] | undefined): TableAccessEntry[] {
    return (grants || [])
        .filter((grant) => grant.resource_type === ResourceType.DATASTORE_TABLE)
        .map((grant) => ({
            table_name: grant.resource_name,
            mode: grant.permission_ids?.includes('datastore.record.write') ? AccessMode.WRITE : AccessMode.READ,
        }));
}

function folderPermissionIds(mode: AccessMode | string | undefined): string[] {
    if (mode === AccessMode.READ) {
        return ['folder.read'];
    }
    return ['folder.read', 'folder.write'];
}

function grantsToFolderAccess(grants: ResourcePermissionGrant[] | undefined): FolderAccessEntry[] {
    return (grants || [])
        .filter((grant) => grant.resource_type === ResourceType.FOLDER)
        .map((grant) => ({
            folder_path: grant.resource_name,
            mode: grant.permission_ids?.includes('folder.write') ? AccessMode.WRITE : AccessMode.READ,
        }));
}

/**
 * Folder grants used to be a bare list of paths that always meant read+write.
 * Anything still carrying that shape keeps the access it was given.
 */
function normalizeFolderAccess(raw: unknown): FolderAccessEntry[] | undefined {
    if (!Array.isArray(raw)) return undefined;
    return raw.map((entry) => (
        typeof entry === 'string'
            ? { folder_path: entry, mode: AccessMode.WRITE }
            : entry as FolderAccessEntry
    ));
}

function grantsToConnectorAccess(grants: ResourcePermissionGrant[] | undefined): ConnectorAccessConfig[] {
    const accountGrant = (grants || []).find((grant) => grant.resource_type === ResourceType.CONNECTOR_ACCOUNT);
    return (grants || [])
        .filter((grant) => grant.resource_type === ResourceType.CONNECTOR)
        .map((grant) => ({
            app_name: grant.resource_name,
            mode: accountGrant ? ConnectorMode.FIXED : ConnectorMode.DYNAMIC,
            account_id: accountGrant?.resource_name,
        }));
}

async function resolveTableResourceName(
    client: ReturnType<typeof getLemmaClient>,
    table: TableAccessEntry,
): Promise<string> {
    const response = await client.tables.list({ limit: 500 });
    const match = (response.items || []).find((candidate) => candidate.id === table.table_name || candidate.name === table.table_name);
    return match?.name || table.table_name;
}

function grantsToFunctionAccess(grants: ResourcePermissionGrant[] | undefined): string[] {
    return (grants || [])
        .filter((grant) => grant.resource_type === ResourceType.FUNCTION)
        .map((grant) => grant.resource_name);
}

function grantsToAgentAccess(grants: ResourcePermissionGrant[] | undefined): string[] {
    return (grants || [])
        .filter((grant) => grant.resource_type === ResourceType.AGENT)
        .map((grant) => grant.resource_name);
}

async function buildResourceGrants(
    client: ReturnType<typeof getLemmaClient>,
    data: Pick<CreateAgentData | UpdateAgentData, 'accessible_connectors' | 'accessible_folders' | 'accessible_tables' | 'accessible_functions' | 'accessible_agents'>,
): Promise<ResourcePermissionGrant[]> {
    const grants: ResourcePermissionGrant[] = [];

    for (const table of data.accessible_tables || []) {
        grants.push({
            resource_type: ResourceType.DATASTORE_TABLE,
            resource_name: await resolveTableResourceName(client, table),
            permission_ids: tablePermissionIds(table.mode),
        });
    }

    for (const folder of data.accessible_folders || []) {
        grants.push({
            resource_type: ResourceType.FOLDER,
            resource_name: folder.folder_path,
            permission_ids: folderPermissionIds(folder.mode),
        });
    }

    for (const app of data.accessible_connectors || []) {
        grants.push({
            resource_type: ResourceType.CONNECTOR,
            resource_name: app.app_name,
            permission_ids: ['connector.use'],
        });
        if (app.mode === ConnectorMode.FIXED && app.account_id) {
            grants.push({
                resource_type: ResourceType.CONNECTOR_ACCOUNT,
                resource_name: app.account_id,
                permission_ids: ['connector_account.use'],
            });
        }
    }

    for (const functionName of data.accessible_functions || []) {
        grants.push({
            resource_type: ResourceType.FUNCTION,
            resource_name: functionName,
            permission_ids: ['function.execute'],
        });
    }

    for (const agentName of data.accessible_agents || []) {
        grants.push({
            resource_type: ResourceType.AGENT,
            resource_name: agentName,
            permission_ids: ['agent.execute'],
        });
    }

    return grants;
}

export type AgentAccessFields = Pick<
    CreateAgentData | UpdateAgentData,
    'accessible_connectors' | 'accessible_folders' | 'accessible_tables' | 'accessible_functions' | 'accessible_agents'
>;

function agentAccessFields(agent: Agent): AgentAccessFields {
    return {
        accessible_tables: agent.accessible_tables,
        accessible_folders: agent.accessible_folders,
        accessible_connectors: agent.accessible_connectors,
        accessible_functions: agent.function_names ?? undefined,
        accessible_agents: agent.agent_names ?? undefined,
    };
}

export function carriesAccess(data: AgentAccessFields): boolean {
    return data.accessible_connectors !== undefined
        || data.accessible_folders !== undefined
        || data.accessible_tables !== undefined
        || data.accessible_functions !== undefined
        || data.accessible_agents !== undefined;
}

function sameEntries<T>(
    current: T[] | null | undefined,
    next: T[] | null | undefined,
    toKey: (entry: T) => string,
): boolean {
    // An absent field isn't a change — the caller simply isn't touching it.
    // An explicit null is: it clears the wiring, so it compares as empty.
    if (next === undefined) return true;
    const left = (current || []).map(toKey).sort();
    const right = (next || []).map(toKey).sort();
    return left.length === right.length && left.every((key, index) => key === right[index]);
}

/**
 * Replacing an agent's grants needs `agent.delete`, which pod editors do not
 * hold — so the permissions call has to fire only when the wiring genuinely
 * moved. Presence of the `accessible_*` fields is not that signal: a normalized
 * agent always carries them (as arrays, never undefined), so every save used to
 * look like an access change and every editor save died on a 403 that named a
 * delete permission they were not exercising.
 */
export function agentAccessChanged(agent: Agent, next: AgentAccessFields): boolean {
    const current = agentAccessFields(agent);
    const unchanged =
        sameEntries(current.accessible_tables, next.accessible_tables, (table) => `${table.table_name}:${table.mode}`)
        && sameEntries(current.accessible_folders, next.accessible_folders, (folder) => `${folder.folder_path}:${folder.mode}`)
        && sameEntries(current.accessible_connectors, next.accessible_connectors, (app) => `${app.app_name}:${app.mode}:${app.account_id ?? ''}`)
        && sameEntries(current.accessible_functions, next.accessible_functions, (name) => name)
        && sameEntries(current.accessible_agents, next.accessible_agents, (name) => name);
    return !unchanged;
}

function normalizeAgent(raw: Record<string, unknown>): Agent {
    const permissions = raw.permissions as Agent['permissions'] | undefined;
    const grants = permissions?.grants as ResourcePermissionGrant[] | undefined;
    const rawRuntime = raw.agent_runtime as (Agent['agent_runtime'] & { harness_kind?: string }) | undefined;

    return {
        id: String(raw.id || ''),
        pod_id: String(raw.pod_id || ''),
        user_id: String(raw.user_id || ''),
        name: String(raw.name || ''),
        description: (raw.description as string | null | undefined) ?? null,
        icon_url: (raw.icon_url as string | null | undefined) ?? null,
        agent_runtime: rawRuntime ?? null,
        harness_kind: (raw.harness_kind as Agent['harness_kind'] | undefined) ?? rawRuntime?.harness_kind,
        model_name: (raw.model_name as Agent['model_name'] | undefined) ?? rawRuntime?.model_name,
        instruction: String(raw.instruction || ''),
        input_schema: (raw.input_schema as Record<string, unknown> | undefined) || {},
        output_schema: (raw.output_schema as Record<string, unknown> | undefined) || {},
        tool_sets: (raw.toolsets as Agent['tool_sets'] | undefined) || (raw.tool_sets as Agent['tool_sets'] | undefined) || [],
        toolsets: (raw.toolsets as Agent['tool_sets'] | undefined) || (raw.tool_sets as Agent['tool_sets'] | undefined) || [],
        visibility: (raw.visibility as Agent['visibility'] | undefined) ?? undefined,
        allowed_actions: Array.isArray(raw.allowed_actions) ? raw.allowed_actions.filter((action): action is string => typeof action === 'string') : undefined,
        permissions,
        accessible_tables: (raw.accessible_tables as Agent['accessible_tables'] | undefined) || grantsToTableAccess(grants),
        accessible_folders: normalizeFolderAccess(raw.accessible_folders) || grantsToFolderAccess(grants),
        accessible_connectors: (raw.accessible_connectors as Agent['accessible_connectors'] | undefined) || grantsToConnectorAccess(grants),
        function_names: (raw.function_names as string[] | undefined) || grantsToFunctionAccess(grants),
        agent_names: (raw.agent_names as string[] | undefined) || grantsToAgentAccess(grants),
        created_at: String(raw.created_at || ''),
        updated_at: String(raw.updated_at || raw.created_at || ''),
    };
}

export const agentsQueryOptions = (podId: string | undefined) => ({
        queryKey: ['agents', podId],
        queryFn: async (): Promise<AgentListResponse> => {
            const response = await getLemmaClient(podId).agents.list();

            return {
                items: (response.items || []).map((item) => normalizeAgent(item as unknown as Record<string, unknown>)),
                limit: 100,
                next_page_token: response.next_page_token,
            };
        },
        refetchOnWindowFocus: true,
        staleTime: 30000,
});

export const useAgents = (podId: string | undefined) => {
    return useQuery({
        ...agentsQueryOptions(podId),
        enabled: !!podId,
    });
};

export const useAgent = (podId: string | undefined, agentName: string | undefined) => {
    return useQuery({
        queryKey: ['agent', podId, agentName],
        queryFn: async () => {
            const response = await getLemmaClient(podId).agents.get(agentName!);
            return normalizeAgent(response as unknown as Record<string, unknown>);
        },
        enabled: !!podId && !!agentName,
    });
};

export const useCreateAgent = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: async ({ podId, data }: { podId: string; data: CreateAgentData }) => {
            const client = getLemmaClient(podId);
            const grants = await buildResourceGrants(client, data);
            const payload = toSdkAgentPayload(data) as CreateAgentInput;
            // Grants ride along with the create instead of following it as a
            // separate permissions-replace: one request, applied in the same
            // transaction, and gated on `agent.create` alone. The old two-step
            // left an editor with an agent that existed but reached nothing.
            const response = await client.agents.create(
                grants.length > 0 ? { ...payload, permissions: { grants: grants as never } } : payload,
            );
            return normalizeAgent(response as unknown as Record<string, unknown>);
        },
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['agents', variables.podId] });
        },
        onError: (error) => {
            console.error(error);
        },
    });
};

export const useUpdateAgent = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: async ({ podId, agentName, data }: { podId: string; agentName: string; data: UpdateAgentData }) => {
            const client = getLemmaClient(podId);
            // Read the pre-update wiring before the PATCH lands, so the
            // comparison is against what the server currently holds. Only the
            // agent editor sends `accessible_*` at all, so nothing else pays
            // for the lookup.
            const current = carriesAccess(data)
                ? queryClient.getQueryData<Agent>(['agent', podId, agentName])
                    ?? normalizeAgent(await client.agents.get(agentName) as unknown as Record<string, unknown>)
                : undefined;
            const response = await client.agents.update(agentName, toSdkAgentPayload(data) as UpdateAgentInput);
            if (current && agentAccessChanged(current, data)) {
                const grants = await buildResourceGrants(client, data);
                await client.agents.permissions.replace(agentName, { grants: grants as never });
            }
            return normalizeAgent(response as unknown as Record<string, unknown>);
        },
        onSuccess: (result, variables) => {
            queryClient.invalidateQueries({ queryKey: ['agents', variables.podId] });
            queryClient.invalidateQueries({ queryKey: ['agent', variables.podId, variables.agentName] });
        },
        onError: (error) => {
            console.error(error);
        },
    });
};

export const useDeleteAgent = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({ podId, agentName }: { podId: string; agentName: string }) =>
            getLemmaClient(podId).agents.delete(agentName),
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['agents', variables.podId] });
        },
        onError: (error) => {
            console.error(error);
        },
    });
};
