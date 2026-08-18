"use client";

// Renders a display-resource widget by iframing the backend-served, config-injected
// page. Inline-content widgets get a short-lived signed embed URL minted from
// (conversation, tool call); external widgets carry a public URL. The widget runs
// on the API origin (isolated from this app) so its SDK works.
//
// Two variants:
//   - "inline": embedded in the chat thread, rendered at its own reported height
//               up to a viewport-relative ceiling, with a fade + Expand past it.
//   - "full":   the standalone widgets/view page, full reported height, no cap.

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Maximize2, MoreHorizontal } from "@/components/ui/icons";
import { useTheme } from "next-themes";

import { Button } from "@/components/ui/button";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

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
import { StepLoader } from "@/components/brand/loader";

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
    /**
     * Max rendered height for the inline variant. Defaults to a share of the
     * viewport, so the widget shows in full unless it would swallow the thread.
     */
    maxHeight?: number;
    /**
     * The widget's own pod route. Opening it adds a tab to the pod's workspace
     * strip, because that strip is derived from the URL.
     */
    podTabHref?: string | null;
    onExpand?: () => void;
    /**
     * Answers posted up by an interactive widget (an `ask_user` rendered as one).
     *
     * The widget is model-authored HTML, so this is an untrusted message: the
     * caller is responsible for accepting only answers that name a question and
     * option it declared. Absent this prop the widget stays display-only, which
     * is what a `display_resource` widget is.
     */
    onAnswer?: (answers: Record<string, unknown>) => void;
}

/**
 * A widget is the answer, not a preview of one, so the ceiling exists only to
 * keep one card from taking the whole transcript: tall enough that anything
 * shaped like a normal widget renders whole, short enough that the next message
 * is still reachable by scrolling rather than by paging through an iframe.
 */
const INLINE_VIEWPORT_SHARE = 0.85;
const INLINE_MIN_MAX_HEIGHT = 480;

function useInlineMaxHeight(explicitMaxHeight?: number): number {
    const [viewportHeight, setViewportHeight] = useState(0);

    useEffect(() => {
        const update = () => setViewportHeight(window.innerHeight);
        update();
        window.addEventListener("resize", update);
        return () => window.removeEventListener("resize", update);
    }, []);

    if (explicitMaxHeight) return explicitMaxHeight;
    if (!viewportHeight) return INLINE_MIN_MAX_HEIGHT;
    return Math.max(INLINE_MIN_MAX_HEIGHT, Math.round(viewportHeight * INLINE_VIEWPORT_SHARE));
}

export function InlineWidget({
    podId,
    conversationId,
    toolCallId,
    externalSrc,
    title = "Widget",
    loadingMessages = [],
    variant = "inline",
    maxHeight,
    podTabHref,
    onExpand,
    onAnswer,
}: InlineWidgetProps) {
    const { resolvedTheme } = useTheme();
    const iframeRef = useRef<HTMLIFrameElement | null>(null);
    const [reportedHeight, setReportedHeight] = useState(variant === "full" ? 520 : 180);
    const [heightReported, setHeightReported] = useState(false);
    const [loadedIframeSrc, setLoadedIframeSrc] = useState<string | null>(null);
    const [loadingProgress, setLoadingProgress] = useState({ key: "", index: 0 });
    const [menuOpen, setMenuOpen] = useState(false);
    const resolvedMaxHeight = useInlineMaxHeight(maxHeight);

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

    // Held in a ref so the listener below subscribes once: `onAnswer` is a fresh
    // closure on most renders, and re-subscribing on each one would drop a
    // message posted mid-render.
    const onAnswerRef = useRef(onAnswer);
    useEffect(() => {
        onAnswerRef.current = onAnswer;
    }, [onAnswer]);

    useEffect(() => {
        const handleMessage = (event: MessageEvent) => {
            if (!iframeRef.current || event.source !== iframeRef.current.contentWindow) return;
            const data = event.data && typeof event.data === "object" ? event.data as Record<string, unknown> : {};
            if (data.type === "lemma-widget-answer") {
                // Height is cosmetic; an answer resolves a paused agent run, so
                // it is checked against the frame's own origin as well as its
                // window before it is passed on. The receiver still validates
                // the payload -- the widget's HTML is model-authored.
                if (!iframeSrc || event.origin !== new URL(iframeSrc).origin) return;
                const answers = data.answers;
                if (!answers || typeof answers !== "object" || Array.isArray(answers)) return;
                onAnswerRef.current?.(answers as Record<string, unknown>);
                return;
            }
            if (data.type !== "lemma-widget-height") return;
            const nextHeight = typeof data.height === "number" ? data.height : Number(data.height);
            if (!Number.isFinite(nextHeight)) return;
            setReportedHeight(Math.max(64, Math.min(2400, Math.round(nextHeight))));
            setHeightReported(true);
        };
        window.addEventListener("message", handleMessage);
        return () => window.removeEventListener("message", handleMessage);
    }, [iframeSrc]);

    const isInline = variant === "inline";
    const fullHeight = !heightReported ? (isInline ? 180 : 360) : reportedHeight;
    const overflows = isInline && heightReported && reportedHeight > resolvedMaxHeight;
    const renderedHeight = isInline ? Math.min(fullHeight, resolvedMaxHeight) : fullHeight;

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
                <StepLoader size="sm" />
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
            <div className="relative h-full min-h-0 overflow-hidden">
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
                        "absolute inset-0 block h-full min-h-0 w-full border-0 bg-transparent transition-opacity",
                        loading && "opacity-0",
                    )}
                />
                {loading ? (
                    <div className="absolute inset-0 flex items-center justify-center gap-2 bg-[var(--pod-main-bg)] text-sm text-[var(--text-secondary)]">
                        <StepLoader size="sm" />
                        {loadingMessage}
                    </div>
                ) : null}
            </div>
        );
    }

    const hasMenuActions = !!podTabHref || !!onExpand;

    return (
        <div className="group relative overflow-hidden">
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
                    <StepLoader size="sm" />
                    {loadingMessage}
                </div>
            ) : null}
            {hasMenuActions && !loading ? (
                // Widgets draw edge to edge and own their own chrome, so this
                // stays out of the way until the reader goes looking for it.
                <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
                    <DropdownMenuTrigger asChild>
                        <Button
                            type="button"
                            variant="quiet"
                            size="icon"
                            aria-label={`${title} actions`}
                            className={cn(
                                "absolute right-2 top-2 z-10 h-7 w-7 rounded-full",
                                // Opaque, unlike a plain quiet button: this floats
                                // over whatever the widget drew underneath it.
                                "border border-[var(--border-subtle)] bg-[var(--bg-canvas)]",
                                "shadow-[var(--shadow-xs)] hover:bg-[var(--bg-subtle)]",
                                // Inert while hidden: the widget owns this corner
                                // too, and an invisible button would eat its clicks.
                                "pointer-events-none opacity-0",
                                "group-hover:pointer-events-auto group-hover:opacity-100 focus-visible:opacity-100",
                                menuOpen && "pointer-events-auto opacity-100",
                            )}
                        >
                            <MoreHorizontal className="size-4" />
                        </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-52">
                        {podTabHref ? (
                            <DropdownMenuItem asChild>
                                <Link
                                    href={podTabHref}
                                    className="flex cursor-pointer items-center gap-2"
                                >
                                    <ArrowUpRight className="size-4 text-[var(--text-tertiary)]" />
                                    Open in new tab
                                </Link>
                            </DropdownMenuItem>
                        ) : null}
                        {onExpand ? (
                            <DropdownMenuItem
                                onClick={onExpand}
                                className="flex cursor-pointer items-center gap-2"
                            >
                                <Maximize2 className="size-4 text-[var(--text-tertiary)]" />
                                Open full view
                            </DropdownMenuItem>
                        ) : null}
                    </DropdownMenuContent>
                </DropdownMenu>
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
