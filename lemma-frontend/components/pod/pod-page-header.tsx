'use client';

import Link from 'next/link';
import { ArrowLeft } from '@/components/ui/icons';
import type { ReactNode } from 'react';
import { useLayoutEffect } from 'react';

import type { ProductIconKind } from '@/components/pod/product-icon';
import { usePodTopbar } from '@/components/pod/pod-topbar-context';
import { cn } from '@/lib/utils';

interface PodHeaderMetric {
    label: string;
    value: ReactNode;
    tone?: 'default' | 'ready' | 'warning' | 'muted';
}

interface PodHeaderStepItem {
    label: string;
    step: number;
    active?: boolean;
    complete?: boolean;
}

interface PodPageHeaderProps {
    podId: string;
    title: string;
    description?: string;
    eyebrow?: string;
    backHref?: string;
    backLabel?: string;
    showBack?: boolean;
    productIconKind?: ProductIconKind;
    icon?: ReactNode;
    actions?: ReactNode;
    switcher?: ReactNode;
    meta?: ReactNode;
    tabs?: ReactNode;
    footer?: ReactNode;
    variant?: 'page' | 'bar';
    className?: string;
}

export function PodPageHeader({
    podId,
    title,
    description,
    eyebrow,
    backHref,
    backLabel = 'Pod home',
    showBack = true,
    productIconKind,
    icon,
    actions,
    switcher,
    meta,
    tabs,
    footer,
    variant = 'page',
    className,
}: PodPageHeaderProps) {
    const resolvedBackHref = backHref || `/pod/${podId}`;
    const topbar = usePodTopbar();
    const resolvedBackLabel = showBack ? backLabel : undefined;
    const resolvedTopbarBackHref = showBack ? resolvedBackHref : undefined;

    useLayoutEffect(() => {
        topbar?.setTopbar({
            title,
            backHref: resolvedTopbarBackHref,
            backLabel: resolvedBackLabel,
            eyebrow,
            switcher,
            meta,
            tabs,
            actions,
        });
        return () => topbar?.setTopbar({});
    }, [actions, eyebrow, meta, resolvedBackLabel, resolvedTopbarBackHref, switcher, tabs, title, topbar]);

    void icon;
    void productIconKind;

    if (topbar) return null;

    if (variant === 'bar') {
        return (
            <header
                className={cn(
                    'flex h-12 shrink-0 items-center justify-between gap-3 bg-[color:color-mix(in_srgb,var(--bg-canvas)_90%,transparent)] px-3 backdrop-blur-sm',
                    className
                )}
            >
                <div className="flex min-w-0 flex-1 items-center gap-3 overflow-hidden">
                    {showBack ? (
                        <>
	                            <Link
	                                href={resolvedBackHref}
	                                className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md border border-[var(--button-secondary-border)] bg-[var(--button-secondary-bg)] px-2 text-xs font-medium text-[var(--button-secondary-fg)] transition-gentle hover:border-[var(--field-border-hover)] hover:bg-[var(--button-secondary-bg-hover)]"
	                            >
                                <ArrowLeft className="h-3.5 w-3.5" />
                                {backLabel}
                            </Link>
                            <span className="hidden h-4 w-px shrink-0 bg-[color:var(--border-subtle)] sm:block" />
                        </>
                    ) : null}
                    <div className="flex min-w-0 shrink items-center gap-2">
                        <span className="flex min-w-0 flex-col">
                            {eyebrow ? (
                                <span className="font-mono truncate type-eyebrow-medium">
                                    {eyebrow}
                                </span>
                            ) : null}
                            <span className="max-w-[18rem] truncate text-sm font-medium text-[var(--text-primary)]">
                                {title}
                            </span>
                        </span>
                        {switcher ? <div className="shrink-0">{switcher}</div> : null}
                    </div>
                    {tabs ? <div className="min-w-0 flex-1 overflow-hidden">{tabs}</div> : null}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                    {meta ? <div className="hidden items-center gap-1.5 lg:flex">{meta}</div> : null}
                    {actions ? (
                        <div className="flex items-center gap-2 [&_button]:h-8 [&_button]:px-3 [&_button]:text-xs">
                            {actions}
                        </div>
                    ) : null}
                </div>
            </header>
        );
    }

    return (
        <section
            className={cn(
                'mb-5 bg-transparent px-0 pb-3 pt-0',
                className
            )}
        >
            <div className="flex min-h-8 flex-wrap items-start justify-between gap-4">
                <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                    <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                        <div className="flex min-w-0 items-center gap-2">
                            {showBack ? (
                                <>
	                                    <Link
	                                        href={resolvedBackHref}
	                                        className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md border border-[var(--button-secondary-border)] bg-[var(--button-secondary-bg)] px-2 text-xs font-medium text-[var(--button-secondary-fg)] transition-gentle hover:border-[var(--field-border-hover)] hover:bg-[var(--button-secondary-bg-hover)]"
	                                    >
                                        <ArrowLeft className="h-3.5 w-3.5" />
                                        {backLabel}
                                    </Link>
                                    <span className="hidden h-4 w-px shrink-0 bg-[color:var(--border-subtle)] sm:block" />
                                </>
                            ) : null}
                            <div className="flex min-w-0 flex-col">
                                {eyebrow ? (
                                    <span className="truncate type-eyebrow-medium">
                                        {eyebrow}
                                    </span>
                                ) : null}
	                                <h1 className="truncate font-display text-xl font-medium tracking-normal text-[var(--text-primary)]">
                                    {title}
                                </h1>
                            </div>
                            {switcher ? <div className="shrink-0">{switcher}</div> : null}
                        </div>
                        {meta ? <div className="min-w-0">{meta}</div> : null}
                        {tabs ? (
                            <>
                                <span className="hidden h-4 w-px bg-[color:var(--border-subtle)] lg:block" />
                                <div className="min-w-0 overflow-x-auto">{tabs}</div>
                            </>
                        ) : null}
                        {footer ? (
                            <>
                                <span className="hidden h-4 w-px bg-[color:var(--border-subtle)] lg:block" />
                                <div className="min-w-0">{footer}</div>
                            </>
                        ) : null}
                    </div>
                    {description ? (
                        <p className="max-w-3xl text-sm leading-6 text-[var(--text-tertiary)]">
                            {description}
                        </p>
                    ) : null}
                </div>
                <div className="flex shrink-0 flex-wrap items-center gap-2 [&_button]:h-8 [&_button]:px-3 [&_button]:text-xs">
                    {actions}
                </div>
            </div>
        </section>
    );
}

export function PodHeaderMetrics({
    items,
    className,
}: {
    items: PodHeaderMetric[];
    className?: string;
}) {
    if (items.length === 0) return null;

    return (
        <div className={cn('flex flex-wrap items-center gap-1.5 text-xs text-[var(--text-secondary)]', className)}>
            {items.map((item) => (
                <span
                    key={item.label}
                    className="inline-flex items-center"
                >
                    <span
                        className={cn(
                            'chip chip-md',
                            item.tone === 'ready' && 'state-badge-success',
                            item.tone === 'warning' && 'state-badge-warning',
                            item.tone === 'muted' && 'chip-muted'
                        )}
                    >
                        <span className="text-[var(--text-tertiary)]">{item.label}</span>
                        <span
                            className={cn(
                                'font-medium text-[var(--text-secondary)]',
                                item.tone === 'ready' && 'text-[var(--state-success)]',
                                item.tone === 'warning' && 'text-[var(--state-warning)]',
                                item.tone === 'muted' && 'text-[var(--text-tertiary)]'
                            )}
                        >
                            {item.value}
                        </span>
                    </span>
                </span>
            ))}
        </div>
    );
}

export function PodHeaderStepper({
    items,
    className,
}: {
    items: PodHeaderStepItem[];
    className?: string;
}) {
    if (items.length === 0) return null;

    return (
        <div className={cn('pod-header-stepper', className)}>
            {items.map((item, index) => (
                <span key={item.step} className="inline-flex items-center gap-2">
                    {index > 0 ? <span className="pod-header-stepper-connector" /> : null}
                    <span
                        className="pod-header-step"
                        data-state={item.active ? 'active' : item.complete ? 'complete' : undefined}
                    >
                        <span className="pod-header-step-index">
                            {item.step}
                        </span>
                        {item.label}
                    </span>
                </span>
            ))}
        </div>
    );
}

export function PodHeaderTabLink({
    href,
    active,
    icon,
    children,
}: {
    href: string;
    active?: boolean;
    icon?: ReactNode;
    children: ReactNode;
}) {
    return (
        <Link
            href={href}
            className="lemma-header-tab inline-flex items-center gap-1.5"
            data-state={active ? 'active' : undefined}
            aria-current={active ? 'page' : undefined}
        >
            {icon ? <span className="text-[var(--text-tertiary)]">{icon}</span> : null}
            {children}
        </Link>
    );
}
