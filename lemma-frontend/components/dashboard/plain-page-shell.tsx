'use client';

import Link from 'next/link';
import type { ReactNode } from 'react';
import { ArrowLeft } from '@/components/ui/icons';
import { HomeTopbar } from '@/components/home/home-topbar';
import { cn } from '@/lib/utils';

interface PlainPageShellProps {
    children: ReactNode;
    contentWidthClassName?: string;
    contentClassName?: string;
    centerContent?: boolean;
    title?: ReactNode;
    icon?: ReactNode;
    backHref?: string;
    backLabel?: string;
    meta?: ReactNode;
    tabs?: ReactNode;
    actions?: ReactNode;
}

export function PlainPageShell({
    children,
    contentWidthClassName = 'max-w-6xl',
    contentClassName,
    centerContent = false,
    title,
    icon,
    backHref,
    backLabel,
    meta,
    tabs,
    actions,
}: PlainPageShellProps) {
    const hasHeader = Boolean(title || icon || backHref || meta || tabs || actions);

    return (
        <div className="min-h-screen bg-[var(--pod-shell-bg)] text-[var(--text-primary)]">
            <HomeTopbar
                actions={actions}
                leftContent={hasHeader ? (
                    <div className="flex min-w-0 flex-col gap-1">
                        <div className="flex min-w-0 items-center gap-2">
                            {backHref && backLabel ? (
                                <Link href={backHref} className="lemma-shell-link lemma-shell-link-sm mr-1 hidden sm:inline-flex">
                                    <ArrowLeft className="h-3.5 w-3.5" />
                                    {backLabel}
                                </Link>
                            ) : null}
                            {icon ? <span className="flex h-5 w-5 shrink-0 items-center justify-center">{icon}</span> : null}
                            {title ? <h1 className="min-w-0 truncate text-base font-medium leading-6 text-[var(--text-primary)]">{title}</h1> : null}
                            {meta ? (
                                <>
                                    <span className="hidden h-4 w-px bg-[var(--border-subtle)] md:block" />
                                    <span className="hidden min-w-0 truncate text-xs text-[var(--text-tertiary)] md:inline-flex">{meta}</span>
                                </>
                            ) : null}
                        </div>
                    </div>
                ) : null}
            />
            <main className="min-h-[calc(100vh-4rem)] bg-[var(--pod-main-bg)] px-4 py-5 sm:px-7 sm:py-8 lg:px-12">
                <div className={cn('mx-auto flex min-h-[calc(100vh-7rem)] w-full flex-col', contentWidthClassName, centerContent && 'justify-center', contentClassName)}>
                    {tabs ? <div className="mb-6 min-w-0 overflow-x-auto">{tabs}</div> : null}
                    {children}
                </div>
            </main>
        </div>
    );
}
