"use client";

import { ApiError } from "lemma-sdk";
import { use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import dynamic from "next/dynamic";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { AIAssistantProvider, useAIAssistant } from "@/components/ai/ai-assistant-context";
import { HelpMenu } from "@/components/education/help-menu";
import { InlineLoader, PageLoader } from "@/components/brand/loader";
import { AppProvider, useApp } from "@/components/app/app-context";
import { AppFrameHost } from "@/components/app/app-frame-host";
import { PodWorkspaceTabs } from "@/components/pod/pod-workspace-tabs";
import { usePodWorkspaceTabs } from "@/components/pod/use-pod-workspace-tabs";
import { useOrganization } from "@/components/dashboard/org-context";
import { PodTopbarProvider, type PodTopbarState } from "@/components/pod/pod-topbar-context";
import { MobileSidebarDrawer } from "@/components/pod/mobile-sidebar-drawer";
import { PodLayoutProvider, usePodLayout } from "@/components/pod/pod-layout-context";
import { WorkspaceSidebar } from "@/components/pod/workspace-sidebar";
import { Button } from "@/components/ui/button";
import { ArrowLeft, PanelLeftOpen, Settings, X } from "@/components/ui/icons";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { getLemmaClient } from "@/lib/sdk/lemma-client";
import { usePod } from "@/lib/hooks/use-pods";
import { usePodContext } from "@/lib/hooks/use-pod-context";
import { usePodAccess } from "@/lib/hooks/use-pod-access";
import { clearLastOpenedPodId, writeLastOpenedPodId } from "@/lib/pods/last-opened-pod";
import { getWorkspaceTabAfterClose, getWorkspaceTabHref } from "@/lib/pods/workspace-tabs";
import {
    buildConversationStandaloneResourceHref,
    CONVERSATION_STAGE_EMBED_PARAM,
    CONVERSATION_STAGE_EMBED_VALUE,
} from "@/lib/assistant/conversation-presentation";
import type { PodRoutePolicyKey } from "@/lib/authz/pod-permissions";
import { cn } from "@/lib/utils";
import type { Pod } from "@/lib/types";
import type { PodContext } from "@/lib/types/ai";

const PodAssistantSidebar = dynamic(
    () => import("@/components/ai/pod-assistant").then((module) => module.PodAssistantSidebar)
);

interface PodHeaderData {
    id: string;
    name: string;
    description?: string | null;
    organization_id?: string;
    icon_url?: string | null;
}

type PodAccessState = "idle" | "checking" | "denied" | "not_found" | "error";

type SearchParamsReader = {
    get(name: string): string | null;
};

interface PodJoinRequestStatusResponse {
    status: "PENDING" | "APPROVED" | "REJECTED" | string;
}

function formatDisplayName(value: string | null | undefined) {
    const cleaned = (value || "")
        .replace(/[_-]+/g, " ")
        .replace(/\s+/g, " ")
        .trim();

    if (!cleaned) return "Untitled pod";

    return cleaned
        .split(" ")
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(" ");
}

function parseConversationMetadataParam(value: string | null): Record<string, unknown> | undefined {
    if (!value) return undefined;
    try {
        const parsed = JSON.parse(value);
        return parsed && typeof parsed === "object" && !Array.isArray(parsed)
            ? parsed as Record<string, unknown>
            : undefined;
    } catch {
        return undefined;
    }
}

function getPodSectionLabel(podId: string, pathname: string) {
    const section = pathname.replace(`/pod/${podId}`, "").split("/").filter(Boolean)[0];

    switch (section) {
        case undefined:
            return "Home";
        case "ai":
        case "agents":
            return "Agents";
        case "flows":
            return "Workflows";
        case "schedules":
            return "Schedules";
        case "data":
            return "Data";
        case "files":
        case "docs":
            return "Docs";
        case "channels":
        case "surfaces":
            return "Surfaces";
        case "connectors":
            return "Connectors";
        case "settings":
            return "Settings";
        case "conversations":
            return "Conversations";
        case "functions":
            return "Functions";
        case "forms":
            return "Forms";
        case "widgets":
            return "Widgets";
        case "app":
            return "Apps";
        case "recipes":
        case "kits":
            return "Recipes";
        default:
            return formatDisplayName(section);
    }
}

function getPodRoutePolicyKey(podId: string, pathname: string): PodRoutePolicyKey | null {
    const section = pathname.replace(`/pod/${podId}`, "").split("/").filter(Boolean)[0];

    switch (section) {
        case undefined:
            return "home";
        case "data":
        case "datastores":
            return "data";
        case "files":
        case "docs":
            return "files";
        case "ai":
        case "agents":
            return "agents";
        case "functions":
            return "functions";
        case "flows":
            return "workflows";
        case "schedules":
            return "schedules";
        case "connectors":
            return "connectors";
        case "app":
            return "apps";
        case "channels":
        case "surfaces":
            return "surfaces";
        case "conversations":
            return "conversations";
        case "settings":
            return "settings";
        case "forms":
        case "widgets":
        case "kits":
        case "recipes":
            return null;
        default:
            return "home";
    }
}

function safeDecodeSegment(value: string | null | undefined) {
    if (!value) return "";
    try {
        return decodeURIComponent(value);
    } catch {
        return value;
    }
}

function getPathBasename(value: string | null | undefined) {
    const decoded = safeDecodeSegment(value);
    return decoded.split("/").filter(Boolean).at(-1) || decoded;
}

function appendAssistantConversationParam(href: string, assistantConversationId: string | null) {
    if (!assistantConversationId) return href;
    const [withoutHash, hash = ""] = href.split("#");
    const [path, query = ""] = withoutHash.split("?");
    const params = new URLSearchParams(query);
    if (!params.has("assistantConversationId")) {
        params.set("assistantConversationId", assistantConversationId);
    }
    const nextQuery = params.toString();
    return `${path}${nextQuery ? `?${nextQuery}` : ""}${hash ? `#${hash}` : ""}`;
}

function getPodScreenLabel(podId: string, pathname: string, searchParams: SearchParamsReader) {
    const parts = pathname.replace(`/pod/${podId}`, "").split("/").filter(Boolean);
    const [section, detail] = parts;

    if (!section) return "Home";

    if ((section === "agents" || section === "ai") && detail) {
        if (detail === "new") return "New agent";
        return formatDisplayName(safeDecodeSegment(detail));
    }

    if (section === "flows" && detail) {
        if (detail === "new") return "New workflow";
        if (parts.includes("runs")) return "Workflow run";
        return formatDisplayName(safeDecodeSegment(detail));
    }

    if (section === "functions" && detail) {
        if (detail === "new") return "New function";
        return formatDisplayName(safeDecodeSegment(detail));
    }

    if (section === "schedules" && detail === "new") return "New schedule";

    if (section === "forms" && detail === "view") return "Agent Needs Your Input";

    if (section === "widgets" && detail === "view") return "Presented Widget";

    if ((section === "surfaces" || section === "channels") && detail === "new") {
        return "New surface";
    }

    if (section === "files" || section === "docs") {
        const file = getPathBasename(searchParams.get("file"));
        if (file) return formatDisplayName(file.replace(/\.(mdx?|markdown)$/i, ""));
        const folder = getPathBasename(searchParams.get("folder"));
        if (folder) return formatDisplayName(folder);
    }

    if (section === "data") {
        const table = searchParams.get("tab");
        if (table) return formatDisplayName(table);
    }

    if (section === "app" && pathname.startsWith(`/pod/${podId}/app/view`)) {
        const page = searchParams.get("page");
        if (page) return formatDisplayName(page);
    }

    return getPodSectionLabel(podId, pathname);
}

function PodShell({
    pod,
    pathname,
    children,
}: {
    pod: PodHeaderData;
    pathname: string;
    children: React.ReactNode;
}) {
    const router = useRouter();
    const searchParams = useSearchParams();
    const {
        openedConversationId,
        conversations,
        isOpen: isAssistantOpen,
        isReady,
        openAssistant,
        closeAssistant,
        sendMessage,
    } = useAIAssistant();
    const podAccess = usePodAccess(pod.id);
    const {
        isCompact,
        navPresentation,
        isMobileNavOpen,
        toggleNav,
        openNav,
        closeNav,
        setMobileNavOpen,
        assistantPresentation,
        isFocusRoute,
        assistantDockWidth,
        setAssistantDockWidth,
    } = usePodLayout();
    const { pages: appPages, isLoading: appPagesLoading } = useApp();
    const [topbar, setTopbar] = useState<PodTopbarState>({});
    const [isPresentedClosing, setIsPresentedClosing] = useState(false);
    const [conversationStageFrameContext, setConversationStageFrameContext] = useState<"checking" | "embedded" | "top-level">("checking");
    const handledAssistantMessageRef = useRef<string | null>(null);
    const assistantMessage = searchParams.get("assistantMessage");
    const conversationInstructions = searchParams.get("conversationInstructions");
    const conversationMetadata = searchParams.get("conversationMetadata");
    const parsedConversationMetadata = useMemo(
        () => parseConversationMetadataParam(conversationMetadata),
        [conversationMetadata]
    );
    const assistantConversationId = searchParams.get("assistantConversationId");
    const searchParamsString = searchParams.toString();
    const currentHref = searchParamsString ? `${pathname}?${searchParamsString}` : pathname;
    const currentSearchParams = useMemo(() => new URLSearchParams(searchParamsString), [searchParamsString]);

    // Close the off-canvas drawer when the route changes so a tap-through nav
    // doesn't leave it pinned open over the new screen.
    const [mobileSidebarHref, setMobileSidebarHref] = useState(currentHref);
    if (mobileSidebarHref !== currentHref) {
        setMobileSidebarHref(currentHref);
        if (isMobileNavOpen) setMobileNavOpen(false);
    }

    const currentScreenLabel = useMemo(
        () => getPodScreenLabel(pod.id, pathname, currentSearchParams),
        [currentSearchParams, pathname, pod.id]
    );
    const podDisplayName = formatDisplayName(pod.name);
    const isWorkflowRoute = pathname.startsWith(`/pod/${pod.id}/flows/`);
    const isWorkflowRunRoute = isWorkflowRoute && pathname.includes("/runs/");
    const isWorkflowEditRoute =
        isWorkflowRoute &&
        pathname !== `/pod/${pod.id}/flows/new` &&
        !pathname.includes("/runs/") &&
        searchParams.get("mode") === "edit";
    const isPodHome = pathname === `/pod/${pod.id}` || pathname === `/pod/${pod.id}/`;
    const isAppViewRoute = pathname.startsWith(`/pod/${pod.id}/app/view`);
    const isConversationRoute = pathname === `/pod/${pod.id}/conversations` || pathname.startsWith(`/pod/${pod.id}/conversations/`);
    const appSlug = isAppViewRoute ? searchParams.get("page") : null;
    const isConversationStageEmbed =
        searchParams.get(CONVERSATION_STAGE_EMBED_PARAM) === CONVERSATION_STAGE_EMBED_VALUE;
    const sectionLabel = getPodSectionLabel(pod.id, pathname);
    const topbarRouteTitle = typeof topbar.title === "string" ? topbar.title.trim() : "";
    const workspaceTabs = usePodWorkspaceTabs({
        enabled: !isConversationStageEmbed,
        podId: pod.id,
        pathname,
        currentHref,
        routeTitle: topbarRouteTitle && topbarRouteTitle !== sectionLabel
            ? topbarRouteTitle
            : currentScreenLabel,
        appSlug,
        pages: appPages,
        appsLoaded: !appPagesLoading,
        conversations,
        openedConversationId,
    });
    const embeddedOpenAppSlugs = useMemo(
        () => appSlug && !workspaceTabs.openAppSlugs.includes(appSlug)
            ? [...workspaceTabs.openAppSlugs, appSlug]
            : workspaceTabs.openAppSlugs,
        [appSlug, workspaceTabs.openAppSlugs],
    );
    // Every non-fullscreen pod route is a workspace. The route decides which tab
    // is active; the page's own title and actions live in the contextual row.
    // A stable key for the "which app-workspace view is this" decision below:
    // changes when you switch apps, land on home, or leave for another section.
    const appNavIntent = isAppViewRoute
        ? `app:${searchParams.get("page") || ""}`
        : isPodHome
            ? "home"
            : "other";
    const isPresentedInteractionRoute =
        pathname === `/pod/${pod.id}/widgets/view`;
    const presentedConversationId = assistantConversationId || searchParams.get("conversationId");
    const presentedConversationHref = presentedConversationId
        ? `/pod/${pod.id}/conversations/${encodeURIComponent(presentedConversationId)}`
        : `/pod/${pod.id}/conversations/new`;
    const canShowAssistantSidebar = !isPodHome && !isFocusRoute;
    const assistantDocked = assistantPresentation === "docked";
    // Nav presentation is owned by the layout context (one source of truth) and
    // is controlled at the nav's own edge — a collapse button in the sidebar
    // header (desktop) and an expand button on the collapsed rail. The shell no
    // longer scatters duplicate nav toggles across the topbar and the assistant.
    const showWorkspaceSidebar = navPresentation === "expanded";
    const showCollapsedRail = navPresentation === "rail";
    const sidebarSlotClassName = showWorkspaceSidebar
        ? "pod-sidebar-slot hidden h-full w-60 shrink-0 overflow-hidden md:block"
        : "pod-sidebar-slot hidden h-full w-10 shrink-0 overflow-hidden md:block";
    // On compact viewports the nav is an off-canvas drawer, reached by a single
    // hamburger in the topbar (resource/presented routes render the shell topbar).
    const showMobileNavTrigger = isCompact && !isPresentedInteractionRoute;
    const routePolicyKey = getPodRoutePolicyKey(pod.id, pathname);
    const canUseCurrentRoute = !routePolicyKey || podAccess.canAccessRoute(routePolicyKey);
    const canUseSettings = podAccess.canAccessRoute("settings");
    const topbarContextValue = useMemo(() => ({
        setTopbar,
    }), [setTopbar]);
    // Focus routes own the full surface and have their own chrome, so fullscreen
    // is no longer gated by the assistant — on focus routes the assistant yields
    // to a launcher instead of docking, so it can never double up the topbar.
    const isFullscreenSurface =
        isWorkflowEditRoute || isWorkflowRunRoute || (!isWorkflowRoute && Boolean(topbar.fullscreen));
    const handleWorkspaceTabClose = useCallback((tabId: string) => {
        const fallbackTab = getWorkspaceTabAfterClose(workspaceTabs.tabs, tabId);
        workspaceTabs.closeTab(tabId);
        if (workspaceTabs.activeTabId === tabId) {
            router.replace(getWorkspaceTabHref(fallbackTab, pod.id));
        }
    }, [pod.id, router, workspaceTabs]);
    const backTarget = topbar.backHref && topbar.backLabel
        ? { href: topbar.backHref, label: topbar.backLabel }
        : null;

    useEffect(() => {
        writeLastOpenedPodId(pod.id);
    }, [pod.id]);

    useEffect(() => {
        let cancelled = false;
        window.queueMicrotask(() => {
            if (cancelled) return;

            let isEmbeddedFrame = true;
            try {
                isEmbeddedFrame = window.self !== window.top;
            } catch {
                // Cross-origin parents are still embedded contexts.
            }

            setConversationStageFrameContext(isEmbeddedFrame ? "embedded" : "top-level");
            if (!isConversationStageEmbed || isEmbeddedFrame) return;

            const standaloneHref = buildConversationStandaloneResourceHref(currentHref);
            router.replace(standaloneHref ?? pathname, { scroll: false });
        });

        return () => {
            cancelled = true;
        };
    }, [currentHref, isConversationStageEmbed, pathname, router]);

    // Selecting an app collapses the workspace nav so the app gets the full
    // surface; landing back on home restores it. Desktop only — compact viewports
    // use an off-canvas drawer this must not fight. Keyed on the view intent so it
    // fires on genuine transitions (and app→app switches), leaving a user's manual
    // toggle within the same view untouched.
    useEffect(() => {
        if (isCompact) return;
        if (appNavIntent.startsWith("app:")) {
            closeNav();
        } else if (appNavIntent === "home") {
            openNav();
        }
    }, [appNavIntent, isCompact, closeNav, openNav]);

    useEffect(() => {
        if (!assistantMessage || !isReady) return;
        if (isConversationRoute) return;

        const key = `${pathname}?${assistantMessage}:${conversationInstructions || ""}:${conversationMetadata || ""}`;
        if (handledAssistantMessageRef.current === key) return;
        handledAssistantMessageRef.current = key;

        const nextParams = new URLSearchParams(searchParams.toString());
        nextParams.delete("assistantMessage");
        const nextQuery = nextParams.toString();

        if (isPodHome) {
            const conversationParams = new URLSearchParams(nextParams.toString());
            conversationParams.set("assistantMessage", assistantMessage);
            router.replace(`/pod/${pod.id}/conversations/new?${conversationParams.toString()}`);
            return;
        }

        void (async () => {
            if (isConversationRoute) {
                closeAssistant();
            } else {
                openAssistant();
            }
            await sendMessage(assistantMessage, {
                forceNewConversation: true,
                instructions: conversationInstructions || undefined,
                conversationMetadata: parsedConversationMetadata,
            });
            router.replace(nextQuery ? `${pathname}?${nextQuery}` : pathname);
        })();
    }, [assistantMessage, closeAssistant, conversationInstructions, conversationMetadata, isConversationRoute, isPodHome, isReady, openAssistant, parsedConversationMetadata, pathname, pod.id, router, searchParams, sendMessage]);

    useEffect(() => {
        if (isPodHome && isAssistantOpen) {
            closeAssistant();
        }
    }, [closeAssistant, isAssistantOpen, isPodHome]);

    useEffect(() => {
        let cancelled = false;
        window.queueMicrotask(() => {
            if (!cancelled) {
                setIsPresentedClosing(false);
            }
        });
        return () => {
            cancelled = true;
        };
    }, [currentHref]);

    const handlePresentedClose = useCallback(() => {
        setIsPresentedClosing(true);
        router.push(presentedConversationHref);
    }, [presentedConversationHref, router]);

    const clearAssistantSideViewUrl = useCallback(() => {
        closeAssistant({ skipUrlSync: true });

        const nextParams = new URLSearchParams(searchParams.toString());
        nextParams.delete("assistantConversationId");
        nextParams.delete("assistant");
        nextParams.delete("presentation");
        const nextQuery = nextParams.toString();

        router.replace(nextQuery ? `${pathname}?${nextQuery}` : pathname, { scroll: false });
    }, [closeAssistant, pathname, router, searchParams]);

    if (podAccess.isLoading) {
        return <PageLoader />;
    }

    if (!canUseCurrentRoute) {
        return (
            <div className="flex h-screen overflow-hidden bg-[var(--pod-shell-bg)] text-[var(--text-primary)]">
                <div className={sidebarSlotClassName}>
                    {showWorkspaceSidebar ? (
                        <div className="pod-sidebar-panel h-full">
                            <WorkspaceSidebar podId={pod.id} podName={podDisplayName} podIconUrl={pod.icon_url} />
                        </div>
                    ) : null}
                </div>
                <main className="pod-workspace-main flex min-w-0 flex-1 items-center justify-center overflow-hidden border-l border-[color:color-mix(in_srgb,var(--border-subtle)_62%,transparent)] bg-[var(--pod-main-bg)] px-4">
                    <div className="surface-panel w-full max-w-lg p-6 text-center sm:p-8">
                        <h2 className="mb-2 font-display text-xl font-semibold text-[var(--text-primary)]">No access to this area</h2>
                        <p className="text-sm text-[var(--text-secondary)]">
                            This pod is available to you, but this section is outside your current permissions.
                        </p>
                        <Button asChild className="mt-5">
                            <Link href={`/pod/${pod.id}`}>Back to pod home</Link>
                        </Button>
                    </div>
                </main>
            </div>
        );
    }

    if (
        conversationStageFrameContext === "embedded"
        || (isConversationStageEmbed && conversationStageFrameContext !== "top-level")
    ) {
        return (
            <div className="h-screen overflow-hidden bg-[var(--pod-main-bg)] text-[var(--text-primary)]">
                <PodTopbarProvider value={topbarContextValue}>
                    <main className="relative h-full min-h-0 w-full overflow-hidden">
                        <div className="h-full min-h-0 w-full overflow-hidden">
                            {children}
                        </div>
                        <AppFrameHost
                            podId={pod.id}
                            visible={isAppViewRoute}
                            activeSlug={appSlug}
                            openAppSlugs={embeddedOpenAppSlugs}
                            canUpdateApp={podAccess.can("app.update")}
                        />
                    </main>
                </PodTopbarProvider>
            </div>
        );
    }

    if (isFullscreenSurface) {
        return (
            <div className="h-screen overflow-hidden bg-[var(--pod-shell-bg)] text-[var(--text-primary)]">
                <PodTopbarProvider value={topbarContextValue}>
                    <main className="h-full min-h-0 w-full overflow-hidden">
                        {children}
                    </main>
                </PodTopbarProvider>
            </div>
        );
    }

    return (
        <div className="flex h-screen overflow-hidden bg-[var(--pod-shell-bg)] text-[var(--text-primary)]">
            <MobileSidebarDrawer isOpen={isMobileNavOpen} onClose={() => setMobileNavOpen(false)}>
                <div className="h-full bg-[var(--pod-shell-bg)]">
                    <WorkspaceSidebar
                        podId={pod.id}
                        podName={podDisplayName}
                        podIconUrl={pod.icon_url}
                        onCollapse={() => setMobileNavOpen(false)}
                    />
                </div>
            </MobileSidebarDrawer>
            <div className={sidebarSlotClassName}>
                {showWorkspaceSidebar ? (
                    <div className="pod-sidebar-panel h-full">
                        <WorkspaceSidebar
                            podId={pod.id}
                            podName={podDisplayName}
                            podIconUrl={pod.icon_url}
                            onCollapse={isPodHome ? undefined : closeNav}
                        />
                    </div>
                ) : showCollapsedRail ? (
                    <div className="pod-sidebar-collapsed flex h-full w-10 flex-col bg-[var(--pod-shell-bg)]">
                        <div className="flex h-12 shrink-0 items-center justify-center border-b border-[color:color-mix(in_srgb,var(--border-subtle)_32%,transparent)]">
                            <button
                                type="button"
                                onClick={openNav}
                                className="lemma-shell-icon-button custom-focus-ring h-7 w-7 text-[var(--text-tertiary)] hover:scale-[1.03]"
                                aria-label="Open sidebar"
                                title="Open sidebar"
                            >
                                <PanelLeftOpen className="h-4 w-4" strokeWidth={1.8} />
                            </button>
                        </div>
                    </div>
                ) : null}
            </div>

            {canShowAssistantSidebar && assistantPresentation !== "closed" ? (
                <PodAssistantSidebar
                    presentationMode={isPresentedInteractionRoute}
                    onClose={clearAssistantSideViewUrl}
                    presentation={assistantPresentation}
                    dockWidth={assistantDockWidth}
                    onDockWidthChange={setAssistantDockWidth}
                />
            ) : null}

            <main className="pod-workspace-main flex min-w-0 flex-1 flex-col overflow-hidden">
                <header className="pod-shell-topbar pod-workspace-tabbar flex h-12 shrink-0 items-center justify-between gap-4 bg-[var(--pod-main-bg)] px-3">
                        <div className="flex h-8 min-w-0 flex-1 items-center gap-2">
                            {showMobileNavTrigger ? (
                                <button
                                    type="button"
                                    onClick={toggleNav}
                                    className="lemma-shell-icon-button custom-focus-ring h-7 w-7 shrink-0 text-[var(--text-tertiary)]"
                                    aria-label="Open navigation"
                                    title="Open navigation"
                                >
                                    <PanelLeftOpen className="h-4 w-4" strokeWidth={1.8} />
                                </button>
                            ) : null}
                            <PodWorkspaceTabs
                                podId={pod.id}
                                tabs={workspaceTabs.tabs}
                                activeTabId={workspaceTabs.activeTabId}
                                canStartConversation={podAccess.can("conversation.write")}
                                onClose={handleWorkspaceTabClose}
                            />
                        </div>
                        <div className="pod-shell-topbar-actions flex h-7 shrink-0 items-center gap-1.5">
                            <TooltipProvider>
                                <HelpMenu />
                                {canUseSettings ? (
                                    <Tooltip>
                                        <TooltipTrigger asChild>
                                            <Link
                                                href={`/pod/${pod.id}/settings`}
                                                className="lemma-shell-icon-button custom-focus-ring"
                                                aria-label="Pod settings"
                                            >
                                                <Settings className="h-4 w-4" strokeWidth={1.8} />
                                            </Link>
                                        </TooltipTrigger>
                                        <TooltipContent>Pod settings</TooltipContent>
                                    </Tooltip>
                                ) : null}
                            </TooltipProvider>
                        </div>
                </header>
                {!isPodHome && !isConversationRoute && !isAppViewRoute ? (
                    <header
                        className={cn(
                            "pod-shell-topbar pod-shell-contextbar flex h-12 shrink-0 items-center justify-between gap-4 bg-[var(--pod-main-bg)] px-4",
                            isPresentedInteractionRoute && "pod-presented-resource-topbar"
                        )}
                    >
                        {isPresentedInteractionRoute ? (
                            <div className="pod-shell-topbar-actions flex h-8 w-full items-center justify-end">
                                <div className="pod-shell-topbar-actions flex h-8 shrink-0 items-center gap-1.5">
                                    <button
                                        type="button"
                                        onClick={handlePresentedClose}
                                        disabled={isPresentedClosing}
                                        className="lemma-shell-icon-button custom-focus-ring disabled:pointer-events-none disabled:opacity-50"
                                        aria-label="Return to conversation"
                                        title="Return to conversation"
                                    >
                                        <X className="h-4 w-4" strokeWidth={1.8} />
                                    </button>
                                </div>
                            </div>
                        ) : (
                            <>
                        <div key={`${currentHref}:topbar-title`} className="pod-shell-topbar-title-cluster flex h-7 min-w-0 flex-1 items-center gap-2">
                            {backTarget ? (
                                <Link
                                    href={appendAssistantConversationParam(backTarget.href, assistantConversationId)}
                                    className="lemma-shell-link lemma-shell-link-sm hidden sm:inline-flex"
                                >
                                    <ArrowLeft className="h-3.5 w-3.5" />
                                    {backTarget.label}
                                </Link>
                            ) : null}
                            <div className="pod-shell-topbar-title min-w-0 truncate text-base font-semibold leading-7 text-[var(--text-primary)]">
                                {topbar.title || currentScreenLabel || sectionLabel}
                            </div>
                            {topbar.switcher ? <span className="shrink-0">{topbar.switcher}</span> : null}
                            {topbar.meta ? (
                                <>
                                    <span className="hidden h-4 w-px bg-[var(--border-subtle)] lg:block" />
                                    <div className="hidden min-w-0 truncate text-xs text-[var(--text-tertiary)] lg:inline-flex">
                                        {topbar.meta}
                                    </div>
                                </>
                            ) : null}
                        </div>
                        {topbar.tabs ? <div className="hidden min-w-0 shrink overflow-x-auto xl:block">{topbar.tabs}</div> : null}
                        <div key={`${currentHref}:topbar-actions`} className="pod-shell-topbar-actions flex h-7 shrink-0 items-center gap-1.5">
                            {topbar.actions}
                        </div>
                            </>
                        )}
                    </header>
                ) : null}
                <div className="relative flex min-h-0 flex-1 flex-col">
                <PodTopbarProvider value={topbarContextValue}>
                    <div
                        className={cn(
                            "pod-page-scroll min-h-0 flex-1",
                            isConversationRoute ? "overflow-hidden" : "overflow-auto",
                            isPodHome
                                ? "bg-[var(--pod-main-bg)] shadow-none"
                                : isConversationRoute
                                    ? "border-l border-[color:color-mix(in_srgb,var(--border-subtle)_62%,transparent)] bg-[var(--pod-main-bg)]"
                                    : "border-l border-[color:color-mix(in_srgb,var(--border-subtle)_62%,transparent)] bg-[var(--pod-main-bg)]",
                            assistantDocked && "pod-page-scroll-assistant-open"
                        )}
                    >
                        <div
                            key={pathname}
                            className={cn(
                                "pod-page-surface",
                                isConversationRoute && "pod-conversation-workspace-surface",
                            )}
                        >
                            {isPresentedInteractionRoute && isPresentedClosing ? (
                                <main className="flex min-h-full items-center justify-center bg-[var(--pod-main-bg)] p-8">
                                    <InlineLoader size="sm" label="Opening conversation" />
                                </main>
                            ) : (
                                children
                            )}
                        </div>
                    </div>
                </PodTopbarProvider>
                <AppFrameHost
                    podId={pod.id}
                    visible={isAppViewRoute}
                    activeSlug={appSlug}
                    openAppSlugs={workspaceTabs.openAppSlugs}
                    canUpdateApp={podAccess.can("app.update")}
                />
                </div>
            </main>
        </div>
    );
}

function PodAssistantScope({
    pod,
    children,
}: {
    pod: PodHeaderData;
    children: React.ReactNode;
}) {
    const [shouldLoadPodContext, setShouldLoadPodContext] = useState(false);
    const { context: loadedPodContext } = usePodContext(pod.id, { enabled: shouldLoadPodContext });
    const pathname = usePathname();
    const searchParams = useSearchParams();
    // A conversation route may name the agent it targets via `?agent=`, so the
    // "message this agent" composer starts a chat scoped to that agent rather than
    // the pod default. Only applies on conversation routes; absent elsewhere.
    const scopedAgentName = useMemo(() => {
        if (!/\/conversations(?:\/|$)/.test(pathname)) return null;
        return searchParams.get("agent");
    }, [pathname, searchParams]);
    const conversationScopeOverride = useMemo(
        () => (scopedAgentName ? { agentName: scopedAgentName } : undefined),
        [scopedAgentName],
    );
    const fallbackPodContext = useMemo<PodContext>(() => ({
        pod: pod as Pod,
        agents: [],
        functions: [],
        flows: [],
        datastores: [],
        appPages: [],
        connectedAccounts: [],
    }), [pod]);

    return (
        <AIAssistantProvider
            podContext={loadedPodContext ?? fallbackPodContext}
            enabled
            conversationScopeOverride={conversationScopeOverride}
            onOpenAssistant={() => setShouldLoadPodContext(true)}
        >
            {children}
        </AIAssistantProvider>
    );
}

export default function PodLayout({
    children,
    params,
}: {
    children: React.ReactNode;
    params: Promise<{ id: string }>;
}) {
    const { id } = use(params);
    const pathname = usePathname();
    const router = useRouter();
    const searchParams = useSearchParams();
    const cameFromRoot = searchParams.get("fromRoot") === "1";
    const searchParamsString = searchParams.toString();
    const { data: pod, isLoading, error } = usePod(id);
    const { currentOrg, setCurrentOrg, organizations } = useOrganization();
    const [podAccessState, setPodAccessState] = useState<PodAccessState>("idle");
    const [joinRequest, setJoinRequest] = useState<PodJoinRequestStatusResponse | null>(null);
    const [isCheckingJoinRequest, setIsCheckingJoinRequest] = useState(false);
    const [isSubmittingJoinRequest, setIsSubmittingJoinRequest] = useState(false);
    const [accessError, setAccessError] = useState<string | null>(null);
    const accessCheckKeyRef = useRef<string | null>(null);

    const loadMyJoinRequest = useCallback(async () => {
        setIsCheckingJoinRequest(true);

        try {
            const request = await getLemmaClient().request<PodJoinRequestStatusResponse | null>(
                "GET",
                `/pods/${encodeURIComponent(id)}/join-requests/me`
            );
            setJoinRequest(request);
        } catch (joinRequestError) {
            if (
                joinRequestError instanceof ApiError &&
                (joinRequestError.statusCode === 404 ||
                    joinRequestError.statusCode === 403 ||
                    joinRequestError.code === "INSUFFICIENT_ROLE")
            ) {
                setJoinRequest(null);
                return;
            }

            setAccessError(
                joinRequestError instanceof Error
                    ? joinRequestError.message
                    : "Failed to check your invite request status."
            );
        } finally {
            setIsCheckingJoinRequest(false);
        }
    }, [id]);

    const handleRequestInvite = useCallback(async () => {
        if (isSubmittingJoinRequest || isCheckingJoinRequest || joinRequest?.status === "PENDING") {
            return;
        }

        setIsSubmittingJoinRequest(true);
        setAccessError(null);

        try {
            const request = await getLemmaClient().request<PodJoinRequestStatusResponse>(
                "POST",
                `/pods/${encodeURIComponent(id)}/join-requests`
            );
            setJoinRequest(request);
        } catch (joinRequestError) {
            if (joinRequestError instanceof ApiError && joinRequestError.statusCode === 409) {
                await loadMyJoinRequest();
                return;
            }

            setAccessError(
                joinRequestError instanceof Error
                    ? joinRequestError.message
                    : "Failed to request pod access. Please try again."
            );
        } finally {
            setIsSubmittingJoinRequest(false);
        }
    }, [id, isCheckingJoinRequest, isSubmittingJoinRequest, joinRequest?.status, loadMyJoinRequest]);

    // Keep the workspace-wide org selection in step with the pod being viewed,
    // so org-scoped surfaces (home, assistant, connectors) reflect this pod's org.
    useEffect(() => {
        if (!pod?.organization_id || pod.organization_id === currentOrg?.id) return;
        const podOrg = organizations.find((org) => org.id === pod.organization_id);
        if (podOrg) setCurrentOrg(podOrg);
    }, [currentOrg?.id, organizations, pod?.organization_id, setCurrentOrg]);

    useEffect(() => {
        if (isLoading || pod) {
            setPodAccessState("idle");
            setJoinRequest(null);
            setAccessError(null);
            accessCheckKeyRef.current = null;
            return;
        }

        const podFetchError = error;
        if (!(podFetchError instanceof ApiError)) {
            setPodAccessState("error");
            setAccessError(
                podFetchError instanceof Error
                    ? podFetchError.message
                    : "Failed to load this pod."
            );
            return;
        }

        const isDeniedFromPodGet =
            podFetchError.statusCode === 403 || podFetchError.code === "INSUFFICIENT_ROLE";
        const isNotFoundFromPodGet = podFetchError.statusCode === 404;

        if (!isDeniedFromPodGet && !isNotFoundFromPodGet) {
            setPodAccessState("error");
            setAccessError(podFetchError.message || "Failed to load this pod.");
            return;
        }

        const accessCheckKey = `${id}:${podFetchError.statusCode}:${podFetchError.code ?? ""}`;
        if (accessCheckKeyRef.current === accessCheckKey) {
            return;
        }
        accessCheckKeyRef.current = accessCheckKey;

        let cancelled = false;

        void (async () => {
            setPodAccessState("checking");
            setAccessError(null);

            try {
                const permissions = await getLemmaClient(id).podPermissions.me();

                if (cancelled) return;

                const hasAnyAction = Array.isArray(permissions?.actions) && permissions.actions.length > 0;
                if (hasAnyAction) {
                    setPodAccessState("error");
                    setAccessError("You have pod permissions, but the pod details request failed. Please refresh.");
                    return;
                }

                setPodAccessState("denied");
                await loadMyJoinRequest();
            } catch (permissionError) {
                if (cancelled) return;

                if (
                    permissionError instanceof ApiError &&
                    (permissionError.statusCode === 403 || permissionError.code === "INSUFFICIENT_ROLE")
                ) {
                    setPodAccessState("denied");
                    await loadMyJoinRequest();
                    return;
                }

                if (permissionError instanceof ApiError && permissionError.statusCode === 404) {
                    setPodAccessState("not_found");
                    return;
                }

                setPodAccessState("error");
                setAccessError(
                    permissionError instanceof Error
                        ? permissionError.message
                        : "Failed to verify your pod permissions."
                );
            }
        })();

        return () => {
            cancelled = true;
        };
    }, [error, id, isLoading, loadMyJoinRequest, pod]);

    useEffect(() => {
        if (pod || isLoading || !cameFromRoot) return;
        if (podAccessState !== "denied" && podAccessState !== "not_found" && podAccessState !== "error") return;

        clearLastOpenedPodId();
        router.replace("/");
    }, [cameFromRoot, isLoading, pod, podAccessState, router]);

    useEffect(() => {
        if (!pod || !cameFromRoot) return;

        const nextSearchParams = new URLSearchParams(searchParamsString);
        nextSearchParams.delete("fromRoot");
        const nextQuery = nextSearchParams.toString();
        router.replace(`${pathname}${nextQuery ? `?${nextQuery}` : ""}`, { scroll: false });
    }, [cameFromRoot, pathname, pod, router, searchParamsString]);

    if (isLoading || podAccessState === "checking") {
        return <PageLoader />;
    }

    if (!pod && podAccessState === "denied") {
        const isPending = joinRequest?.status === "PENDING";
        const isApproved = joinRequest?.status === "APPROVED";
        const buttonLabel = isCheckingJoinRequest
            ? "Checking request..."
            : isSubmittingJoinRequest
                ? "Sending request..."
                : isPending
                    ? "Invite requested"
                    : "Request invite";

        return (
            <div className="flex min-h-screen items-center justify-center bg-transparent px-4">
                <div className="surface-panel w-full max-w-xl p-6 sm:p-8">
                    <h2 className="mb-2 font-display text-xl font-semibold text-[var(--text-primary)]">Request pod access</h2>
                    <p className="text-sm text-[var(--text-secondary)]">
                        {isPending
                            ? "Your invite request is already pending. A pod admin can approve it from pod settings."
                            : "You are signed in, but you do not have access to this pod yet. Send a request and a pod admin can approve it."}
                    </p>

                    <div className="mt-5 flex flex-wrap items-center gap-3">
                        <Button
                            type="button"
                            onClick={() => {
                                void handleRequestInvite();
                            }}
                            disabled={isCheckingJoinRequest || isSubmittingJoinRequest || isPending}
                        >
                            {buttonLabel}
                        </Button>
                        {isApproved ? (
                            <span className="text-xs text-[var(--text-tertiary)]">
                                Your request was approved. Refresh this page to continue.
                            </span>
                        ) : null}
                        <Button asChild variant="secondary">
                            <Link href="/home">Lemma home</Link>
                        </Button>
                    </div>

                    {accessError ? (
                        <p className="mt-3 text-sm text-[var(--state-error)]">{accessError}</p>
                    ) : null}
                </div>
            </div>
        );
    }

    if (!pod) {
        const title = podAccessState === "error" ? "Unable to load pod" : "Pod not found";
        const description = podAccessState === "error"
            ? accessError || "Something went wrong while loading this pod."
            : "The pod you're looking for doesn't exist.";

        return (
            <div className="flex min-h-screen items-center justify-center bg-transparent">
                <div className="text-center">
                    <h2 className="mb-2 font-display text-xl font-semibold text-[var(--text-primary)]">{title}</h2>
                    <p className="text-sm text-[var(--text-secondary)]">{description}</p>
                    <div className="mt-6 flex justify-center">
                        <Button asChild variant="secondary">
                            <Link href="/home">Lemma home</Link>
                        </Button>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <AppProvider podId={pod.id}>
            <PodAssistantScope pod={pod}>
                <PodLayoutProvider>
                    <PodShell pod={pod} pathname={pathname}>
                        {children}
                    </PodShell>
                </PodLayoutProvider>
            </PodAssistantScope>
        </AppProvider>
    );
}
