'use client';

import type { ReactNode } from 'react';
import { useQueries } from '@tanstack/react-query';
import { AlertTriangle, ChevronRight, Zap } from '@/components/ui/icons';

import { ProductIcon } from '@/components/pod/product-icon';
import { Skeleton } from '@/components/shared/loading';
import {
    SKILL_MANIFEST_NAME,
    readSkillManifest,
    skillManifestPath,
    splitSkillDescription,
} from '@/lib/files/skills';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import type { DatastoreFile } from '@/lib/types';
import { cn } from '@/lib/utils';

/**
 * Skills are cards, not file rows.
 *
 * In the folder listing a skill is `weekly-report/`, which says nothing, so the
 * row led with the description instead — and a description written for a model
 * is sixty words long. Twelve of those stacked as rows is a wall of prose in
 * which every skill looks like every other skill, and the whole shelf reads as
 * a folder of documents rather than as equipment an agent picks up.
 *
 * The card is the index card the rest of the pod already uses for its
 * resources, filled with the three things the description actually contains:
 * what the skill does, when it loads, and whether the runtime will take it.
 * That costs one fetch per skill, which is why this renderer is scoped to
 * `/skills` and nothing else.
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
        <div className="resource-index-grid resource-index-grid-md-2 md:grid-cols-2">
            {folders.map((folder, index) => {
                const query = manifests[index];
                const manifest = query?.data
                    ? readSkillManifest(query.data, folder.name)
                    : null;
                const problem = query?.isError
                    ? `No ${SKILL_MANIFEST_NAME} in this folder — an agent cannot load it`
                    : manifest?.problem ?? null;
                const { summary, trigger } = splitSkillDescription(manifest?.description || '');

                return (
                    <article
                        key={folder.id}
                        className="resource-index-card group relative min-h-[9rem] p-4"
                        title={manifest?.description || undefined}
                    >
                        {/* One control, the whole card — and therefore spans
                            rather than paragraphs: a button may only contain
                            phrasing content, which is what broke the old row's
                            clamp. `line-clamp` brings its own `display`, so
                            nothing here adds `block` on top of it. */}
                        <button
                            type="button"
                            onClick={() => onOpenSkill(folder.name)}
                            className="custom-focus-ring flex min-w-0 flex-1 flex-col rounded-md text-left"
                        >
                            {/* Glyph and name on one line. A skill card carries
                                more text than a workflow card and no status of
                                its own, so the row the other ledgers spend on an
                                icon is a row this one spends on the description. */}
                            <span className="flex min-w-0 items-center gap-2 pr-7">
                                <ProductIcon kind="skills" size="lg" />
                                <span className="resource-index-card-title truncate font-display text-base font-medium text-[var(--text-primary)]">
                                    {folder.name}
                                </span>
                            </span>

                            {query?.isPending ? (
                                <span className="mt-2 flex min-h-10 flex-col gap-1.5 pt-1">
                                    <Skeleton className="h-2.5 w-full" />
                                    <Skeleton className="h-2.5 w-3/5" />
                                </span>
                            ) : (
                                <span
                                    className={cn(
                                        'resource-index-card-summary mt-2 min-h-10 text-[var(--text-secondary)]',
                                        // Without a trigger clause the card has one
                                        // block of text; let it use the room.
                                        trigger ? 'line-clamp-2' : 'line-clamp-3'
                                    )}
                                >
                                    {summary || 'No description — agents have nothing to match on'}
                                </span>
                            )}

                            {/* When it loads. The clause is the skill's whole
                                interface with the agent, so it gets its own line
                                rather than a place in the paragraph. */}
                            {trigger ? (
                                <span className="mt-2.5 flex gap-1.5 border-t border-[var(--border-subtle)] pt-2.5 text-xs leading-5 text-[var(--text-tertiary)]">
                                    <Zap className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                                    <span className="line-clamp-2">{trigger}</span>
                                </span>
                            ) : null}

                            <span className="mt-auto flex items-center justify-between gap-2 pt-3 text-xs text-[var(--text-tertiary)]">
                                {problem ? (
                                    <span
                                        className="chip chip-sm chip-pill state-badge-warning inline-flex min-w-0 items-center gap-1"
                                        title={problem}
                                    >
                                        <AlertTriangle className="h-3 w-3 shrink-0" aria-hidden />
                                        Won&apos;t load
                                    </span>
                                ) : (
                                    <span className="truncate">
                                        {folder.updated_at
                                            ? `Updated ${new Date(folder.updated_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`
                                            : 'Skill'}
                                    </span>
                                )}
                                <span className="inline-flex shrink-0 items-center gap-1 font-medium text-[var(--text-secondary)] opacity-0 transition-gentle group-hover:translate-x-0.5 group-hover:opacity-100">
                                    Open
                                    <ChevronRight className="h-3.5 w-3.5" />
                                </span>
                            </span>
                        </button>

                        {/* Outside the button, over its top-right corner: a menu
                            nested in a button is neither valid nor clickable. */}
                        {renderActions ? (
                            <span className="absolute right-3 top-3 z-10">{renderActions(folder)}</span>
                        ) : null}
                    </article>
                );
            })}
        </div>
    );
}
