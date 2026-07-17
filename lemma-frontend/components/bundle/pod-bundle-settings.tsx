'use client';

import { useState } from 'react';
import { ArrowUpRight, Github, PackageOpen, Share2, Upload } from '@/components/ui/icons';
import type { PodRecipe } from 'lemma-sdk';

import { PodSettingsPanel } from '@/components/pod/pod-settings-shell';
import { Button } from '@/components/ui/button';
import { ShareSheet } from '@/components/bundle/share-sheet';
import { ImportDialog } from '@/components/bundle/import-dialog';

interface PodBundleSettingsPanelProps {
    podId: string;
    podName?: string | null;
    canUpdate: boolean;
    recipes: PodRecipe[];
}

function formatImportedAt(value: string): string {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

function RecipeRow({ recipe }: { recipe: PodRecipe }) {
    const when = formatImportedAt(recipe.imported_at);
    const isGithub = recipe.kind === 'github' && recipe.repo_url;

    return (
        <div className="flex items-center gap-2.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-1)] px-3 py-2">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-[var(--surface-2)]">
                {isGithub ? (
                    <Github className="h-4 w-4 text-[var(--text-secondary)]" />
                ) : (
                    <PackageOpen className="h-4 w-4 text-[var(--text-secondary)]" />
                )}
            </span>
            <div className="min-w-0 flex-1">
                {isGithub ? (
                    <a
                        href={recipe.repo_url ?? '#'}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-1 truncate text-sm font-medium text-[var(--action-primary)] hover:underline"
                    >
                        {recipe.repo_url?.replace(/^https?:\/\//, '') ?? recipe.name}
                        <ArrowUpRight className="h-3.5 w-3.5 shrink-0" />
                    </a>
                ) : (
                    <div className="truncate text-sm font-medium text-[var(--text-primary)]">
                        {recipe.name || 'Uploaded bundle'}
                    </div>
                )}
                <div className="text-xs text-[var(--text-tertiary)]">
                    {recipe.kind === 'github' ? 'From GitHub' : 'Uploaded'}
                    {when ? ` · ${when}` : ''}
                </div>
            </div>
        </div>
    );
}

/**
 * The full "Share & bundles" home in pod Settings — export/publish, install a
 * bundle, and the pod's provenance (which bundles were imported into it).
 */
export function PodBundleSettingsPanel({ podId, podName, canUpdate, recipes }: PodBundleSettingsPanelProps) {
    const [shareOpen, setShareOpen] = useState(false);
    const [importOpen, setImportOpen] = useState(false);

    return (
        <PodSettingsPanel
            title="Share & bundles"
            description="Export this pod as a portable bundle, publish it to GitHub, or install another bundle into it."
        >
            {recipes.length > 0 ? (
                <div className="mb-4 space-y-2">
                    <p className="text-xs font-medium uppercase tracking-wide text-[var(--text-tertiary)]">
                        Provenance
                    </p>
                    {recipes.map((recipe, index) => (
                        <RecipeRow key={`${recipe.kind}-${recipe.imported_at}-${index}`} recipe={recipe} />
                    ))}
                </div>
            ) : null}

            <div className="flex flex-wrap gap-2">
                <Button variant="secondary" onClick={() => setShareOpen(true)}>
                    <Share2 className="mr-2 h-4 w-4" />
                    Share / export
                </Button>
                {canUpdate ? (
                    <Button variant="secondary" onClick={() => setImportOpen(true)}>
                        <Upload className="mr-2 h-4 w-4" />
                        Install a bundle
                    </Button>
                ) : null}
            </div>

            <ShareSheet podId={podId} podName={podName} open={shareOpen} onOpenChange={setShareOpen} />
            {canUpdate ? (
                <ImportDialog
                    podId={podId}
                    podName={podName}
                    open={importOpen}
                    onOpenChange={setImportOpen}
                />
            ) : null}
        </PodSettingsPanel>
    );
}
