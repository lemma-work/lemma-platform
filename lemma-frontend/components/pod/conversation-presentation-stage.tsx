'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Maximize2, PanelRightClose } from '@/components/ui/icons';
import { useEffect, useRef, type ReactNode } from 'react';

import { Button } from '@/components/ui/button';
import {
    buildConversationStageEmbedHref,
    buildConversationStandaloneResourceHref,
    resolveConversationStageNavigationHref,
} from '@/lib/assistant/conversation-presentation';
import { playSoundFeedback } from '@/lib/feedback/sound-feedback';

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
    const embedHref = buildConversationStageEmbedHref(resourceHref);
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

    if (!embedHref || !standaloneHref) return children;

    const title = presentationTitle(resourceHref);

    return (
        <div className="conversation-presentation-layout grid h-full min-h-0 min-w-0 overflow-hidden">
            <section className="conversation-presentation-chat min-h-0 min-w-0 overflow-hidden bg-[var(--pod-main-bg)]">
                {children}
            </section>

            <section className="conversation-presentation-stage flex h-full min-h-0 min-w-0 flex-col overflow-hidden border-l border-[color:color-mix(in_srgb,var(--border-subtle)_58%,transparent)] bg-[var(--pod-main-bg)]">
                <header className="flex h-12 shrink-0 items-center gap-2 border-b border-[color:color-mix(in_srgb,var(--border-subtle)_42%,transparent)] px-3">
                    <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        onClick={onClose}
                        className="lemma-shell-icon-button custom-focus-ring h-8 w-8 shrink-0"
                        aria-label="Back to conversation"
                        title="Back to conversation"
                    >
                        <PanelRightClose className="h-4 w-4" strokeWidth={1.8} />
                    </Button>
                    <div className="min-w-0 flex-1 truncate text-sm font-medium text-[var(--text-primary)]">
                        {title}
                    </div>
                    <Button
                        asChild
                        variant="ghost"
                        size="icon"
                        className="lemma-shell-icon-button custom-focus-ring h-8 w-8 shrink-0"
                    >
                        <Link href={standaloneHref} aria-label="Open full view" title="Open full view">
                            <Maximize2 className="h-4 w-4" strokeWidth={1.8} />
                        </Link>
                    </Button>
                </header>
                <div className="relative min-h-0 flex-1 overflow-hidden">
                    <iframe
                        key={embedHref}
                        ref={iframeRef}
                        src={embedHref}
                        title={title}
                        className="absolute inset-0 block h-full min-h-0 w-full border-0 bg-[var(--pod-main-bg)]"
                        allow="clipboard-read; clipboard-write; fullscreen"
                        referrerPolicy="strict-origin-when-cross-origin"
                        onLoad={() => playSoundFeedback('agent-open')}
                        onError={() => playSoundFeedback('load-failure')}
                    />
                </div>
            </section>
        </div>
    );
}
