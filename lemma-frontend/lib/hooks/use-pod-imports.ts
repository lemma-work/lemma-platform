'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { lemmaFetch } from '@/lib/sdk/lemma-client';

export type ImportStatus = 'PLANNED' | 'APPLYING' | 'COMPLETED' | 'FAILED' | 'CANCELLED';

export interface ImportStep {
    resource_type: string;
    resource_name: string;
    action: 'CREATE' | 'UPDATE' | 'SKIP';
    status: 'PENDING' | 'COMPLETED' | 'FAILED' | 'SKIPPED';
    destructive: boolean;
    error?: string | null;
}

export interface Capability {
    tier: string;
    summary: string;
}

export interface PodImport {
    id: string;
    pod_id: string;
    status: ImportStatus;
    source_name?: string | null;
    plan: ImportStep[];
    requirements: Record<string, unknown>;
    capabilities: Capability[];
    progress_done: number;
    progress_total: number;
    error?: string | null;
    started_at?: string | null;
    completed_at?: string | null;
}

async function readError(res: Response): Promise<string> {
    try {
        const body = await res.json();
        return body?.detail || body?.message || res.statusText;
    } catch {
        return res.statusText;
    }
}

/** Upload a bundle archive and get back the computed plan (PLANNED). */
export const useCreateImport = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async ({
            podId,
            file,
            sourceName,
        }: {
            podId: string;
            file: File;
            sourceName?: string;
        }): Promise<PodImport> => {
            const form = new FormData();
            form.append('bundle', file);
            if (sourceName) form.append('source_name', sourceName);
            const res = await lemmaFetch(`/pods/${podId}/imports`, { method: 'POST', body: form });
            if (!res.ok) throw new Error(await readError(res));
            return res.json();
        },
        onSuccess: (imp, { podId }) => {
            queryClient.setQueryData(['pod-imports', podId, imp.id], imp);
        },
    });
};

/** "Create a new pod" path: create a fresh pod from the bundle, then plan the
 * import into it. Returns the PLANNED import — its `pod_id` is the new pod. */
export const useImportIntoNewPod = () =>
    useMutation({
        mutationFn: async ({
            file,
            organizationId,
            sourceKind = 'upload',
            sourceRef,
        }: {
            file: File;
            organizationId: string;
            sourceKind?: string;
            sourceRef?: string;
        }): Promise<PodImport> => {
            const form = new FormData();
            form.append('bundle', file);
            form.append('organization_id', organizationId);
            form.append('source_kind', sourceKind);
            if (sourceRef) form.append('source_ref', sourceRef);
            const res = await lemmaFetch('/imports', { method: 'POST', body: form });
            if (!res.ok) throw new Error(await readError(res));
            return res.json();
        },
    });

/** Shared-link path: create a new pod from an existing pod's bundle (the engine
 * behind /import/p/<id>). Returns the PLANNED import for the new pod. */
export const useImportFromPod = () =>
    useMutation({
        mutationFn: async ({ sourcePodId }: { sourcePodId: string }): Promise<PodImport> => {
            const res = await lemmaFetch(`/imports/from-pod/${sourcePodId}`, { method: 'POST' });
            if (!res.ok) throw new Error(await readError(res));
            return res.json();
        },
    });

/** Poll an import; auto-refreshes while it is applying. */
export const usePodImport = (podId?: string, importId?: string) =>
    useQuery({
        queryKey: ['pod-imports', podId, importId],
        queryFn: async (): Promise<PodImport> => {
            const res = await lemmaFetch(`/pods/${podId}/imports/${importId}`);
            if (!res.ok) throw new Error(await readError(res));
            return res.json();
        },
        enabled: !!podId && !!importId,
        refetchInterval: (query) =>
            (query.state.data as PodImport | undefined)?.status === 'APPLYING' ? 1500 : false,
    });

/** Export the pod as a bundle archive and trigger a browser download. */
export const useExportPod = () =>
    useMutation({
        mutationFn: async ({
            podId,
            withData = true,
        }: {
            podId: string;
            withData?: boolean;
        }): Promise<string> => {
            const res = await lemmaFetch(`/pods/${podId}/export?with_data=${withData}`);
            if (!res.ok) throw new Error(await readError(res));
            const blob = await res.blob();
            const match = (res.headers.get('content-disposition') ?? '').match(/filename="?([^"]+)"?/);
            const filename = match?.[1] ?? 'pod-bundle.zip';
            const url = URL.createObjectURL(blob);
            const anchor = document.createElement('a');
            anchor.href = url;
            anchor.download = filename;
            document.body.appendChild(anchor);
            anchor.click();
            anchor.remove();
            URL.revokeObjectURL(url);
            return filename;
        },
    });

/** Apply (or resume) an import. Re-callable: completed steps are skipped.
 * `variables` resolves the bundle's ${var} placeholders (connector accounts);
 * pod-member assignees default to the importing user. */
export const useApplyImport = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async ({
            podId,
            importId,
            variables,
        }: {
            podId: string;
            importId: string;
            variables?: Record<string, string>;
        }): Promise<PodImport> => {
            const res = await lemmaFetch(`/pods/${podId}/imports/${importId}/apply`, {
                method: 'POST',
                body: JSON.stringify({ variables: variables ?? {} }),
            });
            if (!res.ok) throw new Error(await readError(res));
            return res.json();
        },
        onSuccess: (imp, { podId }) => {
            queryClient.setQueryData(['pod-imports', podId, imp.id], imp);
        },
    });
};
