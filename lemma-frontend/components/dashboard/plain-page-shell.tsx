'use client';

import Link from 'next/link';
import type { ReactNode } from 'react';
import { useState } from 'react';
import { ArrowLeft } from '@/components/ui/icons';
import { SettingsMobileSidebar, SettingsSidebar } from '@/components/settings/settings-sidebar';
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
    const [isMobileSidebarOpen, setIsMobileSidebarOpen] = useState(false);

    return (
        <div className="flex h-dvh overflow-hidden bg-[var(--pod-shell-bg)] text-[var(--text-primary)]">
            <aside className="hidden h-full w-56 shrink-0 overflow-hidden border-r border-[color:color-mix(in_srgb,var(--border-subtle)_42%,transparent)] md:block">
                <SettingsSidebar />
            </aside>

            <div className="flex min-w-0 flex-1 flex-col bg-[var(--pod-main-bg)]">
                {hasHeader ? (
                    <header className="shrink-0 border-b border-[color:color-mix(in_srgb,var(--border-subtle)_42%,transparent)] bg-[var(--pod-main-bg)]">
                        <div className="flex h-12 min-w-0 items-center gap-2 px-3 sm:px-5 lg:px-7">
                            <SettingsMobileSidebar
                                open={isMobileSidebarOpen}
                                onOpenChange={setIsMobileSidebarOpen}
                            />
                            {backHref && backLabel ? (
                                <Link href={backHref} className="lemma-shell-link lemma-shell-link-sm mr-1 hidden sm:inline-flex">
                                    <ArrowLeft className="h-3.5 w-3.5" />
                                    {backLabel}
                                </Link>
                            ) : null}
                            {icon ? <span className="flex h-5 w-5 shrink-0 items-center justify-center text-[var(--text-tertiary)]">{icon}</span> : null}
                            {title ? <h1 className="min-w-0 truncate text-sm font-semibold leading-6 text-[var(--text-primary)] sm:text-base">{title}</h1> : null}
                            {meta ? (
                                <>
                                    <span className="hidden h-4 w-px bg-[var(--border-subtle)] sm:block" />
                                    <span className="hidden min-w-0 truncate text-xs text-[var(--text-tertiary)] sm:inline-flex">{meta}</span>
                                </>
                            ) : null}
                            {actions ? <div className="ml-auto flex shrink-0 items-center gap-2">{actions}</div> : null}
                        </div>
                        {tabs ? (
                            <div className="min-w-0 overflow-x-auto border-t border-[color:color-mix(in_srgb,var(--border-subtle)_28%,transparent)] px-3 sm:px-5 lg:px-7">
                                <div className="flex h-10 items-center">{tabs}</div>
                            </div>
                        ) : null}
                    </header>
                ) : null}

                <main className="min-h-0 flex-1 overflow-y-auto bg-[var(--pod-main-bg)] px-4 py-5 sm:px-7 sm:py-7 lg:px-10">
                    <div className={cn('mx-auto flex w-full flex-col', contentWidthClassName, centerContent && 'min-h-full justify-center', contentClassName)}>
                        {children}
                    </div>
                </main>
            </div>
        </div>
    );
}
