import type { Metadata } from 'next';
import Image from 'next/image';
import Link from 'next/link';

import { Logo } from '@/components/brand/logo';
import { ArrowRight, Check, Github } from '@/components/ui/icons';
import { copyrightNotice } from '@/lib/company';
import {
    PUBLIC_TEMPLATES,
    templateCoverPath,
    templateRunHref,
} from '@/lib/templates/catalog';
import { socialCardPath } from '@/lib/share/social-card';

const image = socialCardPath({
    variant: 'run',
    title: 'Working software, ready to install.',
    detail: 'Ten open-source pods. Review the plan, then make one yours.',
    label: 'lemma.work/templates',
});

export const metadata: Metadata = {
    title: 'Templates',
    description: 'Install a complete open-source Lemma pod, then remix every part.',
    alternates: { canonical: '/templates' },
    openGraph: {
        title: 'Working software, ready to install.',
        description: 'Ten open-source pods. Review the plan, then make one yours.',
        images: [{ url: image, width: 1200, height: 630, alt: 'Lemma templates' }],
    },
    twitter: {
        card: 'summary_large_image',
        title: 'Working software, ready to install.',
        description: 'Ten open-source pods. Review the plan, then make one yours.',
        images: [image],
    },
};

export default function TemplatesPage() {
    return (
        <main className="github-import-page templates-gallery-page">
            <header className="github-import-header">
                <div className="github-import-header-inner">
                    <Link href="/" aria-label="Lemma home">
                        <Logo size="sm" variant="mark-wordmark" />
                    </Link>
                    <nav className="templates-gallery-nav" aria-label="Template navigation">
                        <Link href="/">Home</Link>
                        <Link href="/docs">Docs</Link>
                        <Link href="/auth" className="templates-gallery-nav-cta">
                            Start building
                            <ArrowRight className="h-3.5 w-3.5" />
                        </Link>
                    </nav>
                </div>
            </header>

            <div className="templates-gallery-shell">
                <section className="templates-gallery-hero" aria-labelledby="templates-title">
                    <div className="templates-gallery-hero-copy">
                        <p className="templates-gallery-eyebrow">
                            <Github className="h-4 w-4" />
                            10 open-source pods
                        </p>
                        <h1 id="templates-title">Templates for real work.</h1>
                        <p className="templates-gallery-hero-description">
                            Complete software with the interface and operating system behind it.
                            Pick a job, review the source, and install it into your pod.
                        </p>
                    </div>
                </section>

                <section className="templates-gallery-collection" id="all-templates">
                    <div className="templates-gallery-grid">
                        {PUBLIC_TEMPLATES.map((template, index) => (
                            <Link
                                className="templates-gallery-card"
                                href={templateRunHref(template)}
                                key={template.slug}
                            >
                                <span className="templates-gallery-card-art">
                                    <Image
                                        alt=""
                                        fill
                                        sizes="(max-width: 640px) 100vw, (max-width: 1040px) 50vw, 300px"
                                        src={templateCoverPath(template)}
                                        unoptimized
                                    />
                                    <span>{String(index + 1).padStart(2, '0')}</span>
                                </span>
                                <span className="templates-gallery-card-copy">
                                    <span className="templates-gallery-card-meta">
                                        <span>{template.category}</span>
                                        <span>Open source pod</span>
                                    </span>
                                    <strong>{template.name}</strong>
                                    <span className="templates-gallery-card-description">
                                        {template.description}
                                    </span>
                                    <span className="templates-gallery-card-action">
                                        Review and install
                                        <ArrowRight className="h-4 w-4" />
                                    </span>
                                </span>
                            </Link>
                        ))}
                    </div>
                </section>

                <section className="templates-gallery-install">
                    <div className="templates-gallery-install-copy">
                        <p className="templates-gallery-eyebrow">Transparent by default</p>
                        <h2>Review before install.</h2>
                        <p>
                            Every card goes straight to Lemma’s installer. It reads the repository,
                            shows what will be added, and asks where it should go.
                        </p>
                        <ol>
                            <li>
                                <Check className="h-3.5 w-3.5" />
                                <span><strong>Source</strong> GitHub repository</span>
                            </li>
                            <li>
                                <Check className="h-3.5 w-3.5" />
                                <span><strong>Review</strong> apps, agents, workflows, data</span>
                            </li>
                            <li>
                                <Check className="h-3.5 w-3.5" />
                                <span><strong>Install</strong> new or existing pod</span>
                            </li>
                        </ol>
                    </div>
                </section>

                <footer className="templates-gallery-footer">
                    <Logo size="sm" variant="mark-wordmark" />
                    <p>Open-source starting points for software that becomes yours.</p>
                    <nav aria-label="Footer navigation">
                        <Link href="/docs">Docs</Link>
                        <a href="https://github.com/lemma-work/lemma-platform">GitHub</a>
                    </nav>
                    <p className="templates-gallery-footer-legal">{copyrightNotice()}</p>
                </footer>
            </div>
        </main>
    );
}
