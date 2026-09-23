'use client';

import Link from 'next/link';
import type { ReactNode } from 'react';

import { ResourceHeader } from '@/components/pod/resource-layout';
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
    eyebrow?: string;
    backHref?: string;
    backLabel?: string;
    showBack?: boolean;
    icon?: ReactNode;
    actions?: ReactNode;
    switcher?: ReactNode;
    meta?: ReactNode;
    tabs?: ReactNode;
}

/**
 * Builder-route chrome (the guided create flows and pod settings). A thin
 * default over `ResourceHeader`: back defaults to pod home rather than being
 * required, and `showBack` turns it off for routes reached from the nav.
 */
export function PodPageHeader({
    podId,
    title,
    eyebrow,
    backHref,
    backLabel = 'Pod home',
    showBack = true,
    icon,
    actions,
    switcher,
    meta,
    tabs,
}: PodPageHeaderProps) {
    return (
        <ResourceHeader
            title={title}
            icon={icon}
            backHref={showBack ? backHref || `/pod/${podId}` : undefined}
            backLabel={showBack ? backLabel : undefined}
            eyebrow={eyebrow}
            switcher={switcher}
            meta={meta}
            tabs={tabs}
            actions={actions}
        />
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
