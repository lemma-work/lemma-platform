'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTheme } from 'next-themes';
import { Copy, ExternalLink, RefreshCw, Share2 } from '@/components/ui/icons';
import { toast } from 'sonner';

import { ResourceDetailHeader } from '@/components/pod/resource-layout';
import { ResourceShareButton, type ResourceVisibilityValue } from '@/components/shared/resource-visibility';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { appIndexQueryKey } from '@/lib/hooks/use-app';
import { buildAppThemeMessage } from '@/lib/app/app-theme';
import { resolveWidgetTheme } from '@/lib/assistant/widget-theme';
import { buildResourceShareUrl } from '@/lib/assistant/conversation-presentation';
import { playSoundFeedback } from '@/lib/feedback/sound-feedback';

interface AppFrameProps {
    podId: string;
    appId?: string | null;
    appName?: string | null;
    title: string;
    url: string;
    visibility?: string | null;
    canShare?: boolean;
}

export function AppFrame({
    podId,
    appId,
    appName,
    title,
    url,
    visibility,
    canShare = false,
}: AppFrameProps) {
    const queryClient = useQueryClient();
    const { resolvedTheme } = useTheme();
    const iframeRef = useRef<HTMLIFrameElement | null>(null);
    const [frameKey, setFrameKey] = useState(0);
    const [frameLoaded, setFrameLoaded] = useState(false);
    const [frameFailed, setFrameFailed] = useState(false);

    const postAppTheme = useCallback(() => {
        const iframe = iframeRef.current;
        if (!iframe?.contentWindow) return;
        let targetOrigin: string;
        try {
            targetOrigin = new URL(url, window.location.href).origin;
        } catch {
            return;
        }
        const rootStyles = window.getComputedStyle(document.documentElement);
        const bodyStyles = window.getComputedStyle(document.body);
        const theme = resolveWidgetTheme(
            resolvedTheme,
            window.matchMedia('(prefers-color-scheme: dark)').matches,
        );
        iframe.contentWindow.postMessage(buildAppThemeMessage({
            theme,
            readToken: (name) => rootStyles.getPropertyValue(name),
            fontFamily: bodyStyles.fontFamily,
        }), targetOrigin);
    }, [resolvedTheme, url]);

    useEffect(() => {
        if (!frameLoaded) return;
        postAppTheme();
    }, [frameLoaded, postAppTheme]);

    const copyLink = async () => {
        try {
            await navigator.clipboard.writeText(url);
            toast.success('App link copied');
            playSoundFeedback('action-success');
        } catch {
            toast.error('Could not copy the app link');
        }
    };

    const reloadFrame = () => {
        setFrameLoaded(false);
        setFrameFailed(false);
        setFrameKey((current) => current + 1);
    };

    const handleShareVisibilityChange = useCallback(async (nextVisibility: ResourceVisibilityValue) => {
        if (!appName) return;

        await getLemmaClient(podId).apps.update(appName, { visibility: nextVisibility });
        void queryClient.invalidateQueries({ queryKey: appIndexQueryKey(podId) });
        void queryClient.invalidateQueries({ queryKey: ['app-page', podId] });
        toast.success('Sharing updated');
    }, [appName, podId, queryClient]);

    return (
        <div className="embedded-canvas relative flex h-full w-full flex-col overflow-hidden text-[var(--text-primary)]">
            <ResourceDetailHeader
                title={title}
                productIconKind="apps"
                backHref={`/pod/${podId}/app/pages`}
                backLabel="Apps"
                actions={(
                    <TooltipProvider>
                        <div className="flex shrink-0 items-center gap-1">
                            <Tooltip>
                                <TooltipTrigger asChild>
                                    <Button type="button" variant="ghost" size="icon" className="h-8 w-8 rounded" onClick={reloadFrame} aria-label="Reload app">
                                        <RefreshCw className="h-4 w-4" />
                                    </Button>
                                </TooltipTrigger>
                                <TooltipContent>Reload app</TooltipContent>
                            </Tooltip>
                            <Tooltip>
                                <TooltipTrigger asChild>
                                    <Button type="button" variant="ghost" size="icon" className="h-8 w-8 rounded" onClick={copyLink} aria-label="Copy app link">
                                        <Copy className="h-4 w-4" />
                                    </Button>
                                </TooltipTrigger>
                                <TooltipContent>Copy app link</TooltipContent>
                            </Tooltip>
                            {canShare ? (
                                <ResourceShareButton
                                    value={visibility}
                                    podId={podId}
                                    resourceType="app"
                                    resourceId={appId}
                                    resourceLabel="apps"
                                    resourceName={title}
                                    shareUrl={typeof window === 'undefined'
                                        ? undefined
                                        : buildResourceShareUrl(
                                            `${window.location.pathname}${window.location.search}${window.location.hash}`,
                                            window.location.origin,
                                        ) ?? undefined}
                                    onChange={handleShareVisibilityChange}
                                    disabled={!appId || !appName}
                                    trigger={({ openShare, disabled }) => (
                                        <Tooltip>
                                            <TooltipTrigger asChild>
                                                <Button
                                                    type="button"
                                                    variant="ghost"
                                                    size="icon"
                                                    className="h-8 w-8 rounded"
                                                    onClick={openShare}
                                                    disabled={disabled}
                                                    aria-label="Share app"
                                                >
                                                    <Share2 className="h-4 w-4" />
                                                </Button>
                                            </TooltipTrigger>
                                            <TooltipContent>Share app</TooltipContent>
                                        </Tooltip>
                                    )}
                                />
                            ) : null}
                            <Tooltip>
                                <TooltipTrigger asChild>
                                    <Button asChild variant="ghost" size="icon" className="h-8 w-8 rounded" aria-label="Open app in new tab">
                                        <a href={url} target="_blank" rel="noreferrer">
                                            <ExternalLink className="h-4 w-4" />
                                        </a>
                                    </Button>
                                </TooltipTrigger>
                                <TooltipContent>Open app in new tab</TooltipContent>
                            </Tooltip>
                        </div>
                    </TooltipProvider>
                )}
            />

            <div className="embedded-canvas relative min-h-0 flex-1 overflow-hidden">
                {!frameLoaded && !frameFailed ? (
                    <div className="absolute inset-0 z-10 flex items-center justify-center bg-[var(--bg-canvas)]">
                        <div className="flex items-center gap-2 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-1)] px-3 py-2 text-sm text-[var(--text-secondary)] shadow-[var(--shadow-sm)]">
                            <RefreshCw className="h-4 w-4 animate-spin" />
                            Opening app...
                        </div>
                    </div>
                ) : null}

                {frameFailed ? (
                    <div className="absolute inset-0 z-20 flex items-center justify-center bg-[var(--bg-canvas)] p-4">
                        <section className="w-full max-w-md rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-1)] p-5 shadow-[var(--shadow-sm)]">
                            <p className="text-sm font-semibold text-[var(--text-primary)]">This app cannot be shown here yet.</p>
                            <p className="mt-1 text-sm text-[var(--text-secondary)]">
                                The app may be blocking embedded views. Open it in a tab while we tune the framing policy.
                            </p>
                            <Button asChild className="mt-4 gap-2">
                                <a href={url} target="_blank" rel="noreferrer">
                                    <ExternalLink className="h-4 w-4" />
                                    Open app
                                </a>
                            </Button>
                        </section>
                    </div>
                ) : null}

                <iframe
                    ref={iframeRef}
                    key={`${url}-${frameKey}`}
                    src={url}
                    title={title}
                    className="embedded-canvas h-full w-full border-0"
                    allow="clipboard-read; clipboard-write; fullscreen"
                    referrerPolicy="strict-origin-when-cross-origin"
                    sandbox="allow-same-origin allow-scripts allow-forms allow-popups allow-downloads allow-modals allow-top-navigation-by-user-activation"
                    onLoad={() => {
                        setFrameLoaded(true);
                        setFrameFailed(false);
                        postAppTheme();
                    }}
                    onError={() => {
                        setFrameLoaded(false);
                        setFrameFailed(true);
                        playSoundFeedback('load-failure');
                    }}
                />
            </div>
        </div>
    );
}

export function AppLaunch(props: AppFrameProps) {
    return <AppFrame {...props} />;
}
