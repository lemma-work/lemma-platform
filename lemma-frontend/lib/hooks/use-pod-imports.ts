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

export interface GithubPublishResult {
    status: 'published' | 'not_connected' | 'failed';
    repo_url?: string | null;
    import_badge_markdown?: string | null;
    message?: string | null;
}

export interface GithubPublishPreview {
    repo_name: string;
    readme: string;
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

/** GitHub badge path: create a new pod from a public repo's bundle. Returns the
 * PLANNED import for the new pod. */
export const useImportFromGithub = () =>
    useMutation({
        mutationFn: async ({
            owner,
            repo,
            organizationId,
            ref = 'HEAD',
        }: {
            owner: string;
            repo: string;
            organizationId: string;
            ref?: string;
        }): Promise<PodImport> => {
            const params = new URLSearchParams({ organization_id: organizationId, ref });
            const res = await lemmaFetch(`/imports/from-github/${owner}/${repo}?${params}`, {
                method: 'POST',
            });
            if (!res.ok) throw new Error(await readError(res));
            return res.json();
        },
    });

/** GitHub badge path, "install into an existing pod": plan a public repo's
 * bundle into a pod the caller already has. Returns the PLANNED import. */
export const useImportFromGithubIntoPod = () =>
    useMutation({
        mutationFn: async ({
            podId,
            owner,
            repo,
            ref = 'HEAD',
        }: {
            podId: string;
            owner: string;
            repo: string;
            ref?: string;
        }): Promise<PodImport> => {
            const params = new URLSearchParams({ ref });
            const res = await lemmaFetch(
                `/pods/${podId}/imports/from-github/${owner}/${repo}?${params}`,
                { method: 'POST' },
            );
            if (!res.ok) throw new Error(await readError(res));
            return res.json();
        },
    });

/** Publish this pod as a new GitHub repo (bundle + generated README with an
 * import badge), via the GitHub connector already connected in Connectors
 * settings — no separate OAuth here. */
export const useGithubPublish = () =>
    useMutation({
        mutationFn: async ({
            podId,
            repoName,
            isPrivate = false,
        }: {
            podId: string;
            repoName?: string;
            isPrivate?: boolean;
        }): Promise<GithubPublishResult> => {
            const res = await lemmaFetch(`/pods/${podId}/export/github`, {
                method: 'POST',
                body: JSON.stringify({ repo_name: repoName, private: isPrivate }),
            });
            if (!res.ok) throw new Error(await readError(res));
            return res.json();
        },
    });

/** What Publish will actually write — repo name + rendered README — fetched
 * live as the user edits the form, without touching GitHub. */
export const useGithubPublishPreview = (podId: string, repoName: string, enabled: boolean) =>
    useQuery({
        queryKey: ['pod-github-publish-preview', podId, repoName],
        queryFn: async (): Promise<GithubPublishPreview> => {
            const params = new URLSearchParams(repoName ? { repo_name: repoName } : {});
            const res = await lemmaFetch(`/pods/${podId}/export/github/preview?${params}`);
            if (!res.ok) throw new Error(await readError(res));
            return res.json();
        },
        enabled,
        staleTime: 15_000,
    });

/** Poll an import; auto-refreshes while it is applying. `forcePoll` keeps it
 * polling for the exact duration of an in-flight apply call even before the
 * first fetch has landed (the apply POST itself blocks until done, so the
 * status field alone lags behind — see useApplyImport). */
export const usePodImport = (podId?: string, importId?: string, opts?: { forcePoll?: boolean }) =>
    useQuery({
        queryKey: ['pod-imports', podId, importId],
        queryFn: async (): Promise<PodImport> => {
            const res = await lemmaFetch(`/pods/${podId}/imports/${importId}`);
            if (!res.ok) throw new Error(await readError(res));
            return res.json();
        },
        enabled: !!podId && !!importId,
        refetchInterval: (query) =>
            opts?.forcePoll || (query.state.data as PodImport | undefined)?.status === 'APPLYING'
                ? 1500
                : false,
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
