'use client';

import type { ReactNode } from 'react';
import { useQueries } from '@tanstack/react-query';
import { AlertTriangle, Sparkles } from '@/components/ui/icons';

import { Skeleton } from '@/components/shared/loading';
import { SKILL_MANIFEST_NAME, readSkillManifest, skillManifestPath } from '@/lib/files/skills';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import type { DatastoreFile } from '@/lib/types';

/**
 * A skill row leads with the description, not the filename.
 *
 * In the folder listing a skill is `weekly-report/`, which says nothing — the
 * description is the whole basis on which an agent decides to load it, so it is
 * the one thing worth reading down the list. That costs one fetch per skill,
 * which is why this renderer is scoped to `/skills` and nothing else.
 */
export function SkillEntriesList({
    podId,
    folders,
    onOpenSkill,
    renderActions,
}: {
    podId: string;
    folders: DatastoreFile[];
    onOpenSkill: (skillName: string) => void;
    renderActions?: (entry: DatastoreFile) => ReactNode;
}) {
    const manifests = useQueries({
        queries: folders.map((folder) => ({
            queryKey: ['skill-manifest', podId, folder.path || folder.id],
            queryFn: async () => {
                const blob = await getLemmaClient(podId).files.download(skillManifestPath(folder.name));
                return blob.text();
            },
            staleTime: 60_000,
            retry: false,
        })),
    });

    return (
        <>
            {folders.map((folder, index) => {
                const query = manifests[index];
                const manifest = query?.data
                    ? readSkillManifest(query.data, folder.name)
                    : null;
                const problem = query?.isError
                    ? `No ${SKILL_MANIFEST_NAME} in this folder — an agent cannot load it`
                    : manifest?.problem ?? null;

                return (
                    <div
                        key={folder.id}
                        className="surface-list-row custom-focus-ring group min-h-[3.25rem] min-w-0 gap-2 px-3 py-2 text-left text-sm"
                    >
                        <button
                            type="button"
                            onClick={() => onOpenSkill(folder.name)}
                            className="document-space-entry-button custom-focus-ring flex min-w-0 flex-1 items-start gap-2.5 rounded text-left"
                        >
                            <span className="flex h-5 w-5 shrink-0 items-center justify-center">
                                <Sparkles className="h-4 w-4 text-[var(--text-tertiary)]" aria-hidden />
                            </span>
                            <span className="min-w-0 flex-1">
                                <span className="block truncate text-[var(--text-primary)]">{folder.name}</span>
                                {query?.isPending ? (
                                    <Skeleton className="mt-1.5 h-2.5 w-2/5" />
                                ) : (
                                    <span className="mt-0.5 line-clamp-1 block text-xs leading-5 text-[var(--text-tertiary)]">
                                        {manifest?.description || 'No description — agents have nothing to match on'}
                                    </span>
                                )}
                            </span>
                            {problem ? (
                                <span
                                    className="chip chip-sm chip-pill state-badge-warning mt-0.5 hidden shrink-0 items-center gap-1 sm:inline-flex"
                                    title={problem}
                                >
                                    <AlertTriangle className="h-3 w-3" aria-hidden />
                                    Won&apos;t load
                                </span>
                            ) : null}
                            <span className="mt-0.5 hidden w-14 shrink-0 text-right text-xs text-[var(--text-tertiary)] sm:inline">
                                {folder.updated_at
                                    ? new Date(folder.updated_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
                                    : ''}
                            </span>
                        </button>
                        {renderActions?.(folder)}
                    </div>
                );
            })}
        </>
    );
}
