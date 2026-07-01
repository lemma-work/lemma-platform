/* eslint-disable @next/next/no-img-element */
'use client';

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

/** Shared README/markdown renderer — used anywhere we show a repo's README
 * (recipe detail pages, pod export/import). One set of styles so a README
 * looks the same wherever it's read. */
export function ReadmeMarkdown({ markdown }: { markdown: string }) {
    return (
        <div className="max-w-none text-[var(--text-secondary)]">
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
