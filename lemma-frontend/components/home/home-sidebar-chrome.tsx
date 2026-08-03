'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useMemo, useState } from 'react';
import {
    ChevronDown,
    ChevronRight,
    PanelLeftClose,
    PanelLeftOpen,
    Plus,
} from '@/components/ui/icons';
import { Logo } from '@/components/brand/logo';
import { HomeImportButton } from '@/components/bundle/home-import-button';
import { LocalSettingsButton } from '@/components/desktop/local-settings-button';
import { ProductIcon, type ProductIconKind } from '@/components/pod/product-icon';
import { AccountMenu } from '@/components/shared/account-menu';
import { QuietEmptyState } from '@/components/shared/empty-state';
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from '@/components/ui/sheet';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useAccessiblePods } from '@/lib/hooks/use-pods';

function SidebarContent({
    onNavigate,
    onClose,
    isPodsOpen,
    setIsPodsOpen,
}: {
    onNavigate: () => void;
    onClose?: () => void;
    isPodsOpen: boolean;
    setIsPodsOpen: (value: boolean | ((prev: boolean) => boolean)) => void;
}) {
    const pathname = usePathname();
    const { data: podsResponse } = useAccessiblePods();

    const pods = useMemo(() => podsResponse?.items || [], [podsResponse?.items]);
    const visiblePodGroups = useMemo(() => {
        let remaining = 8;

        return (podsResponse?.groups || []).map((group) => {
            const groupPods = group.pods.slice(0, remaining);
            remaining -= groupPods.length;
            return { ...group, pods: groupPods };
        }).filter((group) => group.pods.length > 0);
    }, [podsResponse?.groups]);
    const showOrganizationLabels = podsResponse?.hasMultipleOrganizations;

    return (
        <div className="flex h-full w-full min-h-0 flex-col overflow-hidden px-4 py-4">
            <div className="mb-4 flex items-center justify-between gap-2">
                <Link href="/" onClick={onNavigate} className="inline-flex px-1 py-1">
                    <Logo size="sm" variant="mark-wordmark" />
                </Link>
                {onClose ? (
                    <button
                        type="button"
                        onClick={onClose}
                        className="home-sidebar-surface-button surface-panel-muted inline-flex h-9 w-9 items-center justify-center p-0 text-[var(--text-secondary)] transition-colors hover:border-[var(--border-strong)] hover:bg-[var(--row-bg-hover)] hover:text-[var(--text-primary)]"
                        aria-label="Collapse sidebar"
                    >
                        <PanelLeftClose className="h-4 w-4" />
                    </button>
                ) : null}
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto">
                <nav className="space-y-1">
                    <Link
                        href="/"
                        onClick={onNavigate}
                        data-active={pathname === '/' ? 'true' : undefined}
                        className="lemma-sidebar-row lemma-sidebar-row-comfy"
                    >
                        <ProductIcon kind="pods" size="sm" state={pathname === '/' ? 'selected' : 'default'} interactive />
                        Pods
                    </Link>
                    <Link
                        href="/create-pod"
                        onClick={onNavigate}
                        data-active={pathname === '/create-pod' ? 'true' : undefined}
                        className="lemma-sidebar-row lemma-sidebar-row-comfy"
                    >
                        <Plus className="h-4 w-4" />
                        New pod
                    </Link>
                    <HomeImportButton onNavigate={onNavigate} />
                </nav>

                <div className="mt-8 space-y-5">
                    <div>
                        <button
                            type="button"
                            onClick={() => setIsPodsOpen((prev) => !prev)}
                            className="home-sidebar-section-button flex w-full items-center justify-between rounded-xl px-2 py-2 text-left type-eyebrow transition-colors hover:text-[var(--text-secondary)]"
                        >
                            <span>Pods</span>
                            <span className="text-[var(--text-secondary)]">
                                {isPodsOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                            </span>
                        </button>

                        {isPodsOpen ? (
                            <div className="mt-1 space-y-0.5">
                                {pods.length > 0 ? (
                                    showOrganizationLabels ? visiblePodGroups.map((group) => (
                                        <div key={group.organization.id} className="space-y-0.5">
                                            <div className="px-2 pt-2 pb-1 text-xs font-medium uppercase tracking-normal text-[var(--text-tertiary)]">
                                                {group.organization.name}
                                            </div>
                                            {group.pods.map((pod) => (
                                                <PodSidebarLink
                                                    key={pod.id}
                                                    pod={pod}
                                                    pathname={pathname}
                                                    onNavigate={onNavigate}
                                                />
                                            ))}
                                        </div>
                                    )) : pods.slice(0, 8).map((pod) => (
                                        <PodSidebarLink
                                            key={pod.id}
                                            pod={pod}
                                            pathname={pathname}
                                            onNavigate={onNavigate}
                                        />
                                    ))
                                ) : (
                                    <QuietEmptyState className="lemma-sidebar-empty">No pods yet.</QuietEmptyState>
                                )}
                            </div>
                        ) : null}
                    </div>
                </div>
            </div>

            <div className="-mx-4 mt-5 border-t border-[color:var(--row-border)] px-4 pt-3">
                <LocalSettingsButton className="mb-2" />
                <AccountMenu className="w-full" onNavigate={onNavigate} />
            </div>
        </div>
    );
}

function PodSidebarLink({
    pod,
    pathname,
    onNavigate,
}: {
    pod: { id: string; name: string };
    pathname: string;
    onNavigate: () => void;
}) {
    return (
        <Link
            href={`/pod/${pod.id}`}
            onClick={onNavigate}
            data-active={pathname === `/pod/${pod.id}` || pathname.startsWith(`/pod/${pod.id}/`) ? 'true' : undefined}
            className="lemma-sidebar-row lemma-sidebar-row-comfy min-w-0"
        >
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-[color:var(--chip-border)] bg-[var(--chip-bg)] text-xs font-semibold text-[var(--action-primary)]">
                {pod.name.charAt(0).toUpperCase()}
            </span>
            <span className="truncate">{pod.name}</span>
        </Link>
    );
}

function RailIconLink({
    href,
    label,
    kind,
    isActive,
}: {
    href: string;
    label: string;
    kind: ProductIconKind;
    isActive?: boolean;
}) {
    return (
        <Tooltip>
            <TooltipTrigger asChild>
                <Link
                    href={href}
                    aria-label={label}
                    data-active={isActive ? 'true' : undefined}
                    className="lemma-sidebar-rail-icon"
                >
                    <ProductIcon kind={kind} size="sm" state={isActive ? 'selected' : 'default'} interactive />
                </Link>
            </TooltipTrigger>
            <TooltipContent side="right">{label}</TooltipContent>
        </Tooltip>
    );
}

function CollapsedSidebarRail({
    onSidebarOpenChange,
}: {
    onSidebarOpenChange: (open: boolean) => void;
}) {
    const pathname = usePathname();
    const navItems = [
        {
            href: '/',
            label: 'Pods',
            kind: 'pods' as const,
            isActive: pathname === '/',
        },
    ];

    return (
        <TooltipProvider>
            <div className="flex h-screen w-full flex-col items-center justify-between py-5">
                <div className="flex flex-col items-center gap-2">
                    <LocalSettingsButton variant="rail" />
                    <Tooltip>
                        <TooltipTrigger asChild>
                            <Link
                                href="/"
                                aria-label="Home"
                                data-active={pathname === '/' ? 'true' : undefined}
                                className="lemma-sidebar-rail-icon text-[var(--text-primary)]"
                            >
                                <Logo size="xs" variant="mark-only" />
                            </Link>
                        </TooltipTrigger>
                        <TooltipContent side="right">Home</TooltipContent>
                    </Tooltip>

                    <Tooltip>
                        <TooltipTrigger asChild>
                            <button
                                type="button"
                                onClick={() => onSidebarOpenChange(true)}
                                className="home-sidebar-rail-button lemma-sidebar-rail-icon"
                                aria-label="Open sidebar"
                            >
                                <PanelLeftOpen className="h-4 w-4" />
                            </button>
                        </TooltipTrigger>
                        <TooltipContent side="right">Open sidebar</TooltipContent>
                    </Tooltip>

                    <div className="my-1 h-px w-6 bg-[var(--border-subtle)]" />

                    {navItems.map((item) => (
                        <RailIconLink
                            key={item.href}
                            href={item.href}
                            label={item.label}
                            kind={item.kind}
                            isActive={item.isActive}
                        />
                    ))}
                </div>

                <div className="flex flex-col items-center gap-2">
                    <AccountMenu variant="rail" side="right" align="end" />
                </div>
            </div>
        </TooltipProvider>
    );
}

export function DashboardSidebarPanel({
    isSidebarOpen,
    onSidebarOpenChange,
}: {
    isSidebarOpen: boolean;
    onSidebarOpenChange: (open: boolean) => void;
}) {
    const [isPodsOpen, setIsPodsOpen] = useState(true);

    return isSidebarOpen ? (
        <div className="pod-sidebar-panel h-full bg-[var(--pod-shell-bg)]">
            <SidebarContent
                onNavigate={() => {}}
                onClose={() => onSidebarOpenChange(false)}
                isPodsOpen={isPodsOpen}
                setIsPodsOpen={setIsPodsOpen}
            />
        </div>
    ) : (
        <div className="pod-sidebar-collapsed h-full w-10 bg-[var(--pod-shell-bg)]">
            <CollapsedSidebarRail onSidebarOpenChange={onSidebarOpenChange} />
        </div>
    );
}

export function MobileSidebarBar({
    isSidebarOpen,
    onSidebarOpenChange,
}: {
    isSidebarOpen: boolean;
    onSidebarOpenChange: (open: boolean) => void;
}) {
    const [isPodsOpen, setIsPodsOpen] = useState(true);

    return (
        <div className="fixed left-0 right-0 top-0 z-40 flex items-center justify-between px-4 py-4 md:hidden">
            <Sheet open={isSidebarOpen} onOpenChange={onSidebarOpenChange}>
                <SheetTrigger asChild>
                    <button
                        type="button"
                        className="home-sidebar-surface-button surface-panel-muted flex h-10 w-10 items-center justify-center p-0 text-[var(--text-primary)]"
                        aria-label="Open sidebar"
                    >
                        <PanelLeftOpen className="h-4 w-4" />
                    </button>
                </SheetTrigger>

                <SheetContent
                    side="left"
                    className="w-[22rem] border-r border-[var(--row-border)] bg-[var(--bg-canvas)] p-0 shadow-none sm:max-w-[22rem]"
                >
                    <SheetTitle className="sr-only">Home sidebar</SheetTitle>
                    <SidebarContent
                        onNavigate={() => onSidebarOpenChange(false)}
                        isPodsOpen={isPodsOpen}
                        setIsPodsOpen={setIsPodsOpen}
                    />
                </SheetContent>
            </Sheet>
        </div>
    );
}
