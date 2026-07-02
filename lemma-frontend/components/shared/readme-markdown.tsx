/* eslint-disable @next/next/no-img-element */
'use client';

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

/** Shared README/markdown renderer — used anywhere we show a repo's README
 * (recipe detail pages, pod export/import). One set of styles so a README
 * looks the same wherever it's read. Sized like GitHub's rendering: compact
 * text, real list bullets, bordered tables, natural-size images — pod READMEs
 * lean hard on tables and badges, so those must read well in a side panel. */
export function ReadmeMarkdown({ markdown }: { markdown: string }) {
    return (
        <div className="max-w-none text-[var(--text-secondary)]">
            <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                    h1: ({ children }) => <h1 className="mb-2.5 text-xl font-semibold leading-snug text-[var(--text-primary)]">{children}</h1>,
                    h2: ({ children }) => <h2 className="mb-2 mt-6 border-b border-[var(--border-subtle)] pb-1.5 text-base font-semibold text-[var(--text-primary)]">{children}</h2>,
                    h3: ({ children }) => <h3 className="mb-1.5 mt-4 text-sm font-semibold text-[var(--text-primary)]">{children}</h3>,
                    p: ({ children }) => <p className="my-2.5 text-sm leading-6 text-[var(--text-secondary)]">{children}</p>,
                    ul: ({ children }) => <ul className="my-2.5 list-disc space-y-1 pl-5 text-sm leading-6 marker:text-[var(--text-tertiary)]">{children}</ul>,
                    ol: ({ children }) => <ol className="my-2.5 list-decimal space-y-1 pl-5 text-sm leading-6 marker:text-[var(--text-tertiary)]">{children}</ol>,
                    li: ({ children }) => <li className="text-[var(--text-secondary)]">{children}</li>,
                    a: ({ href, children }) => (
                        <a href={href} target="_blank" rel="noreferrer" className="font-medium text-[var(--text-primary)] underline decoration-[var(--border-strong)] underline-offset-4">
                            {children}
                        </a>
                    ),
                    strong: ({ children }) => <strong className="font-semibold text-[var(--text-primary)]">{children}</strong>,
                    code: ({ children }) => (
                        <code className="rounded-md bg-[var(--surface-2)] px-1.5 py-0.5 font-mono text-xs text-[var(--text-primary)]">
                            {children}
                        </code>
                    ),
                    pre: ({ children }) => (
                        <pre className="code-surface code-surface-pre my-3 p-4 leading-6">
                            {children}
                        </pre>
                    ),
                    table: ({ children }) => (
                        <div className="my-3 overflow-x-auto rounded-lg border border-[var(--border-subtle)]">
                            <table className="w-full border-collapse text-sm">{children}</table>
                        </div>
                    ),
                    thead: ({ children }) => <thead className="bg-[var(--surface-2)]">{children}</thead>,
                    th: ({ children }) => (
                        <th className="border-b border-[var(--border-subtle)] px-3 py-1.5 text-left text-xs font-semibold text-[var(--text-primary)]">
                            {children}
                        </th>
                    ),
                    td: ({ children }) => (
                        <td className="border-b border-[var(--border-subtle)] px-3 py-1.5 align-top text-sm leading-5 text-[var(--text-secondary)] [tr:last-child>&]:border-b-0">
                            {children}
                        </td>
                    ),
                    hr: () => <hr className="my-5 border-[var(--border-subtle)]" />,
                    img: ({ src, alt }) => (
                        // Natural size, like GitHub renders READMEs: a 28px
                        // shields badge stays badge-sized instead of stretching
                        // into a banner, while large screenshots still cap at
                        // the container width.
                        <img
                            src={src || ''}
                            alt={alt || ''}
                            className="my-2 inline-block h-auto max-w-full"
                        />
                    ),
                    blockquote: ({ children }) => (
                        <blockquote className="my-2.5 border-l-2 border-[var(--border-strong)] pl-3 text-sm leading-6 text-[var(--text-tertiary)] [&_p]:my-1">
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
