'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ArrowUpRight, X } from '@/components/ui/icons';
import { useEffect, useRef, type ReactNode } from 'react';

import { useApp } from '@/components/app/app-context';
import { AppFrame } from '@/components/app/app-launch';
import { StepLoader } from '@/components/brand/loader';
import { EmptyState } from '@/components/shared/empty-state';
import { Button } from '@/components/ui/button';
import { PanelsTopLeft } from '@/components/ui/icons';
import {
    buildConversationStageEmbedHref,
    buildConversationStandaloneResourceHref,
    conversationStageAppSlug,
    resolveConversationStageNavigationHref,
} from '@/lib/assistant/conversation-presentation';
import { formatWorkspaceAppTitle } from '@/lib/pods/workspace-tabs';
import type { AppPageRef } from '@/lib/types/app';

function decodeLabel(value: string | null | undefined): string {
    if (!value) return '';
    try {
        return decodeURIComponent(value).replace(/[_-]+/g, ' ').trim();
    } catch {
        return value.replace(/[_-]+/g, ' ').trim();
    }
}

function presentationTitle(resourceHref: string): string {
    const url = new URL(resourceHref, 'https://lemma.local');
    const parts = url.pathname.split('/').filter(Boolean);
    const section = parts[2];
    const detail = parts.at(-1);

    if (section === 'widgets') return 'Presented widget';
    if (section === 'files') return decodeLabel(url.searchParams.get('file')) || 'Presented file';
    if (section === 'data') return decodeLabel(url.searchParams.get('tab')) || 'Presented data';
    if (section === 'app') return decodeLabel(url.searchParams.get('page')) || 'Presented app';
    if (section === 'agents' || section === 'ai') return decodeLabel(detail) || 'Presented agent';
    if (section === 'functions') return decodeLabel(detail) || 'Presented function';
    if (section === 'flows') return decodeLabel(detail) || 'Presented workflow';
    if (section === 'schedules') return decodeLabel(url.searchParams.get('target')) || 'Presented schedule';
    return 'Presented view';
}

/**
 * The app itself, in the pane — no workspace around it. The stage's own header
 * already names the app and holds the close and open-in-tab controls, so the frame
 * draws without the context bar it would otherwise claim from the conversation.
 */
function StageAppBody({
    podId,
    page,
    title,
    isLoading,
}: {
    podId: string;
    page: AppPageRef | null;
    title: string;
    isLoading: boolean;
}) {
    if (page?.url) {
        return (
            <AppFrame
                podId={podId}
                appId={page.id}
                appName={page.appName || page.title}
                title={title}
                url={page.url}
                visibility={page.visibility}
                chrome="none"
            />
        );
    }

    return (
        <div className="absolute inset-0 flex items-center justify-center">
            {isLoading ? (
                <StepLoader size="sm" />
            ) : (
                <EmptyState
                    variant="region"
                    icon={<PanelsTopLeft className="h-5 w-5" />}
                    title="App unavailable"
                    description="This app didn't return a web app URL. Try opening it again from the Apps list."
                />
            )}
        </div>
    );
}

export function ConversationPresentationStage({
    podId,
    resourceHref,
    onClose,
    children,
}: {
    podId: string;
    resourceHref: string;
    onClose: () => void;
    children: ReactNode;
}) {
    const router = useRouter();
    const iframeRef = useRef<HTMLIFrameElement | null>(null);
    const { pages, isLoading: appsLoading } = useApp();
    // An app is presented in place rather than framed: the stage already sits
    // inside the pod's `AppProvider`, so it can mount the app's own frame
    // directly instead of re-loading the workspace to reach `AppFrameHost`.
    const appSlug = conversationStageAppSlug(resourceHref, podId);
    const appPage = appSlug
        ? pages.find((page) => page.slug === appSlug) ?? null
        : null;
    const embedHref = appSlug ? null : buildConversationStageEmbedHref(resourceHref);
    const standaloneHref = buildConversationStandaloneResourceHref(resourceHref);

    useEffect(() => {
        const handleMessage = (event: MessageEvent) => {
            if (event.origin !== window.location.origin) return;
            if (!iframeRef.current || event.source !== iframeRef.current.contentWindow) return;

            const nextHref = resolveConversationStageNavigationHref(event.data, podId);
            if (nextHref) router.push(nextHref);
        };

        window.addEventListener('message', handleMessage);
        return () => window.removeEventListener('message', handleMessage);
    }, [podId, router]);

    // The app's own record names it the way the tab strip does ("Ledger"), so the
    // pane stops printing the raw slug back at the reader.
    const title = appPage
        ? formatWorkspaceAppTitle(appPage.title || appPage.slug)
        : presentationTitle(resourceHref);

    const stageBody = appSlug ? (
        <StageAppBody podId={podId} page={appPage} title={title} isLoading={appsLoading} />
    ) : embedHref ? (
        <iframe
            key={embedHref}
            ref={iframeRef}
            src={embedHref}
            title={title}
            className="absolute inset-0 block h-full min-h-0 w-full border-0 bg-[var(--pod-main-bg)]"
            allow="clipboard-read; clipboard-write; fullscreen"
            referrerPolicy="strict-origin-when-cross-origin"
        />
    ) : null;

    if (!stageBody || !standaloneHref) return children;

    return (
        <div className="conversation-presentation-layout grid h-full min-h-0 min-w-0 overflow-hidden">
            <section className="conversation-presentation-chat min-h-0 min-w-0 overflow-hidden bg-[var(--pod-main-bg)]">
                {children}
            </section>

            <section className="conversation-presentation-stage flex h-full min-h-0 min-w-0 flex-col overflow-hidden border-l border-[color:color-mix(in_srgb,var(--border-subtle)_58%,transparent)] bg-[var(--pod-main-bg)]">
                <header className="flex h-12 shrink-0 items-center gap-2 border-b border-[color:color-mix(in_srgb,var(--border-subtle)_42%,transparent)] px-3">
                    <Button
                        type="button"
                        variant="quiet"
                        size="icon"
                        onClick={onClose}
                        className="lemma-shell-icon-button custom-focus-ring h-8 w-8 shrink-0"
                        aria-label="Close"
                        title="Close"
                    >
                        <X className="h-4 w-4" strokeWidth={1.8} />
                    </Button>
                    <div className="min-w-0 flex-1 truncate text-sm font-medium text-[var(--text-primary)]">
                        {title}
                    </div>
                    <Button
                        asChild
                        variant="quiet"
                        size="icon"
                        className="lemma-shell-icon-button custom-focus-ring h-8 w-8 shrink-0"
                    >
                        <Link href={standaloneHref} aria-label="Open in new tab" title="Open in new tab">
                            <ArrowUpRight className="h-4 w-4" strokeWidth={1.8} />
                        </Link>
                    </Button>
                </header>
                <div className="relative min-h-0 flex-1 overflow-hidden">
                    {stageBody}
                </div>
            </section>
        </div>
    );
}
