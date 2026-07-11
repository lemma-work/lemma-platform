"use client";

// Renders a display-resource widget by iframing the backend-served, config-injected
// page. Inline-content widgets get a short-lived signed embed URL minted from
// (conversation, tool call); external widgets carry a public URL. The widget runs
// on the API origin (isolated from this app) so its SDK works.
//
// Two variants:
//   - "inline": embedded in the chat thread, height-capped with a fade + Expand.
//   - "full":   the standalone widgets/view page, full reported height, no cap.

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Maximize2 } from "lucide-react";
import { useTheme } from "next-themes";

import { getLemmaClient } from "@/lib/sdk/lemma-client";
import {
    buildWidgetThemeMessage,
    resolveWidgetTheme,
} from "@/lib/assistant/widget-theme";
import {
    isWidgetLoading,
    normalizeWidgetLoadingMessages,
    selectWidgetLoadingMessage,
} from "@/lib/assistant/widget-loading";
import { cn } from "@/lib/utils";

function isHttpUrl(value: string | null | undefined): string | null {
    if (!value) return null;
    try {
        const url = new URL(value);
        return url.protocol === "http:" || url.protocol === "https:" ? url.toString() : null;
    } catch {
        return null;
    }
}

function postWidgetTheme({
    iframe,
    iframeSrc,
    isContentWidget,
    resolvedTheme,
}: {
    iframe: HTMLIFrameElement | null;
    iframeSrc: string | null;
    isContentWidget: boolean;
    resolvedTheme: string | undefined;
}) {
    if (!isContentWidget || !iframeSrc || !iframe?.contentWindow) return;
    const rootStyles = window.getComputedStyle(document.documentElement);
    const bodyStyles = window.getComputedStyle(document.body);
    const theme = resolveWidgetTheme(
        resolvedTheme,
        window.matchMedia("(prefers-color-scheme: dark)").matches,
    );
    const message = buildWidgetThemeMessage({
        theme,
        readToken: (name) => rootStyles.getPropertyValue(name),
        fontFamily: bodyStyles.fontFamily,
    });
    iframe.contentWindow.postMessage(message, new URL(iframeSrc).origin);
}

export interface InlineWidgetProps {
    podId: string;
    conversationId: string | null;
    toolCallId: string;
    externalSrc?: string | null;
    title?: string;
    loadingMessages?: string[];
    variant?: "inline" | "full";
    /** Max rendered height for the inline variant before the fade + Expand kicks in. */
    maxHeight?: number;
    onExpand?: () => void;
}

const INLINE_MAX_HEIGHT = 480;

export function InlineWidget({
    podId,
    conversationId,
    toolCallId,
    externalSrc,
    title = "Widget",
    loadingMessages = [],
    variant = "inline",
    maxHeight = INLINE_MAX_HEIGHT,
    onExpand,
}: InlineWidgetProps) {
    const { resolvedTheme } = useTheme();
    const iframeRef = useRef<HTMLIFrameElement | null>(null);
    const [reportedHeight, setReportedHeight] = useState(variant === "full" ? 520 : 320);
    const [heightReported, setHeightReported] = useState(false);
    const [loadedIframeSrc, setLoadedIframeSrc] = useState<string | null>(null);
    const [loadingProgress, setLoadingProgress] = useState({ key: "", index: 0 });

    const resolvedExternalSrc = isHttpUrl(externalSrc);
    // An inline-content widget is served (and config-injected) by the backend; we
    // mint a signed embed URL and iframe it cross-origin. External widgets skip this.
    const isContentWidget = !resolvedExternalSrc;
    const embedQuery = useQuery({
        queryKey: ["widget-embed-url", podId, conversationId, toolCallId],
        queryFn: async () => {
            if (!conversationId || !toolCallId) return null;
            const result = await getLemmaClient(podId).widgets.embedUrl({
                conversation_id: conversationId,
                tool_call_id: toolCallId,
            });
            return result?.url ?? null;
        },
        enabled: isContentWidget && !!podId && !!conversationId && !!toolCallId,
        refetchOnWindowFocus: false,
    });

    const iframeSrc = resolvedExternalSrc || embedQuery.data || null;
    const embedTokenLoading = isContentWidget && embedQuery.isLoading;
    const loading = isWidgetLoading({ embedTokenLoading, iframeSrc, loadedIframeSrc });
    const loadingKey = iframeSrc || "embed-token";
    const loadingMessageIndex = loadingProgress.key === loadingKey ? loadingProgress.index : 0;
    const normalizedLoadingMessages = useMemo(
        () => normalizeWidgetLoadingMessages(loadingMessages),
        [loadingMessages],
    );
    const loadingMessage = selectWidgetLoadingMessage(
        normalizedLoadingMessages,
        loadingMessageIndex,
    );

    useEffect(() => {
        postWidgetTheme({
            iframe: iframeRef.current,
            iframeSrc,
            isContentWidget,
            resolvedTheme,
        });
    }, [iframeSrc, isContentWidget, resolvedTheme]);

    useEffect(() => {
        if (!loading || normalizedLoadingMessages.length <= 1) return;
        const intervalId = window.setInterval(() => {
            setLoadingProgress((current) => ({
                key: loadingKey,
                index: current.key === loadingKey ? current.index + 1 : 1,
            }));
        }, 1800);
        return () => window.clearInterval(intervalId);
    }, [loading, loadingKey, normalizedLoadingMessages.length]);

    useEffect(() => {
        const handleMessage = (event: MessageEvent) => {
            if (!iframeRef.current || event.source !== iframeRef.current.contentWindow) return;
            const data = event.data && typeof event.data === "object" ? event.data as Record<string, unknown> : {};
            if (data.type !== "lemma-widget-height") return;
            const nextHeight = typeof data.height === "number" ? data.height : Number(data.height);
            if (!Number.isFinite(nextHeight)) return;
            setReportedHeight(Math.max(120, Math.min(2400, Math.round(nextHeight))));
            setHeightReported(true);
        };
        window.addEventListener("message", handleMessage);
        return () => window.removeEventListener("message", handleMessage);
    }, []);

    const isInline = variant === "inline";
    const fullHeight = !heightReported ? 360 : reportedHeight;
    const overflows = isInline && heightReported && reportedHeight > maxHeight;
    const renderedHeight = isInline ? Math.min(fullHeight, maxHeight) : fullHeight;

    const handleIframeLoad = () => {
        if (!iframeSrc) return;
        setLoadedIframeSrc(iframeSrc);
        postWidgetTheme({
            iframe: iframeRef.current,
            iframeSrc,
            isContentWidget,
            resolvedTheme,
        });
    };

    if (embedTokenLoading && !iframeSrc) {
        return (
            <div className={cn(
                "flex items-center justify-center gap-2 py-8 text-sm text-[var(--text-secondary)]",
                !isInline && "min-h-full",
            )}>
                <Loader2 className="h-4 w-4 animate-spin" />
                {loadingMessage}
            </div>
        );
    }

    if (!iframeSrc) {
        return (
            <div className={cn(
                "px-3 py-3 text-xs text-[var(--text-secondary)]",
                !isInline && "min-h-full",
            )}>
                Widget unavailable.
            </div>
        );
    }

    // Side display: the iframe fills the whole pane (full height, edge to edge),
    // no chrome. Inline: height-capped with a fade + Expand when it overflows.
    if (!isInline) {
        return (
            <div className="relative h-full min-h-[360px]">
                <iframe
                    key={iframeSrc}
                    ref={iframeRef}
                    src={iframeSrc}
                    title={title}
                    allow="clipboard-read; clipboard-write; fullscreen"
                    referrerPolicy="strict-origin-when-cross-origin"
                    sandbox="allow-same-origin allow-scripts allow-forms allow-popups allow-downloads allow-modals allow-top-navigation-by-user-activation"
                    onLoad={handleIframeLoad}
                    className={cn(
                        "block h-full w-full border-0 bg-transparent transition-opacity",
                        loading && "opacity-0",
                    )}
                />
                {loading ? (
                    <div className="absolute inset-0 flex items-center justify-center gap-2 bg-[var(--pod-main-bg)] text-sm text-[var(--text-secondary)]">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        {loadingMessage}
                    </div>
                ) : null}
            </div>
        );
    }

    return (
        <div className="relative overflow-hidden">
            <iframe
                key={iframeSrc}
                ref={iframeRef}
                src={iframeSrc}
                title={title}
                height={renderedHeight}
                allow="clipboard-read; clipboard-write; fullscreen"
                referrerPolicy="strict-origin-when-cross-origin"
                sandbox="allow-same-origin allow-scripts allow-forms allow-popups allow-downloads allow-modals allow-top-navigation-by-user-activation"
                onLoad={handleIframeLoad}
                className={cn(
                    "block w-full border-0 bg-transparent transition-opacity",
                    loading && "opacity-0",
                )}
            />
            {loading ? (
                <div className="absolute inset-0 flex items-center justify-center gap-2 bg-[var(--pod-main-bg)] text-sm text-[var(--text-secondary)]">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    {loadingMessage}
                </div>
            ) : null}
            {overflows ? (
                <div className="pointer-events-none absolute inset-x-0 bottom-0 flex h-20 items-end justify-center bg-gradient-to-t from-[var(--pod-main-bg)] via-[color:color-mix(in_srgb,var(--pod-main-bg)_70%,transparent)] to-transparent pb-2">
                    {onExpand ? (
                        <button
                            type="button"
                            onClick={onExpand}
                            className="pointer-events-auto inline-flex items-center gap-1.5 rounded-full border border-[var(--border-subtle)] bg-[var(--bg-canvas)] px-3 py-1.5 text-xs font-medium text-[var(--text-primary)] shadow-[var(--shadow-xs)] transition-colors hover:bg-[var(--bg-subtle)]"
                        >
                            <Maximize2 className="h-3.5 w-3.5" />
                            Expand
                        </button>
                    ) : null}
                </div>
            ) : null}
        </div>
    );
}
