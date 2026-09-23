import type { Metadata } from 'next';
import { Map } from '@/components/ui/icons';

import { DocsShell } from '@/components/docs/docs-shell';
import { HowLemmaWorksMap } from '@/components/docs/how-lemma-works-map';

export const metadata: Metadata = {
    title: 'How Lemma works | Lemma Docs',
    description:
        'The whole system on one screen: work arrives through surfaces, agents and workflows move it over tables and files, people steer from apps and approvals.',
};

export default function HowLemmaWorksPage() {
    return (
        <DocsShell activeSlug="how-lemma-works">
            <article className="docs-page-article mx-auto w-full min-w-0 max-w-[960px] px-5 py-8 md:px-8 md:py-11">
                <header>
                    <div className="flex items-center gap-2 text-[var(--delight)]">
                        <span className="grid h-7 w-7 flex-none place-items-center rounded-md border border-[var(--border-subtle)] bg-[var(--surface-1)]">
                            <Map className="h-3.5 w-3.5" />
                        </span>
                        <p className="text-xs font-semibold uppercase tracking-normal">Concept</p>
                    </div>
                    <h1 className="docs-page-title mt-3 text-3xl font-semibold leading-tight text-[var(--text-primary)] md:text-4xl">
                        How Lemma works
                    </h1>
                    <p className="mt-3 max-w-2xl text-base leading-7 text-[var(--text-secondary)]">
                        Work arrives through surfaces. Inside the pod, agents handle judgment, functions handle
                        rules, and workflows chain them with human approvals — all reading and writing tables and
                        files. People watch and steer from apps and conversations. Hover any piece to see what it
                        does; click through to it in your own pod.
                    </p>
                </header>

                <div className="mt-9">
                    <HowLemmaWorksMap />
                </div>
            </article>
        </DocsShell>
    );
}
