'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { ArrowRight, Search, X } from '@/components/ui/icons';
import { QuietEmptyState } from '@/components/shared/empty-state';
import { docsGroups, docsPages, getDocsHref, type DocsBlock } from '@/lib/data/docs';

type DocsSidebarNavProps = {
  activeSlug?: string;
};

const sidebarScrollKey = 'lemma-docs-sidebar-scroll';

export function DocsSidebarNav({ activeSlug }: DocsSidebarNavProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pathname = usePathname();

  const saveScrollPosition = () => {
    const node = scrollRef.current;
    if (!node) return;
    window.sessionStorage.setItem(sidebarScrollKey, String(node.scrollTop));
  };

  useLayoutEffect(() => {
    const node = scrollRef.current;
    if (!node) return;
    const saved = window.sessionStorage.getItem(sidebarScrollKey);
    if (saved) {
      node.scrollTop = Number(saved);
    }
  }, [pathname]);

  useEffect(() => {
    return () => {
      saveScrollPosition();
    };
  }, []);

  return (
    <div
      className="sticky top-14 h-[calc(100vh-3.5rem)] overflow-y-auto px-4 py-6"
      onScroll={saveScrollPosition}
      ref={scrollRef}
    >
      <nav className="grid gap-7" aria-label="Documentation sidebar">
        {docsGroups.map((group) => (
          <section key={group.title}>
            <p className="px-2 text-xs font-medium uppercase text-[var(--text-tertiary)]">{group.title}</p>
            <div className="mt-2 grid gap-1">
              {group.pages.map((slug) => {
                const page = docsPages.find((item) => item.slug === slug);
                if (!page) return null;
                const Icon = page.icon;
                const active = activeSlug === page.slug;
                return (
                  <Link
                    className={`flex items-center gap-2 rounded-lg px-2.5 py-2 text-sm font-medium transition-colors ${
                      active
                        ? 'bg-[var(--button-primary-bg)] text-[var(--button-primary-fg)]'
                        : 'text-[var(--text-secondary)] hover:bg-[var(--surface-1)] hover:text-[var(--text-primary)]'
                    }`}
                    href={getDocsHref(page)}
                    key={page.slug}
                    onClick={saveScrollPosition}
                  >
                    <Icon className="h-4 w-4 flex-none" />
                    <span className="min-w-0 truncate">{page.title}</span>
                  </Link>
                );
              })}
            </div>
          </section>
        ))}
      </nav>
    </div>
  );
}

export function DocsMobileNav({ activeSlug }: DocsSidebarNavProps) {
  return (
    <nav className="grid gap-5" aria-label="Mobile documentation navigation">
      {docsGroups.map((group) => (
        <section key={group.title}>
          <p className="px-1.5 text-xs font-medium uppercase text-[var(--text-tertiary)]">{group.title}</p>
          <div className="mt-2 grid gap-1">
            {group.pages.map((slug) => {
              const page = docsPages.find((item) => item.slug === slug);
              if (!page) return null;
              const Icon = page.icon;
              const active = activeSlug === page.slug;
              return (
                <Link
                  className={`flex min-w-0 items-center gap-2 rounded-lg px-2.5 py-2 text-sm font-medium transition-colors ${
                    active
                      ? 'bg-[var(--button-primary-bg)] text-[var(--button-primary-fg)]'
                      : 'text-[var(--text-secondary)] hover:bg-[var(--surface-1)] hover:text-[var(--text-primary)]'
                  }`}
                  href={getDocsHref(page)}
                  key={page.slug}
                >
                  <Icon className="h-4 w-4 flex-none" />
                  <span className="min-w-0 truncate">{page.title}</span>
                </Link>
              );
            })}
          </div>
        </section>
      ))}
    </nav>
  );
}

export function DocsSearch() {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);

  const results = useMemo(() => {
    const normalizedQuery = normalize(query);
    if (!normalizedQuery) return docsPages.slice(0, 6);

    return docsPages
      .map((page) => {
        const haystack = normalize([
          page.title,
          page.eyebrow,
          page.group,
          page.description,
          ...page.blocks.flatMap(blockSearchText),
        ].join(' '));

        const title = normalize(page.title);
        const score =
          title === normalizedQuery
            ? 4
            : title.includes(normalizedQuery)
              ? 3
              : haystack.includes(normalizedQuery)
                ? 1
                : 0;

        return { page, score };
      })
      .filter((item) => item.score > 0)
      .sort((a, b) => b.score - a.score || a.page.title.localeCompare(b.page.title))
      .slice(0, 8)
      .map((item) => item.page);
  }, [query]);

  useEffect(() => {
    const handlePointerDown = (event: PointerEvent) => {
      if (!wrapperRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };

    document.addEventListener('pointerdown', handlePointerDown);
    return () => document.removeEventListener('pointerdown', handlePointerDown);
  }, []);

  return (
    <div className="relative w-full max-w-md" ref={wrapperRef}>
      <div className="flex items-center gap-2 rounded-lg border border-[var(--field-border)] bg-[var(--field-bg)] px-3 py-2 text-sm text-[var(--text-secondary)] shadow-[var(--shadow-xs)] focus-within:border-[var(--field-border-focus)] focus-within:ring-2 focus-within:ring-[color:var(--ring)]">
        <Search className="h-4 w-4 flex-none" />
        <input
          aria-label="Search docs"
          className="inline-edit-field min-w-0 flex-1 bg-transparent text-sm text-[var(--text-primary)] outline-none placeholder:text-[var(--text-tertiary)]"
          onChange={(event) => {
            setQuery(event.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(event) => {
            if (event.key === 'Escape') {
              setOpen(false);
              event.currentTarget.blur();
            }
          }}
          placeholder="Search docs"
          value={query}
        />
        {query ? (
          <button
            aria-label="Clear search"
            className="lemma-quiet-icon-button h-5 w-5 text-[var(--text-tertiary)]"
            onClick={() => {
              setQuery('');
              setOpen(true);
            }}
            type="button"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </div>

      {open ? (
        <div className="absolute left-0 right-0 top-[calc(100%+0.5rem)] z-40 overflow-hidden rounded-lg border border-[var(--card-border)] bg-[var(--card-bg)] shadow-[var(--shadow-lg)]">
          <div className="border-b border-[var(--border-subtle)] px-3 py-2 text-xs font-medium uppercase text-[var(--text-tertiary)]">
            {query.trim() ? 'Results' : 'Start here'}
          </div>
          {results.length > 0 ? (
            <div className="max-h-[360px] overflow-y-auto p-1.5">
              {results.map((page) => {
                const Icon = page.icon;
                return (
                  <Link
                    className="group flex gap-3 rounded-lg px-2.5 py-2.5 text-left hover:bg-[var(--card-bg-hover)]"
                    href={getDocsHref(page)}
                    key={page.slug}
                    onClick={() => setOpen(false)}
                  >
                    <span className="mt-0.5 grid h-7 w-7 flex-none place-items-center rounded-md border border-[var(--border-subtle)] text-[var(--delight)]">
                      <Icon className="h-3.5 w-3.5" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-semibold text-[var(--text-primary)]">{page.title}</span>
                      <span className="mt-0.5 line-clamp-2 block text-xs leading-5 text-[var(--text-secondary)]">{page.description}</span>
                    </span>
                    <ArrowRight className="mt-1 h-3.5 w-3.5 flex-none text-[var(--text-tertiary)] group-hover:text-[var(--text-primary)]" />
                  </Link>
                );
              })}
            </div>
          ) : (
            <QuietEmptyState className="justify-center px-3 py-8 text-center">No docs match this search.</QuietEmptyState>
          )}
        </div>
      ) : null}
    </div>
  );
}

function blockSearchText(block: DocsBlock): string[] {
  if (block.type === 'paragraph') return [block.title ?? '', block.body];
  if (block.type === 'list' || block.type === 'steps') return [block.title, block.body ?? '', ...block.items];
  if (block.type === 'code') return [block.title, block.body ?? '', block.language, block.code];
  if (block.type === 'table') return [block.title, block.body ?? '', ...block.columns, ...block.rows.flat()];
  return [block.title, block.body];
}

function normalize(value: string): string {
  return value.toLowerCase().replace(/\s+/g, ' ').trim();
}
