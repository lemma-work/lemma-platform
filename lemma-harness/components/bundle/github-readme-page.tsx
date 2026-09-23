/* eslint-disable @next/next/no-img-element */
'use client';

import { useEffect, useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { ExternalLink, Github } from '@/components/ui/icons';
import {
    extractReadmePresentation,
    fetchPublicGitHubReadme,
    resolveReadmeAssetUrl,
    resolveReadmeLinkUrl,
    type PublicGitHubReadme,
} from '@/lib/github/public-repository';
import { StepLoader } from '@/components/brand/loader';

type ReadmeState =
    | { status: 'loading' }
    | { status: 'ready'; value: PublicGitHubReadme }
    | { status: 'error' };

export function GitHubReadmePage({
    owner,
    repo,
    initialReadme,
}: {
    owner: string;
    repo: string;
    initialReadme?: PublicGitHubReadme | null;
}) {
    const [state, setState] = useState<ReadmeState>(() =>
        initialReadme
            ? { status: 'ready', value: initialReadme }
            : { status: 'loading' },
    );

    useEffect(() => {
        if (initialReadme) return;

        let cancelled = false;

        void fetchPublicGitHubReadme(owner, repo).then((value) => {
            if (cancelled) return;
            setState(value ? { status: 'ready', value } : { status: 'error' });
        });

        return () => {
            cancelled = true;
        };
    }, [initialReadme, owner, repo]);

    if (state.status === 'loading') {
        return (
            <div className="github-import-readme-loading">
                <StepLoader size="sm" />
                Loading the README from GitHub…
            </div>
        );
    }

    if (state.status === 'error') {
        return (
            <div className="github-import-readme-error">
                <Github className="h-6 w-6" />
                <div>
                    <h1>{repo}</h1>
                    <p>The repository README could not be loaded. You can still review and install the pod.</p>
                </div>
            </div>
        );
    }

    return <ReadmeDocument owner={owner} repo={repo} readme={state.value} />;
}

function ReadmeDocument({
    owner,
    repo,
    readme,
}: {
    owner: string;
    repo: string;
    readme: PublicGitHubReadme;
}) {
    const presentation = useMemo(
        () => extractReadmePresentation(readme.markdown, repo),
        [readme.markdown, repo],
    );
    const repositoryUrl = readme.repository?.html_url || `https://github.com/${owner}/${repo}`;
    const sourceLabel = readme.repository?.full_name || `${owner}/${repo}`;
    const coverUrl = presentation.coverImage
        ? resolveReadmeAssetUrl(presentation.coverImage, owner, repo, readme.branch)
        : null;

    return (
        <article className="github-import-repository">
            <header className="github-import-repo-summary">
                <div className="github-import-kicker">
                    <Github className="h-4 w-4" />
                    Public GitHub repository
                </div>
                <h1 className="github-import-title">{presentation.title}</h1>
                {presentation.intro ? (
                    <p className="github-import-intro">{presentation.intro}</p>
                ) : readme.repository?.description ? (
                    <p className="github-import-intro">{readme.repository.description}</p>
                ) : null}

                <a
                    href={repositoryUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="github-import-source-pill"
                >
                    <img
                        src={`https://github.com/${owner}.png?size=48`}
                        alt=""
                        className="github-import-owner-avatar"
                    />
                    <span>{sourceLabel}</span>
                    <ExternalLink className="h-3.5 w-3.5" />
                </a>
            </header>

            <section className="github-import-readme-card" aria-label="Repository README">
                <div className="github-import-readme-rule">
                    <span>README</span>
                    {/* `HEAD` is what we ask GitHub for, not a branch anyone
                        named. Printing it in the slot where a reader expects
                        `main` says nothing true. */}
                    <span>{readme.branch === 'HEAD' ? '' : readme.branch}</span>
                </div>

                {coverUrl ? (
                    // A README that declared a width meant it, in both
                    // directions: a 300px phone screenshot stretched across the
                    // column is the artwork the author was avoiding, and a
                    // banner marked `100%` is one that wants the whole column.
                    // Said nothing: cap rather than fill.
                    <div
                        className="github-import-cover"
                        /* eslint-disable-next-line no-restricted-syntax -- The cap comes from the README author, so it is runtime geometry. */
                        style={{ maxWidth: presentation.coverMaxWidth ?? '640px' }}
                    >
                        <img src={coverUrl} alt="" />
                    </div>
                ) : null}

                <div className="github-import-markdown">
                    <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{
                            h1: ({ children }) => <h1>{children}</h1>,
                            h2: ({ children }) => <h2>{children}</h2>,
                            h3: ({ children }) => <h3>{children}</h3>,
                            p: ({ children }) => <p>{children}</p>,
                            ul: ({ children }) => <ul>{children}</ul>,
                            ol: ({ children }) => <ol>{children}</ol>,
                            li: ({ children }) => <li>{children}</li>,
                            a: ({ href = '', children }) => (
                                <a
                                    href={resolveReadmeLinkUrl(href, owner, repo, readme.branch)}
                                    target={href.startsWith('#') ? undefined : '_blank'}
                                    rel={href.startsWith('#') ? undefined : 'noreferrer'}
                                >
                                    {children}
                                </a>
                            ),
                            img: ({ src = '', alt = '' }) => (
                                <img
                                    src={
                                        typeof src === 'string'
                                            ? resolveReadmeAssetUrl(src, owner, repo, readme.branch)
                                            : ''
                                    }
                                    alt={alt}
                                />
                            ),
                            blockquote: ({ children }) => <blockquote>{children}</blockquote>,
                            code: ({ children }) => <code>{children}</code>,
                            pre: ({ children }) => <pre>{children}</pre>,
                            table: ({ children }) => (
                                <div className="github-import-table-wrap">
                                    <table>{children}</table>
                                </div>
                            ),
                        }}
                    >
                        {presentation.body}
                    </ReactMarkdown>
                </div>
            </section>
        </article>
    );
}
