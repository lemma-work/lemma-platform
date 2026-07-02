'use client';

import { use, useEffect, useState } from 'react';
import { ExternalLink, Github, Loader2 } from 'lucide-react';

import { ProtectedRoute } from '@/components/auth/protected-route';
import { PlainPageShell } from '@/components/dashboard/plain-page-shell';
import { ImportPodBundleWizard } from '@/components/pod/import-pod-bundle-wizard';
import { ReadmeMarkdown } from '@/components/shared/readme-markdown';
import { fetchGithubReadme } from '@/lib/github/readme';

type ReadmeState =
    | { status: 'loading' }
    | { status: 'unavailable' }
    | { status: 'ready'; markdown: string };

/** The source repo's own README, read straight off GitHub — so you know what
 * you're about to install before you click the button. */
function SourceReadme({ owner, repo }: { owner: string; repo: string }) {
    const [state, setState] = useState<ReadmeState>({ status: 'loading' });

    useEffect(() => {
        let cancelled = false;
        fetchGithubReadme(owner, repo).then((result) => {
            if (cancelled) return;
            setState(result ? { status: 'ready', markdown: result.markdown } : { status: 'unavailable' });
        });
        return () => {
            cancelled = true;
        };
    }, [owner, repo]);

    return (
        <div className="surface-panel overflow-hidden">
            <div className="flex items-center gap-2 border-b border-[var(--border-subtle)] px-5 py-3">
                <Github className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" aria-hidden />
                <span className="min-w-0 truncate text-xs font-medium text-[var(--text-secondary)]">
                    README · {owner}/{repo}
                </span>
            </div>
            {state.status === 'loading' && (
                <div className="flex items-center justify-center gap-2 py-16 text-sm text-[var(--text-tertiary)]">
                    <Loader2 className="h-4 w-4 animate-spin" /> Loading README…
                </div>
            )}
            {state.status === 'unavailable' && (
                <div className="p-6 text-center">
                    <p className="text-sm text-[var(--text-secondary)]">
                        This repo doesn&apos;t have a README to show.
                    </p>
                    <a
                        href={`https://github.com/${owner}/${repo}`}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-2 inline-flex items-center gap-1 text-sm font-medium text-[var(--text-accent)] hover:underline"
                    >
                        View on GitHub <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                </div>
            )}
            {state.status === 'ready' && (
                <div className="p-6">
                    <ReadmeMarkdown markdown={state.markdown} />
                </div>
            )}
        </div>
    );
}

export default function ImportFromGithubPage({
    params,
}: {
    params: Promise<{ owner: string; repo: string }>;
}) {
    return (
        <ProtectedRoute>
            <ImportFromGithubContent params={params} />
        </ProtectedRoute>
    );
}

function ImportFromGithubContent({
    params,
}: {
    params: Promise<{ owner: string; repo: string }>;
}) {
    const { owner, repo } = use(params);

    return (
        <PlainPageShell
            title={`Import ${owner}/${repo}`}
            icon={<Github className="h-4 w-4" />}
            backHref="/"
            backLabel="Home"
            contentWidthClassName="max-w-6xl"
        >
            {/* Install card first (mobile order too), README alongside on large
                screens — the card stays pinned while you scroll the README. */}
            <div className="flex flex-col gap-6 py-6 lg:grid lg:grid-cols-[420px_minmax(0,1fr)] lg:items-start">
                {/* Capped + self-scrolling: a tall card that just sticks would
                    pin its Install button below the fold, unreachable next to
                    a long README. */}
                <div className="lg:sticky lg:top-20 lg:max-h-[calc(100vh-6rem)] lg:overflow-y-auto">
                    <ImportPodBundleWizard source={{ kind: 'github', owner, repo }} fullWidth />
                </div>
                <SourceReadme key={`${owner}/${repo}`} owner={owner} repo={repo} />
            </div>
        </PlainPageShell>
    );
}
