import Link from 'next/link';
import { ArrowLeft, ArrowUpRight } from '@/components/ui/icons';
import { Logo } from '@/components/brand/logo';
import { config } from '@/lib/config';
import type { LegalDocument } from '@/lib/data/legal';

type LegalPageProps = {
    document: LegalDocument;
};

const footerLinks = [
    { href: '/privacy', label: 'Privacy Policy' },
    { href: '/tos', label: 'Terms of Service' },
    { href: '/login', label: 'Sign in' },
] as const;

function toSectionId(title: string) {
    return title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
}

export function LegalPage({ document }: LegalPageProps) {
    return (
        <main className="relative min-h-screen overflow-hidden bg-[var(--bg-canvas)] text-[var(--text-primary)]">
            <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10 overflow-hidden">
                <div className="absolute inset-x-0 top-0 h-px bg-[var(--row-border)]" />
            </div>

            <div className="sticky top-0 z-20 border-b border-[var(--row-border)] bg-[color:color-mix(in_srgb,var(--bg-canvas)_84%,transparent)] backdrop-blur-xl">
                <div className="mx-auto flex max-w-[1180px] items-center justify-between gap-4 px-5 py-4 sm:px-8 lg:px-10">
                    <Link href="/" aria-label="Lemma home" className="inline-flex items-center">
                        <Logo size="xs" variant="mark-wordmark" />
                    </Link>
                    <Link
                        href="/"
                        className="inline-flex items-center gap-2 text-sm text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
                    >
                        <ArrowLeft className="h-4 w-4" />
                        <span>Back to home</span>
                    </Link>
                </div>
            </div>

            <section className="px-5 pb-8 pt-12 sm:px-8 sm:pb-10 sm:pt-16 lg:px-10 lg:pt-20">
                <div className="mx-auto max-w-[1180px]">
                    <div className="max-w-[780px]">
                        <p className="font-mono type-eyebrow-medium">
                            Legal
                        </p>
                        <h1 className="mt-5 max-w-[11ch] [font-family:var(--font-landing-serif)] text-5xl font-normal leading-none tracking-normal text-[var(--text-primary)] sm:text-6xl lg:text-7xl">
                            {document.title}
                        </h1>
                        <p className="mt-6 max-w-[48rem] text-base leading-8 text-[var(--text-secondary)] sm:text-lg">
                            {document.description}
                        </p>
                        <div className="mt-6 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm text-[var(--text-tertiary)]">
                            <span>Effective {document.effectiveDate}</span>
                            <span className="hidden h-1 w-1 rounded-full bg-[var(--row-border)] sm:inline-block" />
                            <Link
                                href={`mailto:${config.SUPPORT_EMAIL}`}
                                className="inline-flex items-center gap-1 transition-colors hover:text-[var(--text-primary)]"
                            >
                                <span>{config.SUPPORT_EMAIL}</span>
                                <ArrowUpRight className="h-3.5 w-3.5" />
                            </Link>
                        </div>
                    </div>

                    <div className="mt-10 grid gap-3 sm:grid-cols-3">
                        {document.summary.map((item) => (
                            <div
                                key={item}
                                className="border-t border-[var(--row-border)] pt-3 text-sm leading-7 text-[var(--text-secondary)]"
                            >
                                {item}
                            </div>
                        ))}
                    </div>
                </div>
            </section>

            <section className="px-5 pb-16 sm:px-8 sm:pb-20 lg:px-10">
                <div className="mx-auto grid max-w-[1180px] gap-12 lg:grid-cols-[240px_minmax(0,1fr)] lg:gap-16">
                    <aside className="lg:sticky lg:top-[88px] lg:self-start">
                        <p className="font-mono type-eyebrow-medium">
                            On this page
                        </p>
                        <nav className="mt-5 space-y-3">
                            {document.sections.map((section, index) => (
                                <a
                                    key={section.title}
                                    href={`#${toSectionId(section.title)}`}
                                    className="flex items-baseline gap-3 text-sm leading-6 text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
                                >
                                    <span className="min-w-7 font-mono text-xs text-[var(--text-tertiary)]">
                                        {(index + 1).toString().padStart(2, '0')}
                                    </span>
                                    <span>{section.title}</span>
                                </a>
                            ))}
                        </nav>
                    </aside>

                    <article className="surface-panel overflow-hidden">
                        <div className="divide-y divide-[var(--row-border)]">
                            {document.sections.map((section, index) => (
                                <section
                                    key={section.title}
                                    id={toSectionId(section.title)}
                                    className="scroll-mt-28 px-6 py-8 sm:px-10 sm:py-10"
                                >
                                    <div className="flex flex-col gap-4 md:flex-row md:gap-8">
                                        <div className="md:w-[180px] md:flex-none">
                                            <p className="font-mono type-eyebrow-medium">
                                                {(index + 1).toString().padStart(2, '0')}
                                            </p>
                                            <h2 className="mt-3 [font-family:var(--font-landing-serif)] text-3xl font-normal leading-tight tracking-normal text-[var(--text-primary)] sm:text-4xl">
                                                {section.title}
                                            </h2>
                                        </div>

                                        <div className="min-w-0 flex-1">
                                            {section.body ? (
                                                <p className="text-base leading-8 text-[var(--text-secondary)] sm:text-base">
                                                    {section.body}
                                                </p>
                                            ) : null}

                                            {section.items?.length ? (
                                                <ul className="mt-5 space-y-4 text-base leading-8 text-[var(--text-secondary)] sm:text-base">
                                                    {section.items.map((item) => (
                                                        <li key={item.text} className="border-l border-[var(--row-border)] pl-4">
                                                            <p>{item.text}</p>
                                                            {item.children?.length ? (
                                                                <ul className="mt-3 space-y-2 text-sm leading-7 text-[var(--text-tertiary)] sm:text-base">
                                                                    {item.children.map((child) => (
                                                                        <li key={child} className="list-disc ml-5">
                                                                            {child}
                                                                        </li>
                                                                    ))}
                                                                </ul>
                                                            ) : null}
                                                        </li>
                                                    ))}
                                                </ul>
                                            ) : null}
                                        </div>
                                    </div>
                                </section>
                            ))}
                        </div>
                    </article>
                </div>
            </section>

            <footer className="border-t border-[var(--row-border)] px-5 py-8 sm:px-8 lg:px-10">
                <div className="mx-auto flex max-w-[1180px] flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                    <p className="text-sm text-[var(--text-secondary)]">© {new Date().getFullYear()} Lemma. All rights reserved.</p>
                    <div className="flex flex-wrap items-center gap-5">
                        {footerLinks.map((link) => (
                            <Link
                                key={link.href}
                                href={link.href}
                                className="text-sm text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
                            >
                                {link.label}
                            </Link>
                        ))}
                    </div>
                </div>
            </footer>
        </main>
    );
}
