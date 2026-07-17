import Link from 'next/link';
import { ArrowLeft, ArrowRight, BookOpen, Check, Github, Menu } from '@/components/ui/icons';
import { DocsMobileNav, DocsSearch, DocsSidebarNav } from '@/components/docs/docs-nav';
import { Logo } from '@/components/brand/logo';
import {
  docsPages,
  getAdjacentDocsPages,
  getDocsHref,
  type DocsBlock,
  type DocsPage,
} from '@/lib/data/docs';

type DocsShellProps = {
  activeSlug?: string;
  children: React.ReactNode;
};

const toneClass = {
  note: 'signal-surface-intelligence',
  warning: 'state-surface-warning text-[var(--text-primary)]',
  success: 'state-surface-success text-[var(--text-primary)]',
};

export function DocsShell({ activeSlug, children }: DocsShellProps) {
  return (
    <div className="docs-shell min-h-screen bg-[var(--bg-canvas)] text-[var(--text-primary)]">
      <header className="sticky top-0 z-30 border-b border-[var(--border-subtle)] bg-[color:color-mix(in_srgb,var(--bg-canvas)_90%,transparent)] backdrop-blur-xl">
        <div className="docs-header-inner mx-auto flex h-14 max-w-[1440px] items-center justify-between gap-4 px-4 md:px-6">
          <div className="docs-header-brand flex items-center gap-3">
            <Link href="/" className="inline-flex items-center gap-2 rounded-md text-[var(--text-primary)]">
              <Logo size="xs" variant="mark-wordmark" />
            </Link>
            <span className="hidden h-5 w-px bg-[var(--border-subtle)] md:block" />
            <Link href="/docs" className="lemma-shell-link hidden md:inline-flex">
              Documentation
            </Link>
          </div>
          <div className="hidden min-w-0 flex-1 justify-center md:flex">
            <DocsSearch />
          </div>
          <nav className="docs-header-nav flex items-center gap-2">
            <Link className="docs-header-quickstart lemma-shell-link px-3 py-2" href="/docs/quickstart">
              Quickstart
            </Link>
            <a
              className="lemma-shell-link px-3 py-2"
              href="https://github.com/lemma-work"
              rel="noreferrer"
              target="_blank"
            >
              <Github className="h-4 w-4" />
              <span className="docs-header-github-label">GitHub</span>
            </a>
          </nav>
        </div>
      </header>
      <div className="docs-mobile-tools md:hidden">
        <DocsSearch />
        <details className="docs-mobile-menu">
          <summary>
            <span className="inline-flex min-w-0 items-center gap-2">
              <Menu className="h-4 w-4 flex-none" />
              <span className="min-w-0 truncate">Browse docs</span>
            </span>
            <span className="text-xs uppercase text-[var(--text-tertiary)]">Menu</span>
          </summary>
          <div className="docs-mobile-menu-panel">
            <DocsMobileNav activeSlug={activeSlug} />
          </div>
        </details>
      </div>
      <div className="mx-auto grid max-w-[1440px] grid-cols-1 md:grid-cols-[280px_minmax(0,1fr)]">
        <aside className="hidden border-r border-[var(--border-subtle)] bg-[var(--bg-canvas)] md:block">
          <DocsSidebarNav activeSlug={activeSlug} />
        </aside>
        <main className="min-w-0">{children}</main>
      </div>
    </div>
  );
}

export function DocsHome() {
  const featured = docsPages.filter((page) => ['getting-started', 'overview', 'sdk/conversations', 'cli/workflows', 'guides/build-a-app'].includes(page.slug));

  return (
    <DocsShell activeSlug="overview">
      <article className="docs-home-article mx-auto max-w-5xl px-5 py-12 md:px-8 md:py-16">
        <div className="max-w-3xl">
          <div className="state-badge-brand inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-medium uppercase">
            <BookOpen className="h-3.5 w-3.5" />
            Lemma docs
          </div>
          <h1 className="docs-home-title mt-5 text-4xl font-semibold leading-tight text-[var(--text-primary)] md:text-5xl">
            Build operating systems for real work.
          </h1>
          <p className="mt-5 text-lg leading-8 text-[var(--text-secondary)]">
            This is the working documentation for Lemma: the platform model, SDK hooks, CLI commands,
            provisioning flow, app build rules, and runtime guardrails.
          </p>
        </div>

        <div className="mt-10 grid gap-4 md:grid-cols-2">
          {featured.map((page) => (
            <DocsFeatureCard key={page.slug} page={page} />
          ))}
        </div>

        <section className="docs-home-reading-path surface-panel mt-12 p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="type-eyebrow">Reading path</p>
              <h2 className="mt-1 text-xl font-semibold text-[var(--text-primary)]">From model to shipped system</h2>
            </div>
            <Link className="inline-flex items-center gap-1 rounded-lg bg-[var(--button-primary-bg)] px-3 py-2 text-sm font-semibold text-[var(--button-primary-fg)] hover:bg-[var(--button-primary-bg-hover)]" href="/docs/quickstart">
              Start quickstart
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
          <div className="mt-6 grid gap-3 md:grid-cols-4">
            {['Scope the pod', 'Model data', 'Wire runtime', 'Ship the app'].map((item, index) => (
              <div className="surface-panel-muted p-4" key={item}>
                <span className="grid h-7 w-7 place-items-center rounded-md bg-[var(--delight-soft)] text-xs font-semibold text-[var(--delight)]">
                  {index + 1}
                </span>
                <p className="mt-3 text-sm font-semibold text-[var(--text-primary)]">{item}</p>
              </div>
            ))}
          </div>
        </section>
      </article>
    </DocsShell>
  );
}

export function DocsPageView({ page }: { page: DocsPage }) {
  const { previous, next } = getAdjacentDocsPages(page);
  const headings = page.blocks.flatMap((block) => ('title' in block && block.title ? [block.title] : []));
  const Icon = page.icon;

  return (
    <DocsShell activeSlug={page.slug}>
      <div className="docs-page-grid grid min-w-0 xl:grid-cols-[minmax(0,1fr)_180px]">
        <article className="docs-page-article mx-auto w-full min-w-0 max-w-[760px] px-5 py-8 md:px-8 md:py-11">
          <header>
            <div className="flex items-center gap-2 text-[var(--delight)]">
              <span className="grid h-7 w-7 flex-none place-items-center rounded-md border border-[var(--border-subtle)] bg-[var(--surface-1)]">
                <Icon className="h-3.5 w-3.5" />
              </span>
              <p className="text-xs font-semibold uppercase tracking-normal">{page.eyebrow}</p>
            </div>
            <h1 className="docs-page-title mt-3 text-3xl font-semibold leading-tight text-[var(--text-primary)] md:text-4xl">{page.title}</h1>
            <p className="mt-3 max-w-2xl text-base leading-7 text-[var(--text-secondary)]">{page.description}</p>
          </header>

          <div className="docs-content-stack mt-9 grid gap-9">
            {page.blocks.map((block, index) => (
              <DocsBlockView block={block} key={`${page.slug}-${index}`} />
            ))}
          </div>

          <nav className="mt-12 grid gap-3 border-t border-[var(--border-subtle)] pt-5 md:grid-cols-2" aria-label="Adjacent docs pages">
            {previous ? (
              <Link className="docs-adjacent-card surface-panel surface-panel-interactive hover-border-delight p-4" href={getDocsHref(previous)}>
                <span className="type-eyebrow inline-flex items-center gap-1">
                  <ArrowLeft className="h-3.5 w-3.5" />
                  Previous
                </span>
                <p className="mt-2 font-semibold text-[var(--text-primary)]">{previous.title}</p>
              </Link>
            ) : (
              <span />
            )}
            {next ? (
              <Link className="docs-adjacent-card surface-panel surface-panel-interactive hover-border-delight p-4 text-right" href={getDocsHref(next)}>
                <span className="type-eyebrow inline-flex items-center gap-1">
                  Next
                  <ArrowRight className="h-3.5 w-3.5" />
                </span>
                <p className="mt-2 font-semibold text-[var(--text-primary)]">{next.title}</p>
              </Link>
            ) : null}
          </nav>
        </article>
        <aside className="hidden min-w-0 border-l border-[var(--border-subtle)] xl:block">
          <div className="sticky top-14 h-[calc(100vh-3.5rem)] overflow-y-auto px-4 py-7">
            <p className="text-xs font-semibold uppercase text-[var(--text-tertiary)]">On this page</p>
            <nav className="mt-3 grid gap-1.5">
              {headings.map((heading) => (
                <a className="text-xs leading-5 text-[var(--text-secondary)] hover:text-[var(--text-primary)]" href={`#${headingId(heading)}`} key={heading}>
                  {heading}
                </a>
              ))}
            </nav>
          </div>
        </aside>
      </div>
    </DocsShell>
  );
}

function DocsFeatureCard({ page }: { page: DocsPage }) {
  const Icon = page.icon;
  return (
    <Link className="docs-feature-card surface-panel surface-panel-interactive hover-border-delight group p-5" href={getDocsHref(page)}>
      <div className="flex items-center justify-between gap-4">
        <span className="grid h-10 w-10 place-items-center rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-2)] text-[var(--delight)]">
          <Icon className="h-5 w-5" />
        </span>
        <ArrowRight className="h-4 w-4 text-[var(--text-tertiary)] transition-transform group-hover:translate-x-1 group-hover:text-[var(--text-primary)]" />
      </div>
      <p className="type-eyebrow mt-5">{page.eyebrow}</p>
      <h2 className="mt-1 text-xl font-semibold text-[var(--text-primary)]">{page.title}</h2>
      <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">{page.description}</p>
    </Link>
  );
}

function DocsBlockView({ block }: { block: DocsBlock }) {
  if (block.type === 'paragraph') {
    return (
      <section className="docs-section min-w-0 scroll-mt-20" id={block.title ? headingId(block.title) : undefined}>
        {block.title ? <h2 className="text-xl font-semibold leading-snug text-[var(--text-primary)]">{block.title}</h2> : null}
        <p className="docs-section-body mt-2.5 text-base leading-7 text-[var(--text-secondary)]">{block.body}</p>
      </section>
    );
  }

  if (block.type === 'list') {
    return (
      <section className="docs-section min-w-0 scroll-mt-20" id={headingId(block.title)}>
        <h2 className="text-xl font-semibold leading-snug text-[var(--text-primary)]">{block.title}</h2>
        {block.body ? <p className="docs-section-body mt-2.5 text-base leading-7 text-[var(--text-secondary)]">{block.body}</p> : null}
        <ul className="mt-3.5 grid gap-2 border-l border-[var(--border-subtle)] pl-4">
          {block.items.map((item) => (
            <li className="docs-list-item flex gap-2.5 text-sm leading-6 text-[var(--text-secondary)]" key={item}>
              <Check className="mt-1 h-3.5 w-3.5 flex-none text-[var(--delight)]" />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </section>
    );
  }

  if (block.type === 'steps') {
    return (
      <section className="docs-section min-w-0 scroll-mt-20" id={headingId(block.title)}>
        <h2 className="text-xl font-semibold leading-snug text-[var(--text-primary)]">{block.title}</h2>
        {block.body ? <p className="docs-section-body mt-2.5 text-base leading-7 text-[var(--text-secondary)]">{block.body}</p> : null}
        <ol className="mt-4 grid gap-2.5">
          {block.items.map((item, index) => (
            <li className="docs-step-item grid grid-cols-[1.5rem_minmax(0,1fr)] gap-3" key={item}>
              <span className="mt-0.5 grid h-6 w-6 place-items-center rounded-md bg-[var(--button-primary-bg)] text-xs font-semibold text-[var(--button-primary-fg)]">
                {index + 1}
              </span>
              <div className="border-b border-[var(--border-subtle)] pb-2.5 text-sm leading-6 text-[var(--text-secondary)]">{item}</div>
            </li>
          ))}
        </ol>
      </section>
    );
  }

  if (block.type === 'code') {
    return (
      <section className="docs-section min-w-0 scroll-mt-20" id={headingId(block.title)}>
        <h2 className="text-xl font-semibold leading-snug text-[var(--text-primary)]">{block.title}</h2>
        {block.body ? <p className="docs-section-body mt-2.5 text-base leading-7 text-[var(--text-secondary)]">{block.body}</p> : null}
        <div className="docs-code-surface code-surface mt-3.5 max-w-full">
          <div className="code-surface-toolbar">
            <span className="code-surface-label">{block.language}</span>
            <span className="code-surface-meta">copy</span>
          </div>
          <pre className="code-surface-pre text-sm leading-6">
            <code>{block.code}</code>
          </pre>
        </div>
      </section>
    );
  }

  if (block.type === 'table') {
    return (
      <section className="docs-section min-w-0 scroll-mt-20" id={headingId(block.title)}>
        <h2 className="text-xl font-semibold leading-snug text-[var(--text-primary)]">{block.title}</h2>
        {block.body ? <p className="docs-section-body mt-2.5 text-base leading-7 text-[var(--text-secondary)]">{block.body}</p> : null}
        <div className="docs-table-frame surface-panel mt-3.5 max-w-full overflow-hidden">
          <div className="docs-table-scroll max-w-full overflow-x-auto">
            <table className="docs-table w-full min-w-[580px] border-collapse text-left text-sm">
              <thead className="bg-[var(--surface-2)] text-[var(--text-secondary)]">
                <tr>
                  {block.columns.map((column) => (
                    <th className="border-b border-[var(--border-subtle)] px-3.5 py-2.5 font-semibold" key={column}>{column}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {block.rows.map((row, rowIndex) => (
                  <tr className="border-b border-[var(--border-subtle)] last:border-0" key={`${block.title}-${rowIndex}`}>
                    {row.map((cell, cellIndex) => (
                      <td className="px-3.5 py-2.5 align-top leading-6 text-[var(--text-secondary)]" key={`${block.title}-${rowIndex}-${cellIndex}`}>
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    );
  }

  return (
    <aside className={`docs-section scroll-mt-20 rounded-lg border p-4 ${toneClass[block.tone ?? 'note']}`} id={headingId(block.title)}>
      <h2 className="text-base font-semibold">{block.title}</h2>
      <p className="mt-1.5 text-sm leading-6 opacity-80">{block.body}</p>
    </aside>
  );
}

function headingId(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}
