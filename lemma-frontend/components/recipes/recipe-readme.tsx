'use client';

import { useEffect, useState } from 'react';
import { ExternalLink, Loader2 } from 'lucide-react';

import { ReadmeMarkdown } from '@/components/shared/readme-markdown';
import {
    absolutizeReadmeAssetUrls,
    getReadmeRawCandidates,
    type KitDefinition,
} from '@/lib/kits/catalog';

type ReadmeState =
    | { status: 'loading' }
    | { status: 'error'; message: string }
    | { status: 'ready'; markdown: string; branch: string };

// Renders the source README for a repo-backed recipe. Lifted from the kit
// landing page so prompt and repo recipes can share one detail page.
export function RecipeReadme({ kit }: { kit: KitDefinition }) {
    const [readmeState, setReadmeState] = useState<ReadmeState>({ status: 'loading' });

    useEffect(() => {
        let cancelled = false;

        async function loadReadme() {
            setReadmeState({ status: 'loading' });
            const candidates = getReadmeRawCandidates(kit);
            if (candidates.length === 0) {
                setReadmeState({ status: 'error', message: 'This recipe does not point at a valid source.' });
                return;
            }

            for (const candidate of candidates) {
                try {
                    const response = await fetch(candidate.url, { cache: 'no-store' });
                    if (!response.ok) continue;
                    const rawMarkdown = await response.text();
                    if (!cancelled) {
                        setReadmeState({
                            status: 'ready',
                            branch: candidate.branch,
                            markdown: absolutizeReadmeAssetUrls(rawMarkdown, kit, candidate.branch),
                        });
                    }
                    return;
                } catch {
                    // Try the next likely default branch.
                }
            }

            if (!cancelled) {
                setReadmeState({ status: 'error', message: 'Could not load README.md from this recipe source.' });
            }
        }

        void loadReadme();
        return () => {
            cancelled = true;
        };
    }, [kit]);

    if (readmeState.status === 'loading') {
        return (
            <div className="flex min-h-72 items-center justify-center gap-2 text-sm text-[var(--text-secondary)]">
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading README...
            </div>
        );
    }

    if (readmeState.status === 'error') {
        return (
            <div className="surface-panel-dashed p-5">
                <h2 className="text-sm font-semibold text-[var(--text-primary)]">README unavailable</h2>
                <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">{readmeState.message}</p>
                <a href={kit.github} target="_blank" rel="noreferrer" className="mt-4 inline-flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]">
                    Open source
                    <ExternalLink className="h-4 w-4" />
                </a>
            </div>
        );
    }

    return (
        <div>
            <div className="mb-5 flex flex-col gap-2 border-b border-[var(--border-subtle)] pb-4 sm:flex-row sm:items-center sm:justify-between">
                <div>
                    <p className="type-eyebrow-mono">README</p>
                    <h2 className="mt-1 text-lg font-semibold text-[var(--text-primary)]">What this recipe sets up</h2>
                </div>
                <span className="rounded-md border border-[color:var(--chip-border)] bg-[var(--chip-bg)] px-2 py-1 font-mono text-xs text-[var(--chip-fg)]">
                    branch: {readmeState.branch}
                </span>
            </div>
            <ReadmeMarkdown markdown={readmeState.markdown} />
        </div>
    );
}
