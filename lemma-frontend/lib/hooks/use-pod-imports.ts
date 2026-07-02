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
    /** The exact README text committed to the repo (post AI-polish / import-URL
     * rewrite); null when publish never reached the README stage. */
    readme?: string | null;
    message?: string | null;
}

/** One "progress" line of the NDJSON publish stream. `label` describes the
 * export/repo/readme stages; the upload stage carries per-file counters. */
export type GithubPublishProgress = {
    stage: 'export' | 'repo' | 'readme' | 'upload';
    label?: string;
    done?: number;
    total?: number;
    path?: string;
};

export interface GithubPublishPreview {
    repo_name: string;
    readme: string;
    /** True when publish will run an AI polish pass over the README draft. */
    ai_polish?: boolean;
    /** Non-zero bundle resource counts keyed by kind name (tables, functions,
     * agents, workflows, schedules, surfaces, apps). */
    resource_counts?: Record<string, number>;
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

type GithubPublishStreamLine =
    | ({ event: 'progress' } & GithubPublishProgress)
    | ({ event: 'result' } & GithubPublishResult)
    | { event?: undefined };

/** Publish this pod as a new GitHub repo (bundle + generated README with an
 * import badge), via the GitHub connector already connected in Connectors
 * settings — no separate OAuth here. The backend streams NDJSON progress
 * lines and finishes with an "event":"result" line; older backends respond
 * with one plain JSON object, which we still accept. */
export const useGithubPublish = () =>
    useMutation({
        mutationFn: async ({
            podId,
            repoName,
            isPrivate = false,
            readme,
            onProgress,
        }: {
            podId: string;
            repoName?: string;
            isPrivate?: boolean;
            /** User-edited README: published verbatim (import URLs still get
             * rewritten server-side) instead of the rendered + AI-polished draft. */
            readme?: string;
            onProgress?: (progress: GithubPublishProgress) => void;
        }): Promise<GithubPublishResult> => {
            const res = await lemmaFetch(`/pods/${podId}/export/github`, {
                method: 'POST',
                body: JSON.stringify({
                    repo_name: repoName,
                    private: isPrivate,
                    ...(readme !== undefined ? { readme } : {}),
                }),
            });
            if (!res.ok) throw new Error(await readError(res));
            if (!res.body) return res.json();

            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            let fullText = ''; // everything decoded so far, for the old-shape fallback
            let sawEventLine = false;

            // The dialog is unclosable while a publish is pending (the stream
            // can't be resumed), so a silently-dead socket must surface as an
            // error rather than spin forever. The backend writes a line at
            // least once per stage/file — minutes of silence means it's gone.
            const STALL_MS = 120_000;
            const read = async (): Promise<ReadableStreamReadResult<Uint8Array>> => {
                let timer: ReturnType<typeof setTimeout> | undefined;
                const stalled = new Promise<never>((_, reject) => {
                    timer = setTimeout(() => {
                        reader.cancel().catch(() => {});
                        reject(
                            new Error(
                                'The publish stream stalled — check GitHub for a partial repo before retrying',
                            ),
                        );
                    }, STALL_MS);
                });
                try {
                    return await Promise.race([reader.read(), stalled]);
                } finally {
                    clearTimeout(timer);
                }
            };

            // Old backend shape: not NDJSON — drain the rest of the stream and
            // parse the whole body as a single GithubPublishResult.
            const parseWholeBody = async (): Promise<GithubPublishResult> => {
                for (;;) {
                    const { done, value } = await read();
                    if (value) fullText += decoder.decode(value, { stream: true });
                    if (done) break;
                }
                return JSON.parse(fullText + decoder.decode());
            };

            for (;;) {
                const { done, value } = await read();
                if (value) {
                    const chunk = decoder.decode(value, { stream: true });
                    buffer += chunk;
                    fullText += chunk;
                }
                const lines = buffer.split('\n');
                // Mid-stream, the last piece may be a partial line — keep it
                // buffered; once the stream ends it is the final line.
                buffer = done ? '' : (lines.pop() ?? '');
                for (const raw of lines) {
                    const line = raw.trim();
                    if (!line) continue;
                    let parsed: GithubPublishStreamLine;
                    try {
                        parsed = JSON.parse(line);
                    } catch {
                        if (sawEventLine) throw new Error('Publish stream returned a malformed line');
                        return parseWholeBody();
                    }
                    if (!parsed.event && !sawEventLine) return parseWholeBody();
                    sawEventLine = true;
                    if (parsed.event === 'progress') {
                        onProgress?.(parsed);
                    } else if (parsed.event === 'result') {
                        return parsed;
                    }
                }
                if (done) throw new Error('Publish stream ended unexpectedly');
            }
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
