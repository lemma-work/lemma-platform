import type { Metadata } from 'next';
import Link from 'next/link';

import { Logo } from '@/components/brand/logo';
import { ArrowRight } from '@/components/ui/icons';
import { PUBLIC_TEMPLATES } from '@/lib/templates/catalog';
import { socialCardPath } from '@/lib/share/social-card';

const image = socialCardPath({
    variant: 'run',
    title: 'Templates that actually run.',
    detail: 'Start with the job. Make it yours.',
    label: 'lemma.work/templates',
});

export const metadata: Metadata = {
    title: 'Templates',
    description: 'Start with a complete Lemma setup, then make it yours.',
    alternates: { canonical: '/templates' },
    openGraph: {
        title: 'Templates that actually run.',
        description: 'Start with the job. Make it yours.',
        images: [{ url: image, width: 1200, height: 630, alt: 'Lemma templates' }],
    },
    twitter: {
        card: 'summary_large_image',
        title: 'Templates that actually run.',
        description: 'Start with the job. Make it yours.',
        images: [image],
    },
};

export default function TemplatesPage() {
    return (
        <main className="min-h-screen bg-[var(--surface-0)] text-[var(--text-primary)]">
            <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-6">
                <Link href="/" aria-label="Lemma home">
                    <Logo size="sm" variant="mark-wordmark" />
                </Link>
                <Link
                    href="/auth"
                    className="rounded-full bg-[var(--action-primary)] px-4 py-2 text-sm font-medium text-[var(--text-on-brand)]"
                >
                    Start building
                </Link>
            </header>

            <section className="mx-auto max-w-6xl px-6 pb-24 pt-20 sm:pt-28">
                <p className="type-eyebrow-mono text-[var(--text-tertiary)]">Run it on Lemma</p>
                <h1 className="mt-5 max-w-3xl text-5xl font-semibold leading-none tracking-tight sm:text-7xl">
                    Start with the job.
                    <span className="block text-[var(--text-secondary)]">Make it yours.</span>
                </h1>
                <p className="mt-6 max-w-xl text-lg leading-8 text-[var(--text-secondary)]">
                    Complete starting points for apps, agents, workflows, and data—ready to shape around your team.
                </p>

                <div className="mt-16 grid gap-5 sm:grid-cols-2">
                    {PUBLIC_TEMPLATES.length === 0 ? (
                        <div className="surface-panel p-7 sm:col-span-2 sm:p-9">
                            <p className="type-eyebrow-mono text-[var(--text-tertiary)]">Coming soon</p>
                            <h2 className="mt-6 text-3xl font-semibold tracking-[-0.035em]">
                                Verified templates are being prepared.
                            </h2>
                            <p className="mt-3 max-w-xl leading-7 text-[var(--text-secondary)]">
                                Every template listed here will be backed by a public, end-to-end tested GitHub
                                bundle.
                            </p>
                        </div>
                    ) : PUBLIC_TEMPLATES.map((template) => (
                        <Link
                            key={template.slug}
                            href={`/templates/${template.slug}`}
                            className="surface-panel group p-7 transition-transform hover:-translate-y-1 sm:p-9"
                        >
                            <p className="type-eyebrow-mono text-[var(--text-tertiary)]">{template.kicker}</p>
                            <h2 className="mt-12 text-3xl font-semibold tracking-[-0.035em]">{template.name}</h2>
                            <p className="mt-3 max-w-lg leading-7 text-[var(--text-secondary)]">{template.description}</p>
                            <span className="mt-8 inline-flex items-center gap-2 text-sm font-medium">
                                See template
                                <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
                            </span>
                        </Link>
                    ))}
                </div>
            </section>
        </main>
    );
}
