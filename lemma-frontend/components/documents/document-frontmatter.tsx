'use client';

import { useMemo } from 'react';
import { AlertTriangle, Sparkles } from '@/components/ui/icons';

import { setFrontmatterField, splitFrontmatter } from '@/lib/files/frontmatter';
import { isSkillManifestPath, readSkillManifest, skillNameFromPath } from '@/lib/files/skills';
import { cn } from '@/lib/utils';

/**
 * The header a markdown file's frontmatter deserves.
 *
 * Under `/skills` the block is a contract — a name the runtime matches against
 * the folder, and a description that decides whether an agent ever reaches for
 * the skill — so it gets fields, not paragraphs. Elsewhere frontmatter is
 * metadata someone tucked at the top, and a quiet row of pairs is enough.
 */

type DocumentFrontmatterProps = {
    /** The whole file, frontmatter included. */
    content: string;
    path: string;
    editable: boolean;
    onChange: (nextContent: string) => void;
};

export function DocumentFrontmatter({ content, path, editable, onChange }: DocumentFrontmatterProps) {
    if (isSkillManifestPath(path)) {
        return <SkillHeader content={content} path={path} editable={editable} onChange={onChange} />;
    }

    const { raw, fields } = splitFrontmatter(content);
    if (!raw) return null;

    const entries = Object.entries(fields).filter(([, value]) => value);
    if (entries.length === 0) return null;

    return (
        <dl className="mb-5 flex flex-wrap gap-x-5 gap-y-1.5 border-b border-[var(--border-subtle)] pb-4 text-xs">
            {entries.map(([key, value]) => (
                <div key={key} className="flex min-w-0 max-w-full gap-1.5">
                    <dt className="shrink-0 text-[var(--text-tertiary)]">{key}</dt>
                    <dd className="min-w-0 truncate text-[var(--text-secondary)]">{value}</dd>
                </div>
            ))}
        </dl>
    );
}

function SkillHeader({ content, path, editable, onChange }: DocumentFrontmatterProps) {
    const folderName = skillNameFromPath(path) || '';
    const manifest = useMemo(() => readSkillManifest(content, folderName), [content, folderName]);
    const { raw, body } = useMemo(() => splitFrontmatter(content), [content]);

    const writeField = (key: string, value: string) => {
        const nextRaw = setFrontmatterField(raw, key, value);
        onChange(`${nextRaw}\n\n${body}`);
    };

    return (
        <div className="mb-7 border-b border-[var(--border-subtle)] pb-5">
            <div className="flex items-center gap-2">
                <Sparkles className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" aria-hidden />
                <span className="type-eyebrow-sm text-[var(--text-tertiary)]">Skill</span>
                <span className="truncate text-xs text-[var(--text-tertiary)]">{manifest.name}</span>
            </div>

            {/* The description is the skill's opening line, so it is set as one —
                no border, no field background, sized like the prose it precedes.
                A box here made the one sentence that decides whether an agent
                ever loads this skill look like a settings input.

                Uncontrolled and keyed to the file: writing the value back through
                the frontmatter serializer collapses whitespace, so a controlled
                field would eat the space you just typed. */}
            <textarea
                key={path}
                ref={fitToContent}
                aria-label="Skill description"
                className={cn(
                    'mt-2 block w-full resize-none overflow-hidden border-0 bg-transparent p-0 outline-none',
                    'text-sm leading-relaxed text-[var(--text-secondary)]',
                    'placeholder:text-[var(--text-soft)] disabled:cursor-text disabled:opacity-100'
                )}
                rows={1}
                defaultValue={manifest.description}
                placeholder="When should an agent load this skill? This sentence is all it sees before deciding."
                disabled={!editable}
                spellCheck
                onChange={(event) => {
                    fitToContent(event.currentTarget);
                    writeField('description', event.target.value);
                }}
            />

            {manifest.problem ? (
                <SkillProblem
                    problem={manifest.problem}
                    onFix={editable && folderName ? () => writeField('name', folderName) : undefined}
                />
            ) : null}
        </div>
    );
}

/**
 * Grow with the text instead of scrolling inside a fixed box — a scrollbar in a
 * two-line subtitle is the boxiness the border removal was meant to shed.
 * Written straight to the node so no render depends on a measured height.
 */
function fitToContent(node: HTMLTextAreaElement | null) {
    if (!node) return;
    node.style.height = 'auto';
    node.style.height = `${node.scrollHeight}px`;
}

function SkillProblem({ problem, onFix }: { problem: string; onFix?: () => void }) {
    return (
        <div className="mt-3 flex items-start gap-2 rounded-md px-3 py-2 text-xs state-surface-warning">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
            <div className="min-w-0 flex-1">
                <p>{problem}</p>
                {onFix ? (
                    <button
                        type="button"
                        className="mt-1 underline underline-offset-2 hover:no-underline"
                        onClick={onFix}
                    >
                        Use the folder name
                    </button>
                ) : null}
            </div>
        </div>
    );
}
