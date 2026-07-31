'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import type { Column, Datastore, Table } from '@/lib/types';
import type { FileSearchResponse, RecordSort } from 'lemma-sdk';
import type { FilterRule } from '../types/app';
import { buildRecordQueryFilters, buildRecordQuerySort } from '@/lib/utils/datastore-records';

const DEFAULT_DATASTORE_NAME = 'default';

type FileSearchScopeMode = 'DIRECT' | 'SUBTREE';
type FileSearchMethod = 'VECTOR' | 'TEXT' | 'HYBRID';
type SearchDatastoreFilesInput = {
    podId: string;
    datastoreName: string;
    query: string;
    limit?: number;
    search_method?: FileSearchMethod;
    scope_mode?: FileSearchScopeMode;
    scope_path?: string | null;
    directory_path?: string | null;
    include_subdirectories?: boolean;
};
function resolveDirectoryPath(
    directoryPath?: string | null,
    legacyParentId?: string | null
): string | undefined {
    const candidate = directoryPath ?? legacyParentId;
    if (candidate === null) return '/';
    if (candidate === undefined) return undefined;

    const trimmed = candidate.trim();
    if (!trimmed) return '/';
    return trimmed;
}

function resolveFilePath(
    filePath?: string,
    legacyFileId?: string
): string {
    const target = filePath ?? legacyFileId;
    if (!target) {
        throw new Error('A file path is required.');
    }
    return target;
}

type CreateDatastoreSchema = {
    name: string;
    description?: string;
};

type CreateTableSchema = {
    name: string;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    columns: any[];
    primary_key_column?: string;
    visibility?: string | null;
};

type TableMigrationRequestSchema = {
    config?: Record<string, unknown>;
    operations?: unknown[];
};

interface RecordListResponseLike {
    items?: Record<string, unknown>[];
    records?: Record<string, unknown>[];
    rows?: Record<string, unknown>[];
    limit?: number;
    offset?: number;
    total?: number;
    next_page_token?: string | null;
    next_page_cursor?: string | null;
}

interface NormalizedRecordList {
    items: Record<string, unknown>[];
    limit: number;
    next_page_cursor?: string | null;
    next_page_token?: string | null;
    total?: number;
}

function normalizeRecordList(
    response: RecordListResponseLike,
    fallbackLimit: number,
    fallbackOffset = 0
): NormalizedRecordList {
    const items = response.items || response.records || response.rows || [];
    const limit = response.limit || fallbackLimit;
    const total = response.total;

    const nextCursor =
        response.next_page_cursor ??
        response.next_page_token ??
        (typeof total === 'number' && fallbackOffset + items.length < total
            ? String(fallbackOffset + limit)
            : null);

    return {
        items,
        limit,
        total,
        next_page_cursor: nextCursor,
        next_page_token: response.next_page_token,
    };
}

function toCursorPage<T extends { next_page_token?: string | null }>(response: T): T & { next_page_cursor?: string | null } {
    return {
        ...response,
        next_page_cursor: response.next_page_token ?? null,
    };
}

function normalizeColumn(raw: Record<string, unknown>): Column {
    return {
        name: String(raw.name || ''),
        type: String(raw.type || 'TEXT') as Column['type'],
        description: (raw.description as string | null | undefined) ?? undefined,
        required: (raw.required as boolean | undefined) ?? undefined,
        unique: (raw.unique as boolean | undefined) ?? undefined,
        default: raw.default as Column['default'],
        foreign_key: (raw.foreign_key as Column['foreign_key']) ?? undefined,
        max_length: (raw.max_length as number | undefined) ?? undefined,
        type_params: (raw.type_params as Record<string, unknown> | undefined) ?? undefined,
        options: (raw.options as string[] | undefined) ?? undefined,
        auto: (raw.auto as boolean | undefined) ?? undefined,
        computed: (raw.computed as boolean | undefined) ?? undefined,
        expression: (raw.expression as string | undefined) ?? undefined,
    };
}

export function normalizeTable(raw: Record<string, unknown>): Table {
    const columns = Array.isArray(raw.columns)
        ? (raw.columns as Record<string, unknown>[]).map(normalizeColumn)
        : [];

    return {
        name: String(raw.name || raw.name || ''),
        primary_key_column: String(raw.primary_key_column || 'id'),
        columns,
        visibility: raw.visibility as Table['visibility'],
        enable_rls: typeof raw.enable_rls === 'boolean' ? raw.enable_rls : undefined,
        config: (raw.config as Table['config']) ?? undefined,
        allowed_actions: Array.isArray(raw.allowed_actions) ? raw.allowed_actions.filter((action): action is string => typeof action === 'string') : undefined,
    };
}

// Datastores are not a first-class backend resource: every pod has exactly one
// implicit datastore (DEFAULT_DATASTORE_NAME), and the real resources live
// inside it (tables and records, which DO hit the API below). The datastore
// hooks below therefore synthesize a single, stable datastore object so that UI
// code expecting a datastore list/record keeps working without a dedicated
// endpoint. The "create"/"update"/"delete" mutations are intentional no-ops that
// just return the synthetic shape and invalidate caches; there is nothing to
// persist server-side.
function buildSyntheticDatastore(podId: string, overrides?: Partial<Datastore>): Datastore {
    return {
        id: overrides?.id || `${podId}:${DEFAULT_DATASTORE_NAME}`,
        name: overrides?.name || DEFAULT_DATASTORE_NAME,
        description: overrides?.description,
        user_id: overrides?.user_id || '',
        created_at: overrides?.created_at || new Date(0).toISOString(),
        updated_at: overrides?.updated_at || new Date(0).toISOString(),
    };
}

export const useDatastoreQuery = (podId: string | undefined, datastoreName: string | undefined, tableName: string | undefined, filters?: FilterRule[], columns?: string[], page = 1, limit = 50) => {
    const offset = (page - 1) * limit;

    return useQuery({
        queryKey: ['datastore-query', podId, datastoreName, tableName, filters, columns, page, limit],
        queryFn: async () => {
            if (!podId || !datastoreName || !tableName) return [];

            const response = (filters && filters.length > 0
                ? await getLemmaClient(podId).records.query(tableName, {
                    filters: buildRecordQueryFilters(filters),
                    limit,
                    offset,
                })
                : await getLemmaClient(podId).records.list(
                    tableName,
                    {
                        limit,
                        offset,
                    }
                )) as RecordListResponseLike;

            const result = normalizeRecordList(response, limit, offset);

            // Client-side column projection if needed (API might not support it)
            let records = result.items;
            if (columns && columns.length > 0) {
                records = records.map(record => {
                    const projected: Record<string, unknown> = {};
                    columns.forEach(col => {
                        if (record[col] !== undefined) projected[col] = record[col];
                    });
                    // Preserve ID if not selected but usually needed
                    if (record.id && !projected.id) projected.id = record.id;
                    return projected;
                });
            }

            return records;
        },
        enabled: !!podId && !!datastoreName && !!tableName,
    });
};

export const useDatastores = (podId: string | undefined) => {
    return useQuery({
        queryKey: ['datastores', podId],
        queryFn: () => {
            return {
                limit: 1,
                next_page_cursor: null,
                next_page_token: null,
                items: [
                    buildSyntheticDatastore(podId!, {
                        name: DEFAULT_DATASTORE_NAME,
                    }),
                ],
            };
        },
        enabled: !!podId,
    });
};

export const useDatastore = (podId: string | undefined, datastoreName: string | undefined) => {
    return useQuery({
        queryKey: ['datastores', podId, datastoreName],
        queryFn: () => {
            const name = datastoreName || DEFAULT_DATASTORE_NAME;
            return buildSyntheticDatastore(podId!, {
                name,
            });
        },
        enabled: !!podId && !!datastoreName,
    });
};

export const useCreateDatastore = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: async ({ podId, data }: { podId: string; data: CreateDatastoreSchema }) => {
            return buildSyntheticDatastore(podId, {
                name: data.name || DEFAULT_DATASTORE_NAME,
                description: data.description,
                created_at: new Date().toISOString(),
                updated_at: new Date().toISOString(),
            });
        },
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['datastores', variables.podId] });
        },
    });
};

export const useUpdateDatastore = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: async ({ podId, datastoreName, data }: { podId: string; datastoreName: string; data: { name?: string; description?: string } }) => {
            return buildSyntheticDatastore(podId, {
                name: data.name || datastoreName || DEFAULT_DATASTORE_NAME,
                description: data.description,
                updated_at: new Date().toISOString(),
            });
        },
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['datastores', variables.podId] });
            queryClient.invalidateQueries({ queryKey: ['datastores', variables.podId, variables.datastoreName] });
        },
    });
};

export const useDeleteDatastore = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: async ({ datastoreName }: { podId: string; datastoreName: string }) => ({
            success: true,
            name: datastoreName,
        }),
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['datastores', variables.podId] });
        },
    });
};

// Tables
interface UseTablesOptions {
    enabled?: boolean;
}

export const tablesQueryOptions = (podId: string | undefined) => ({
    queryKey: ['tables', podId] as const,
    queryFn: async () => {
        const response = await getLemmaClient(podId).tables.list();
        return {
            ...toCursorPage(response),
            items: (response.items || []).map((item) => normalizeTable(item as unknown as Record<string, unknown>)),
        };
    },
});

export const useTables = (
    podId: string | undefined,
    datastoreName?: string | undefined,
    options: UseTablesOptions = {}
) => {
    void datastoreName;
    return useQuery({
        ...tablesQueryOptions(podId),
        enabled: (options.enabled ?? true) && !!podId,
    });
};

export const tableQueryOptions = (podId: string | undefined, tableName: string | undefined) => ({
    queryKey: ['table', podId, tableName] as const,
    queryFn: async () => {
        const response = await getLemmaClient(podId).tables.get(tableName!);
        return normalizeTable(response as unknown as Record<string, unknown>);
    },
});

export const useTable = (podId: string | undefined, datastoreName: string | undefined, tableName: string | undefined) => {
    void datastoreName;
    return useQuery({
        ...tableQueryOptions(podId, tableName),
        enabled: !!podId && !!tableName,
    });
};

export const useCreateTable = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: async ({ podId, datastoreName, data }: { podId: string; datastoreName: string; data: CreateTableSchema }) => {
            void datastoreName;
            const response = await getLemmaClient(podId).tables.create({
                name: data.name,
                primary_key_column: data.primary_key_column,
                columns: data.columns,
                visibility: data.visibility as never,
            });
            return normalizeTable(response as unknown as Record<string, unknown>);
        },
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['tables', variables.podId] });
        },
    });
};

export const useUpdateTable = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: async ({ podId, datastoreName, tableName, data }: { podId: string; datastoreName: string; tableName: string; data: TableMigrationRequestSchema }) => {
            void datastoreName;
            const response = await getLemmaClient(podId).tables.update(tableName, {
                config: data.config ?? { operations: data.operations || [] },
            });
            return normalizeTable(response as unknown as Record<string, unknown>);
        },
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['tables', variables.podId] });
            queryClient.invalidateQueries({ queryKey: ['table', variables.podId, variables.tableName] });
        },
    });
};

export const useDeleteTable = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({ podId, tableName }: { podId: string; datastoreName: string; tableName: string }) =>
            getLemmaClient(podId).tables.delete(tableName),
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['tables', variables.podId] });
        },
    });
};

// Records
interface TableRecordsQueryOptions {
    page?: number;
    limit?: number;
    sortField?: string | null;
    sortOrder?: 'asc' | 'desc';
    filters?: FilterRule[];
    pageToken?: string | null;
}

export const tableRecordsQueryOptions = (
    podId: string | undefined,
    datastoreName: string | undefined,
    tableName: string | undefined,
    options: TableRecordsQueryOptions = {}
) => {
    const page = options.page ?? 0;
    const limit = options.limit ?? 50;
    const sortField = options.sortField ?? null;
    const sortOrder = options.sortOrder ?? 'asc';
    const filters = options.filters ?? [];
    const pageToken = options.pageToken ?? null;
    const usesStructuredQuery = filters.length > 0 || sortField !== null;

    return {
        queryKey: ['records', podId, datastoreName, tableName, page, sortField, sortOrder, filters, pageToken] as const,
        queryFn: async () => {
            const response = (usesStructuredQuery
                ? await getLemmaClient(podId).records.query(tableName!, {
                    filters: buildRecordQueryFilters(filters),
                    sort: buildRecordQuerySort(sortField, sortOrder),
                    limit,
                    page_token: pageToken ?? undefined,
                })
                : await getLemmaClient(podId).records.list(tableName!, {
                    limit,
                    offset: page * limit,
                })) as RecordListResponseLike;
            const items = response.items || response.records || response.rows || [];

            return {
                ...response,
                items,
                total: response.total ?? items.length,
                limit: response.limit ?? limit,
            };
        },
    };
};

export const useRecords = (
    podId: string | undefined,
    datastoreName: string | undefined,
    tableName: string | undefined,
    options?: { limit?: number; offset?: number; sort?: RecordSort[] }
) => {
    return useQuery({
        queryKey: ['records', podId, datastoreName, tableName, options],
        queryFn: async () => {
            const response = await getLemmaClient(podId).records.list(
                tableName!,
                {
                    limit: options?.limit,
                    offset: options?.offset,
                    sort: options?.sort,
                }
            ) as RecordListResponseLike;
            return normalizeRecordList(response, options?.limit ?? 50, options?.offset ?? 0);
        },
        enabled: !!podId && !!datastoreName && !!tableName,
    });
};

export const useCreateRecord = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({ podId, tableName, data }: { podId: string; datastoreName: string; tableName: string; data: Record<string, unknown> }) =>
            getLemmaClient(podId).records.create(tableName, data),
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['records', variables.podId, variables.datastoreName, variables.tableName] });
        },
    });
};

export const useUpdateRecord = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({ podId, tableName, recordId, data }: { podId: string; datastoreName: string; tableName: string; recordId: string; data: Record<string, unknown> }) =>
            getLemmaClient(podId).records.update(tableName, recordId, data),
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['records', variables.podId, variables.datastoreName, variables.tableName] });
        },
    });
};

export const useDeleteRecord = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({ podId, tableName, recordId }: { podId: string; datastoreName: string; tableName: string; recordId: string }) =>
            getLemmaClient(podId).records.delete(tableName, recordId),
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['records', variables.podId, variables.datastoreName, variables.tableName] });
        },
    });
};

// Datastore Files
export const useDatastoreFiles = (
    podId: string | undefined,
    datastoreName: string | undefined,
    options?: {
        directory_path?: string | null;
        limit?: number;
        page_token?: string;
        parent_id?: string | null; // deprecated alias
    }
) => {
    return useQuery({
        queryKey: ['datastore-files', podId, datastoreName, options],
        queryFn: () => getLemmaClient(podId).files.list({
            directoryPath: resolveDirectoryPath(options?.directory_path, options?.parent_id),
            limit: options?.limit,
            pageToken: options?.page_token,
        }),
        enabled: !!podId && !!datastoreName,
    });
};

export const useDatastoreFile = (
    podId: string | undefined,
    datastoreName: string | undefined,
    filePath: string | undefined
) => {
    return useQuery({
        queryKey: ['datastore-files', podId, datastoreName, filePath],
        queryFn: () => getLemmaClient(podId).files.get(filePath!),
        enabled: !!podId && !!datastoreName && !!filePath,
    });
};

export const useUploadDatastoreFile = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({ podId, file, name, description, directory_path, parent_id, search_enabled }: {
            podId: string;
            datastoreName: string;
            file: File;
            name?: string;
            description?: string;
            directory_path?: string | null;
            parent_id?: string | null; // deprecated alias
            search_enabled?: boolean;
        }) => getLemmaClient(podId).files.upload(file, {
            name,
            description,
            directoryPath: resolveDirectoryPath(directory_path, parent_id),
            searchEnabled: search_enabled,
        }),
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['datastore-files', variables.podId, variables.datastoreName] });
        },
    });
};

/**
 * Bring-your-own markdown: replace a document's agent-facing `document.md` (and
 * the images it references) with a user-authored version. Applies to
 * non-markdown documents (PDF, Word, HTML, …); the original file is unchanged.
 */
export const useAttachDocumentMarkdown = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({ podId, file_path, fileId, markdown, images }: {
            podId: string;
            datastoreName: string;
            file_path?: string;
            fileId?: string; // deprecated alias
            markdown: Blob;
            images?: File[];
        }) => getLemmaClient(podId).files.markdown.attach(
            resolveFilePath(file_path, fileId),
            markdown,
            { images },
        ),
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['datastore-files', variables.podId, variables.datastoreName] });
        },
    });
};

/** Drop user-provided markdown so the document reverts to extraction. */
export const useDetachDocumentMarkdown = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({ podId, file_path, fileId }: {
            podId: string;
            datastoreName: string;
            file_path?: string;
            fileId?: string; // deprecated alias
        }) => getLemmaClient(podId).files.markdown.detach(resolveFilePath(file_path, fileId)),
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['datastore-files', variables.podId, variables.datastoreName] });
        },
    });
};

export const useCreateDatastoreFolder = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({ podId, name, directory_path, parent_id, description }: {
            podId: string;
            datastoreName: string;
            name: string;
            directory_path?: string | null;
            parent_id?: string | null; // deprecated alias
            description?: string;
        }) => getLemmaClient(podId).files.folder.create(name, {
            directoryPath: resolveDirectoryPath(directory_path, parent_id),
            description,
        }),
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['datastore-files', variables.podId, variables.datastoreName] });
        },
    });
};

export const useDeleteDatastoreFile = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({ podId, file_path, fileId }: {
            podId: string;
            datastoreName: string;
            file_path?: string;
            fileId?: string; // deprecated alias
        }) => getLemmaClient(podId).files.delete(resolveFilePath(file_path, fileId)),
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['datastore-files', variables.podId, variables.datastoreName] });
        },
    });
};

export const useUpdateDatastoreFile = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({ podId, file_path, fileId, data }: {
            podId: string;
            datastoreName: string;
            file_path?: string;
            fileId?: string; // deprecated alias
            data: {
                name?: string;
                description?: string;
                directory_path?: string | null;
                parent_id?: string | null; // deprecated alias
                search_enabled?: boolean;
                content?: string;
            };
        }) => {
            const targetPath = resolveFilePath(file_path, fileId);
            const targetDirectoryPath = resolveDirectoryPath(data.directory_path, data.parent_id);

            if (data.content !== undefined) {
                const blob = new Blob([data.content], { type: 'text/markdown' });
                return getLemmaClient(podId).files.update(targetPath, {
                    file: blob,
                    name: data.name,
                    description: data.description,
                    directoryPath: targetDirectoryPath,
                    searchEnabled: data.search_enabled,
                });
            }

            return getLemmaClient(podId).files.update(targetPath, {
                name: data.name,
                description: data.description,
                directoryPath: targetDirectoryPath,
                searchEnabled: data.search_enabled,
            });
        },
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['datastore-files', variables.podId, variables.datastoreName] });
            queryClient.invalidateQueries({
                queryKey: ['datastore-files', variables.podId, variables.datastoreName, variables.file_path ?? variables.fileId],
            });
        },
    });
};

export const useSearchDatastoreFiles = () => {
    return useMutation<FileSearchResponse, Error, SearchDatastoreFilesInput>({
        mutationFn: ({
            podId,
            query,
            limit,
            search_method,
            scope_mode,
            scope_path,
            directory_path,
            include_subdirectories,
        }) => {
            const normalizedScopePath = resolveDirectoryPath(scope_path, directory_path);
            const normalizedScopeMode = scope_mode ?? (include_subdirectories === undefined
                ? undefined
                : include_subdirectories
                    ? 'SUBTREE'
                    : 'DIRECT');
            const sdkSearchOptions = {
                limit,
                searchMethod: search_method ?? 'HYBRID',
                ...(normalizedScopePath ? { scopePath: normalizedScopePath } : {}),
                ...(normalizedScopeMode ? { scopeMode: normalizedScopeMode } : {}),
            } satisfies Parameters<ReturnType<typeof getLemmaClient>['files']['search']>[1];

            return getLemmaClient(podId).files.search(
                query,
                sdkSearchOptions
            );
        },
    });
};
