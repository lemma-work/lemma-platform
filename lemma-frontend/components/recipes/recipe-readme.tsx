/* eslint-disable @next/next/no-img-element */
'use client';

import { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ExternalLink, Loader2 } from '@/components/ui/icons';

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

function ReadmeMarkdown({ markdown }: { markdown: string }) {
    return (
        <div className="kit-readme max-w-none text-[var(--text-secondary)]">
            <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                    h1: ({ children }) => <h1 className="mb-4 text-2xl font-semibold text-[var(--text-primary)]">{children}</h1>,
                    h2: ({ children }) => <h2 className="mb-3 mt-8 text-lg font-semibold text-[var(--text-primary)]">{children}</h2>,
                    h3: ({ children }) => <h3 className="mb-2 mt-5 text-base font-semibold text-[var(--text-primary)]">{children}</h3>,
                    p: ({ children }) => <p className="my-3 text-sm leading-7 text-[var(--text-secondary)]">{children}</p>,
                    ul: ({ children }) => <ul className="my-3 space-y-2 pl-5 text-sm leading-6">{children}</ul>,
                    ol: ({ children }) => <ol className="my-3 list-decimal space-y-2 pl-5 text-sm leading-6">{children}</ol>,
                    li: ({ children }) => <li className="pl-1 text-[var(--text-secondary)]">{children}</li>,
                    a: ({ href, children }) => (
                        <a href={href} target="_blank" rel="noreferrer" className="font-medium text-[var(--text-primary)] underline decoration-[var(--border-strong)] underline-offset-4">
                            {children}
                        </a>
                    ),
                    code: ({ children }) => (
                        <code className="rounded-md bg-[var(--surface-2)] px-1.5 py-0.5 font-mono text-xs text-[var(--text-primary)]">
                            {children}
                        </code>
                    ),
                    pre: ({ children }) => (
                        <pre className="code-surface code-surface-pre my-4 p-4 leading-6">
                            {children}
                        </pre>
                    ),
                    img: ({ src, alt }) => (
                        <img
                            src={src || ''}
                            alt={alt || ''}
                            className="my-5 h-auto max-h-none w-full rounded-lg border border-[var(--border-subtle)] object-contain shadow-[var(--shadow-xs)]"
                        />
                    ),
                    blockquote: ({ children }) => (
                        <blockquote className="my-4 border-l-2 border-[var(--border-strong)] pl-4 text-sm italic text-[var(--text-secondary)]">
                            {children}
                        </blockquote>
                    ),
                }}
            >
                {markdown}
            </ReactMarkdown>
        </div>
    );
}
