import Link from 'next/link';
import type { ReactNode } from 'react';
import { ArrowLeft, ArrowUpRight } from '@/components/ui/icons';
import { Logo } from '@/components/brand/logo';
import { copyrightNotice } from '@/lib/company';
import { config } from '@/lib/config';
import type { LegalDocument, LegalListItem } from '@/lib/data/legal';

type LegalPageProps = {
    document: LegalDocument;
    /** Rendered between the plain answers and the policy itself. Anything the
     *  document can be *acted on* with belongs here rather than at the bottom:
     *  a control mentioned in a policy nobody scrolls to may as well not exist. */
    action?: ReactNode;
};

const footerLinks = [
    { href: '/privacy', label: 'Privacy' },
    { href: '/tos', label: 'Terms' },
    { href: '/login', label: 'Sign in' },
] as const;

function toSectionId(title: string) {
    return title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
}

function ordinal(index: number) {
    return (index + 1).toString().padStart(2, '0');
}

/**
 * A legal document, laid out to be read rather than to be scrolled past.
 *
 * Three things drive the layout, and all three are reactions to what the
 * previous version did:
 *
 * - **The heading sits above its body, not beside it.** Section titles used to
 *   live in a 180px column in 36px serif, so "What This Policy Covers" broke
 *   into four lines and outweighed the paragraph it was labelling. A heading
 *   that is louder than its content is a heading working against the document.
 * - **One measured column.** Body copy is capped near 68 characters, the width
 *   prose is actually legible at, instead of running the full width of a card.
 * - **The answers come first.** Nobody opens a privacy page to read a privacy
 *   page; they open it with one question. `answers` puts those at the top in
 *   the words people use, and the numbered sections stay underneath for the
 *   reader — or the regulator — who needs the whole thing.
 */
export function LegalPage({ document, action }: LegalPageProps) {
    const siblings = footerLinks.filter((link) => link.href !== '/login');

    return (
        <main className="min-h-screen bg-[var(--bg-canvas)] text-[var(--text-primary)]">
            <header className="sticky top-0 z-20 border-b border-[var(--row-border)] bg-[color:color-mix(in_srgb,var(--bg-canvas)_84%,transparent)] backdrop-blur-xl">
                <div className="mx-auto flex max-w-[1120px] items-center justify-between gap-4 px-5 py-4 sm:px-8 lg:px-10">
                    <Link href="/" aria-label="Lemma home" className="inline-flex items-center">
                        <Logo size="xs" variant="mark-wordmark" />
                    </Link>
                    <nav className="flex items-center gap-5 text-sm">
                        {siblings.map((link) => (
                            <Link
                                key={link.href}
                                href={link.href}
                                className="text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
                            >
                                {link.label}
                            </Link>
                        ))}
                        <span aria-hidden="true" className="hidden h-4 w-px bg-[var(--row-border)] sm:block" />
                        <Link
                            href="/"
                            className="hidden items-center gap-2 text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)] sm:inline-flex"
                        >
                            <ArrowLeft className="h-4 w-4" />
                            <span>Home</span>
                        </Link>
                    </nav>
                </div>
            </header>

            <div className="mx-auto max-w-[1120px] px-5 sm:px-8 lg:px-10">
                {/* ── Masthead */}
                <section className="pb-12 pt-14 sm:pt-20 lg:pt-24">
                    <p className="type-eyebrow-mono">Legal</p>
                    <h1 className="mt-6 max-w-[16ch] [font-family:var(--font-landing-serif)] text-5xl font-normal leading-none tracking-normal text-[var(--text-primary)] sm:text-7xl">
                        {document.title}
                    </h1>
                    <p className="mt-7 max-w-[60ch] text-lg leading-8 text-[var(--text-secondary)]">
                        {document.description}
                    </p>
                    <div className="mt-8 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm text-[var(--text-tertiary)]">
                        <span>Effective {document.effectiveDate}</span>
                        <span aria-hidden="true" className="hidden h-1 w-1 rounded-full bg-[var(--row-border)] sm:inline-block" />
                        <Link
                            href={`mailto:${config.SUPPORT_EMAIL}`}
                            className="inline-flex items-center gap-1 transition-colors hover:text-[var(--text-primary)]"
                        >
                            <span>{config.SUPPORT_EMAIL}</span>
                            <ArrowUpRight className="h-3.5 w-3.5" />
                        </Link>
                    </div>
                </section>

                {/* ── The short version */}
                <section aria-label="Summary" className="border-t border-[var(--row-border)] pt-8">
                    <p className="type-eyebrow-mono">The short version</p>
                    <div className="mt-6 grid gap-x-10 gap-y-6 sm:grid-cols-3">
                        {document.summary.map((item, index) => (
                            <p
                                key={item}
                                className="max-w-[38ch] text-sm leading-6 text-[var(--text-secondary)]"
                            >
                                <span
                                    aria-hidden="true"
                                    className="mr-3 font-mono text-xs text-[var(--action-primary)]"
                                >
                                    {ordinal(index)}
                                </span>
                                {item}
                            </p>
                        ))}
                    </div>
                </section>

                {/* ── Straight answers */}
                {document.answers?.length ? (
                    <section aria-label="Common questions" className="mt-16 sm:mt-20">
                        <p className="type-eyebrow-mono">Straight answers</p>
                        <div className="mt-2 grid sm:grid-cols-2 lg:grid-cols-3">
                            {document.answers.map((entry) => (
                                <div
                                    key={entry.question}
                                    className="border-t border-[var(--row-border)] py-7 sm:pr-10"
                                >
                                    <p className="text-sm leading-6 text-[var(--text-primary)]">
                                        {entry.question}
                                    </p>
                                    <p className="mt-3 [font-family:var(--font-landing-serif)] text-2xl font-normal leading-none text-[var(--action-primary)]">
                                        {entry.answer}
                                    </p>
                                    <p className="mt-4 max-w-[42ch] text-sm leading-6 text-[var(--text-secondary)]">
                                        {entry.detail}
                                    </p>
                                </div>
                            ))}
                        </div>
                    </section>
                ) : null}

                {action ? <section className="mt-14 sm:mt-16">{action}</section> : null}

                {/* ── The document */}
                <section className="mt-16 grid gap-x-16 gap-y-10 pb-20 sm:mt-20 lg:grid-cols-[196px_minmax(0,1fr)]">
                    <aside className="hidden lg:sticky lg:top-24 lg:block lg:self-start">
                        <p className="type-eyebrow-mono">Contents</p>
                        <nav className="mt-5 space-y-3">
                            {document.sections.map((section, index) => (
                                <a
                                    key={section.title}
                                    href={`#${toSectionId(section.title)}`}
                                    className="flex items-baseline gap-3 text-sm leading-6 text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
                                >
                                    <span className="font-mono text-xs text-[var(--text-soft)]">
                                        {ordinal(index)}
                                    </span>
                                    <span>{section.title}</span>
                                </a>
                            ))}
                        </nav>
                    </aside>

                    <article className="min-w-0">
                        {document.sections.map((section, index) => (
                            <section
                                key={section.title}
                                id={toSectionId(section.title)}
                                className="scroll-mt-24 border-t border-[var(--row-border)] py-10 first:border-t-0 first:pt-0 sm:py-12 sm:first:pt-0"
                            >
                                <p className="type-eyebrow-mono">{ordinal(index)}</p>
                                <h2 className="mt-4 max-w-[22ch] [font-family:var(--font-landing-serif)] text-2xl font-normal leading-tight tracking-normal text-[var(--text-primary)]">
                                    {section.title}
                                </h2>

                                {section.body ? (
                                    <p className="mt-6 max-w-[68ch] text-base leading-7 text-[var(--text-secondary)]">
                                        {section.body}
                                    </p>
                                ) : null}

                                {section.items?.length ? (
                                    <ul className="mt-7 max-w-[68ch] space-y-6">
                                        {section.items.map((item) => (
                                            <ListItem key={item.text} item={item} />
                                        ))}
                                    </ul>
                                ) : null}
                            </section>
                        ))}
                    </article>
                </section>
            </div>

            <footer className="border-t border-[var(--row-border)] px-5 py-8 sm:px-8 lg:px-10">
                <div className="mx-auto flex max-w-[1120px] flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                    <p className="text-sm text-[var(--text-secondary)]">{copyrightNotice()} All rights reserved.</p>
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

/**
 * The marker is a short rule, not a disc or a left border.
 *
 * A per-item left border made every item read as a pulled quote, which is a lot
 * of emphasis to spend on the fourth bullet about backups. The rule is the same
 * hairline the rest of the page is built from, hanging in the gutter.
 */
function ListItem({ item }: { item: LegalListItem }) {
    return (
        <li className="relative pl-6">
            <span
                aria-hidden="true"
                className="absolute left-0 top-[0.85em] h-px w-3.5 bg-[var(--row-border)]"
            />
            <p className="text-base leading-7 text-[var(--text-secondary)]">
                {item.label ? (
                    <span className="text-[var(--text-primary)]">{item.label}. </span>
                ) : null}
                {item.text}
            </p>
            {item.children?.length ? (
                <ul className="mt-3 space-y-2 pl-4 text-sm leading-6 text-[var(--text-tertiary)]">
                    {item.children.map((child) => (
                        <li key={child} className="list-disc">
                            {child}
                        </li>
                    ))}
                </ul>
            ) : null}
        </li>
    );
}
