import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import type { CreateAgentInput, UpdateAgentInput } from 'lemma-sdk';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { ConnectorMode, ResourceType, TableAccessMode, type Agent, type ConnectorAccessConfig, type CreateAgentData, type ResourcePermissionGrant, type TableAccessEntry, type UpdateAgentData } from '@/lib/types';

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

function tablePermissionIds(mode: TableAccessMode | string | undefined): string[] {
    if (mode === TableAccessMode.READ) {
        return ['datastore.table.read', 'datastore.record.read'];
    }
    return ['datastore.table.read', 'datastore.record.read', 'datastore.record.write'];
}

function grantsToTableAccess(grants: ResourcePermissionGrant[] | undefined): TableAccessEntry[] {
    return (grants || [])
        .filter((grant) => grant.resource_type === ResourceType.DATASTORE_TABLE)
        .map((grant) => ({
            table_name: grant.resource_name,
            mode: grant.permission_ids?.includes('datastore.record.write') ? TableAccessMode.WRITE : TableAccessMode.READ,
        }));
}

function grantsToFolderAccess(grants: ResourcePermissionGrant[] | undefined): string[] {
    return (grants || [])
        .filter((grant) => grant.resource_type === ResourceType.FOLDER)
        .map((grant) => grant.resource_name);
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

    for (const folderName of data.accessible_folders || []) {
        grants.push({
            resource_type: ResourceType.FOLDER,
            resource_name: folderName,
            permission_ids: ['folder.read', 'folder.write'],
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
        accessible_folders: (raw.accessible_folders as string[] | undefined) || grantsToFolderAccess(grants),
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
            const response = await client.agents.create(toSdkAgentPayload(data) as CreateAgentInput);
            const grants = await buildResourceGrants(client, data);
            if (grants.length > 0) {
                await client.agents.permissions.replace(response.name, { grants: grants as never });
            }
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
            const response = await client.agents.update(agentName, toSdkAgentPayload(data) as UpdateAgentInput);
            if (
                data.accessible_connectors !== undefined ||
                data.accessible_folders !== undefined ||
                data.accessible_tables !== undefined ||
                data.accessible_functions !== undefined ||
                data.accessible_agents !== undefined
            ) {
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
