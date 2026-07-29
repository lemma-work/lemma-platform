import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';

import { Logo } from '@/components/brand/logo';
import { ArrowRight } from '@/components/ui/icons';
import {
    getPublicTemplateBySlug,
    PUBLIC_TEMPLATES,
    templateRunHref,
} from '@/lib/templates/catalog';
import { socialCardPath } from '@/lib/share/social-card';

interface TemplatePageProps {
    params: Promise<{ slug: string }>;
}

export function generateStaticParams() {
    return PUBLIC_TEMPLATES.map((template) => ({ slug: template.slug }));
}

export async function generateMetadata({ params }: TemplatePageProps): Promise<Metadata> {
    const { slug } = await params;
    const template = getPublicTemplateBySlug(slug);
    if (!template) return {};

    const image = socialCardPath({
        variant: 'run',
        title: template.name,
        detail: template.kicker,
        label: `lemma.work/templates/${template.slug}`,
    });

    return {
        title: template.name,
        description: template.description,
        alternates: { canonical: `/templates/${template.slug}` },
        openGraph: {
            title: `Run ${template.name} on Lemma.`,
            description: template.description,
            type: 'website',
            images: [{ url: image, width: 1200, height: 630, alt: `Run ${template.name} on Lemma` }],
        },
        twitter: {
            card: 'summary_large_image',
            title: `Run ${template.name} on Lemma.`,
            description: template.description,
            images: [image],
        },
    };
}

export default async function TemplatePage({ params }: TemplatePageProps) {
    const { slug } = await params;
    const template = getPublicTemplateBySlug(slug);
    if (!template) notFound();

    return (
        <main className="min-h-screen bg-[var(--surface-0)] text-[var(--text-primary)]">
            <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-6">
                <Link href="/" aria-label="Lemma home">
                    <Logo size="sm" variant="mark-wordmark" />
                </Link>
                <Link href="/templates" className="text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
                    All templates
                </Link>
            </header>

            <section className="mx-auto grid max-w-6xl gap-14 px-6 pb-24 pt-16 lg:grid-cols-[minmax(0,1.05fr)_minmax(320px,0.75fr)] lg:pt-24">
                <div>
                    <p className="type-eyebrow-mono text-[var(--text-tertiary)]">Run it on Lemma</p>
                    <h1 className="mt-5 text-5xl font-semibold leading-none tracking-tight sm:text-7xl">
                        {template.name}
                    </h1>
                    <p className="mt-6 max-w-2xl text-xl leading-8 text-[var(--text-secondary)]">
                        {template.description}
                    </p>
                    <Link
                        href={templateRunHref(template)}
                        className="mt-9 inline-flex items-center gap-2 rounded-full bg-[var(--action-primary)] px-6 py-3.5 text-sm font-medium text-[var(--text-on-brand)]"
                    >
                        Run this on Lemma
                        <ArrowRight className="h-4 w-4" />
                    </Link>

                    <div className="mt-16 border-t border-[var(--border-subtle)] pt-8">
                        <p className="type-eyebrow-mono text-[var(--text-tertiary)]">What it gives your team</p>
                        <ul className="mt-6 space-y-4">
                            {template.outcomes.map((outcome) => (
                                <li key={outcome} className="flex gap-4 text-base leading-7 text-[var(--text-secondary)]">
                                    <span className="mt-2.5 h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--text-primary)]" />
                                    {outcome}
                                </li>
                            ))}
                        </ul>
                    </div>
                </div>

                <aside className="surface-panel p-7 sm:p-9">
                    <p className="type-eyebrow-mono text-[var(--text-tertiary)]">Inside the template</p>
                    <div className="mt-8 divide-y divide-[var(--border-subtle)]">
                        {template.includes.map((item, index) => (
                            <section key={item.label} className={index === 0 ? 'pb-6' : 'py-6'}>
                                <h2 className="text-base font-medium">{item.label}</h2>
                                <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">{item.detail}</p>
                            </section>
                        ))}
                    </div>
                    <p className="mt-4 text-xs leading-5 text-[var(--text-tertiary)]">
                        Lemma creates this inside your own pod. You can change every part of it with your coding agent.
                    </p>
                </aside>
            </section>
        </main>
    );
}
