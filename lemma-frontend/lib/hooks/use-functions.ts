'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { ConnectorMode, ResourceType, TableAccessMode, type ConnectorAccessConfig, type CreateFunctionData, type Function as FunctionType, type FunctionRun, type ResourcePermissionGrant, type TableAccessEntry, type UpdateFunctionData } from '@/lib/types';

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

async function buildResourceGrants(
    client: ReturnType<typeof getLemmaClient>,
    data: Pick<CreateFunctionData | UpdateFunctionData, 'accessible_connectors' | 'accessible_folders' | 'accessible_tables'>,
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

    return grants;
}

function toSdkFunctionPayload<T extends CreateFunctionData | UpdateFunctionData>(data: T) {
    const rest = { ...data } as Partial<CreateFunctionData & UpdateFunctionData>;
    delete rest.accessible_connectors;
    delete rest.accessible_folders;
    delete rest.accessible_tables;
    delete rest.input_schema;
    delete rest.output_schema;
    delete rest.config_schema;
    return rest;
}

function normalizeFunction(raw: Record<string, unknown>): FunctionType {
    const configValue =
        (raw.config as Record<string, unknown> | null | undefined) ??
        (raw.configs as Record<string, unknown> | null | undefined) ??
        null;
    const permissions = raw.permissions as FunctionType['permissions'] | undefined;
    const grants = permissions?.grants as ResourcePermissionGrant[] | undefined;

    return {
        id: String(raw.id || ''),
        pod_id: String(raw.pod_id || ''),
        user_id: String(raw.user_id || ''),
        name: String(raw.name || ''),
        description: (raw.description as string | null | undefined) ?? null,
        icon_url: (raw.icon_url as string | null | undefined) ?? null,
        config: configValue,
        config_schema: (raw.config_schema as Record<string, unknown> | null | undefined) ?? null,
        code_path: (raw.code_path as string | null | undefined) ?? null,
        code: (raw.code as string | null | undefined) ?? null,
        input_schema: (raw.input_schema as Record<string, unknown> | undefined) ?? {},
        output_schema: (raw.output_schema as Record<string, unknown> | undefined) ?? {},
        visibility: (raw.visibility as FunctionType['visibility'] | undefined) ?? undefined,
        allowed_actions: Array.isArray(raw.allowed_actions) ? raw.allowed_actions.filter((action): action is string => typeof action === 'string') : undefined,
        permissions,
        accessible_tables: (raw.accessible_tables as FunctionType['accessible_tables'] | undefined) ?? grantsToTableAccess(grants),
        accessible_folders: (raw.accessible_folders as string[] | undefined) ?? grantsToFolderAccess(grants),
        accessible_connectors: (raw.accessible_connectors as FunctionType['accessible_connectors'] | undefined) ?? grantsToConnectorAccess(grants),
        status: raw.status as FunctionType['status'],
        type: raw.type as FunctionType['type'],
        created_at: String(raw.created_at || ''),
        updated_at: String(raw.updated_at || ''),
    };
}

function normalizeFunctionRun(raw: Record<string, unknown>): FunctionRun {
    let outputData: Record<string, unknown> | null = null;
    if (raw.output_data && typeof raw.output_data === 'object' && !Array.isArray(raw.output_data)) {
        outputData = raw.output_data as Record<string, unknown>;
    } else if (typeof raw.output_data === 'string') {
        try {
            const parsed = JSON.parse(raw.output_data);
            outputData = (parsed && typeof parsed === 'object' && !Array.isArray(parsed))
                ? parsed as Record<string, unknown>
                : { value: raw.output_data };
        } catch {
            outputData = { value: raw.output_data };
        }
    }

    return {
        id: String(raw.id || ''),
        function_id: String(raw.function_id || ''),
        user_id: String(raw.user_id || ''),
        input_data: (raw.input_data as Record<string, unknown> | null | undefined) ?? null,
        output_data: outputData,
        status: raw.status as FunctionRun['status'],
        error: (raw.error as string | null | undefined) ?? null,
        logs: (raw.logs as string | null | undefined) ?? null,
        started_at: (raw.started_at as string | null | undefined) ?? null,
        completed_at: (raw.completed_at as string | null | undefined) ?? null,
        created_at: String(raw.created_at || ''),
    };
}

export const useFunctions = (podId: string | undefined) => {
    return useQuery({
        queryKey: ['functions', podId],
        queryFn: async () => {
            const response = await getLemmaClient(podId).functions.list();
            return {
                ...response,
                next_page_cursor: response.next_page_token ?? null,
                items: (response.items || []).map((item) => normalizeFunction(item as unknown as Record<string, unknown>)),
            };
        },
        enabled: !!podId,
    });
};

export const useFunction = (podId: string | undefined, name: string | undefined) => {
    return useQuery({
        queryKey: ['functions', podId, name],
        queryFn: async () => {
            const response = await getLemmaClient(podId).functions.get(name!);
            return normalizeFunction(response as unknown as Record<string, unknown>);
        },
        enabled: !!podId && !!name,
    });
};

export const useCreateFunction = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: async ({ podId, data }: { podId: string; data: CreateFunctionData }) => {
            const client = getLemmaClient(podId);
            const response = await client.functions.create(
                toSdkFunctionPayload(data) as Parameters<typeof client.functions.create>[0],
            );
            const grants = await buildResourceGrants(client, data);
            if (grants.length > 0) {
                await client.functions.permissions.replace(response.name, { grants: grants as never });
            }
            return normalizeFunction(response);
        },
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['functions', variables.podId] });
        },
    });
};

export const useUpdateFunction = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: async ({ podId, name, data }: { podId: string; name: string; data: UpdateFunctionData }) => {
            const client = getLemmaClient(podId);
            const response = await client.functions.update(
                name,
                toSdkFunctionPayload(data) as Parameters<typeof client.functions.update>[1],
            );
            if (
                data.accessible_connectors !== undefined ||
                data.accessible_folders !== undefined ||
                data.accessible_tables !== undefined
            ) {
                const grants = await buildResourceGrants(client, data);
                await client.functions.permissions.replace(name, { grants: grants as never });
            }
            return normalizeFunction(response);
        },
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['functions', variables.podId] });
            queryClient.invalidateQueries({ queryKey: ['functions', variables.podId, variables.name] });
        },
    });
};

export const useDeleteFunction = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({ podId, name }: { podId: string; name: string }) =>
            getLemmaClient(podId).functions.delete(name),
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['functions', variables.podId] });
        },
    });
};

// Function Runs
export const useFunctionRuns = (podId: string | undefined, functionName: string | undefined) => {
    return useQuery({
        queryKey: ['function-runs', podId, functionName],
        queryFn: async () => {
            const response = await getLemmaClient(podId).functions.runs.list(functionName!);
            return {
                ...response,
                next_page_cursor: response.next_page_token ?? null,
                items: (response.items || []).map((item) => normalizeFunctionRun(item as unknown as Record<string, unknown>)),
            };
        },
        enabled: !!podId && !!functionName,
    });
};

export const useFunctionRun = (podId: string | undefined, functionName: string | undefined, runId: string | undefined) => {
    return useQuery({
        queryKey: ['function-runs', podId, functionName, runId],
        queryFn: async () => {
            const response = await getLemmaClient(podId).functions.runs.get(functionName!, runId!);
            return normalizeFunctionRun(response as unknown as Record<string, unknown>);
        },
        enabled: !!podId && !!functionName && !!runId,
    });
};

export const useCreateFunctionRun = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: async ({ podId, functionName, input_data }: { podId: string; functionName: string; input_data?: Record<string, unknown> }) => {
            const response = await getLemmaClient(podId).functions.runs.create(functionName, {
                input: input_data,
            });
            return normalizeFunctionRun(response);
        },
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['function-runs', variables.podId, variables.functionName] });
        },
    });
};
