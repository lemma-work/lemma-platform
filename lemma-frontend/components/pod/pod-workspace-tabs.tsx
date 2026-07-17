'use client';

import Link from 'next/link';
import { ExternalLink, Home, MessageCircle, Plus, Sparkles, X } from '@/components/ui/icons';

import { Button } from '@/components/ui/button';
import { ProductIcon, type ProductIconKind } from '@/components/pod/product-icon';
import { getAppAccent } from '@/lib/app/app-accent';
import {
    getWorkspaceTabHref,
    type PodWorkspaceTab,
} from '@/lib/pods/workspace-tabs';
import { cn } from '@/lib/utils';
import { getConversationStatusView } from '@/lib/utils/conversations';

const routeTabKinds: Record<string, ProductIconKind> = {
    apps: 'apps',
    agents: 'agents',
    workflows: 'workflows',
    schedules: 'schedules',
    data: 'data',
    files: 'docs',
    functions: 'functions',
    connectors: 'connectors',
    surfaces: 'surfaces',
    settings: 'settings',
    conversations: 'conversation',
    forms: 'files',
    widgets: 'apps',
    recipes: 'workflows',
};

function RouteTabIcon({ routeKey, active }: { routeKey: string; active: boolean }) {
    return <ProductIcon kind={routeTabKinds[routeKey] || 'pods'} size="xs" state={active ? 'selected' : 'default'} />;
}

function ConversationActivity({ tab }: { tab: Extract<PodWorkspaceTab, { kind: 'conversation' }> }) {
    const status = getConversationStatusView(tab.status);
    if (!status.isActive && !status.isAwaiting && status.state !== 'failed') return null;

    return (
        <span
            aria-label={status.label}
            title={status.label}
            className={cn(
                'h-1.5 w-1.5 shrink-0 rounded-full bg-current',
                status.tone === 'live' && 'text-[var(--delight)]',
                status.tone === 'warning' && 'text-[var(--state-warning)]',
                status.tone === 'danger' && 'text-[var(--state-error)]',
                status.isActive && 'animate-pulse',
            )}
        />
    );
}

export function PodWorkspaceTabs({
    podId,
    tabs,
    activeTabId,
    canStartConversation,
    onClose,
}: {
    podId: string;
    tabs: PodWorkspaceTab[];
    activeTabId: string | null;
    canStartConversation: boolean;
    onClose: (tabId: string) => void;
}) {
    return (
        <nav
            className="no-scrollbar flex h-8 min-w-0 flex-1 items-center gap-0.5 overflow-x-auto"
            aria-label="Pod workspace tabs"
        >
            {tabs.map((tab) => {
                const active = activeTabId === tab.id;
                const closable = tab.kind !== 'home' && tab.kind !== 'app';
                const accent = tab.kind === 'app' ? getAppAccent(tab.resourceId) : null;

                return (
                    <div
                        key={tab.id}
                        data-state={active ? 'active' : undefined}
                        data-kind={tab.kind}
                        className={cn(
                            'group relative inline-flex h-8 shrink-0 items-center overflow-hidden rounded-md text-[var(--text-secondary)] transition-colors',
                            'hover:bg-[color:color-mix(in_srgb,var(--surface-2)_62%,transparent)] hover:text-[var(--text-primary)]',
                            'data-[state=active]:bg-[var(--surface-2)] data-[state=active]:font-medium data-[state=active]:text-[var(--text-primary)]',
                            tab.kind === 'home' ? 'min-w-[6.25rem]' : 'min-w-[7.5rem] max-w-[12rem]',
                        )}
                    >
                        <Link
                            href={getWorkspaceTabHref(tab, podId)}
                            aria-current={active ? 'page' : undefined}
                            title={tab.title}
                            className={cn(
                                'custom-focus-ring inline-flex h-full min-w-0 flex-1 items-center gap-1.5 rounded-md pl-2.5 text-sm',
                                closable || (tab.kind === 'app' && active && tab.url) ? 'pr-1' : 'pr-3',
                            )}
                        >
                            {tab.kind === 'home' ? (
                                <Home className="h-3.5 w-3.5 shrink-0" weight={active ? 'fill' : 'regular'} />
                            ) : tab.kind === 'new' ? (
                                <Sparkles className="h-3.5 w-3.5 shrink-0" weight={active ? 'fill' : 'regular'} />
                            ) : tab.kind === 'app' ? (
                                <span
                                    data-accent={accent}
                                    className="app-tile app-icon flex h-5 w-5 shrink-0 items-center justify-center rounded-md text-xs font-medium leading-none"
                                >
                                    {tab.icon || tab.title.charAt(0)}
                                </span>
                            ) : tab.kind === 'route' ? (
                                <RouteTabIcon routeKey={tab.resourceId} active={active} />
                            ) : (
                                <MessageCircle className="h-3.5 w-3.5 shrink-0" weight={active ? 'fill' : 'regular'} />
                            )}
                            <span className="min-w-0 flex-1 truncate">{tab.title}</span>
                            {tab.kind === 'conversation' ? <ConversationActivity tab={tab} /> : null}
                        </Link>

                        {tab.kind === 'app' && active && tab.url ? (
                            <a
                                href={tab.url}
                                target="_blank"
                                rel="noreferrer"
                                aria-label={`Open ${tab.title} in a new tab`}
                                title="Open in new tab"
                                className="custom-focus-ring mr-1 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-[var(--text-tertiary)] transition-colors hover:bg-[var(--surface-3)] hover:text-[var(--text-primary)]"
                            >
                                <ExternalLink className="h-3.5 w-3.5" strokeWidth={1.8} />
                            </a>
                        ) : null}

                        {closable ? (
                            <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                onClick={() => onClose(tab.id)}
                                className={cn(
                                    'custom-focus-ring mr-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-[var(--text-tertiary)] transition-colors hover:bg-[var(--surface-3)] hover:text-[var(--text-primary)]',
                                    active ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 group-focus-within:opacity-100',
                                )}
                                aria-label={`Close ${tab.title}`}
                                title={`Close ${tab.title}`}
                            >
                                <X className="h-3.5 w-3.5" strokeWidth={1.8} />
                            </Button>
                        ) : null}
                    </div>
                );
            })}

            {canStartConversation ? (
                <Link
                    href={`/pod/${podId}/conversations/new`}
                    className="lemma-shell-icon-button custom-focus-ring ml-0.5 h-7 w-7 shrink-0 text-[var(--text-tertiary)]"
                    aria-label="New"
                    title="New"
                >
                    <Plus className="h-4 w-4" strokeWidth={1.8} />
                </Link>
            ) : null}
        </nav>
    );
}
