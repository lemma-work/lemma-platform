"use client";
import { docTitle } from "@/library/doc-title";
import { startAnalytics, setAnalyticsIdentity } from '@/site/analytics/client';
import { settingsFromQuery } from "@/site/legacy-address";

import { WorkspaceLoading } from "@/shell/workspace-loading";
import { Library, TableView } from "@/library/library";
import { readableName } from "@/library/reading";
import { RecordView } from "@/library/record-view";
import { ViewActions } from "./view-actions";
import { HumanProfile } from "@/session/human-profile";
import { FirstProfileStep } from "@/session/first-profile-step";
import { AllowanceNote } from "@/usage/allowance-note";
import { MinimizeIcon, ChevronUpIcon, SidebarIcon, MenuIcon, PlusIcon, SearchIcon, LinkIcon } from "@/ui/icons";
import { usePathname, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { source, NEW_CONVERSATION } from "@/data";
import { DocSpace, isDoc } from "@/docs/doc-space";
import { CommentsButton } from "@/docpages/comments/button";
import { DocAskContext } from "@/docs/doc-ask";
import { AllView } from "@/space/all-view";
import { SpaceNav } from "@/space/space-nav";
import { ShareSheet, type ShareSubject } from "@/space/share-sheet";
import { SIGN_IN_EVENT, type SignInRequest } from "@/computer/sign-in-bridge";
import { SignInPane } from "@/computer/sign-in-pane";
import { AgentPage } from "@/space/agent-page";
import { WorkflowPage } from "@/space/workflow-page";
import { displayAgentName, isPodDefaultAgent } from "@/data/agent-names";
import { SettingsPage, type SettingsSection as SpaceSettingsSection } from "@/space/settings-page";
import { WorkflowsPage } from "@/space/workflows-page";
import { Home } from "@/space/home";
import { ChatsPage } from "@/space/chats-page";
import { RunPage } from "@/space/run-page";
import { AboutPage, isAboutSection, type AboutSection } from "@/space/about-page";
import { TeammateRail } from "@/space/teammate-rail";
import { TeammatesPage } from "@/space/teammates-page";
import { TeammateFace } from "@/space/teammate-face";
import { owedFrom } from "@/space/teammates";
import { gatherAsked } from "@/thread/waiting-on-you";
import type { SpaceView } from "@/data";
import { FloatingChat, useFloatingChat, type ChatResource } from "@/chat/floating-chat";
import type { FileContent, Tab } from "@/data";
import { AppsPane } from "@/stage/apps";
import { lemma } from "@/session/client";
import { key } from "@/session/storage";
import { isUnauthorized } from "@/session/auth-state";
import { AI_MATE, NEW_MATE } from "@/copy";
import { makePage } from "@/docpages/templates";
import { renamePage } from "@/docpages/rename";
import { moveComments } from "@/docpages/comments/store";
import { NOWHERE, TEAMMATES, isNewPlace, readAddress, tabFromId, writeAddress } from "./address";
import { podAccess, readLastPods, rememberPod, type LastPods } from "./pod-access";
import { NotYours } from "./not-yours";
import { Mark } from "./mark";
import { Surfaces } from "./surfaces";
import { Notifications } from "./notifications";
import { WaitingInbox, gather } from "@/workflow/waiting-inbox";
import { SearchPalette } from "@/search/palette";
import { useResourceConversation } from "@/thread/use-resource-conversation";
import { AddPeople } from "./add-people";
import { ReachSheet } from "./reach";
import { Modal } from "./modal";
import { SettingsModal, type SettingsSection } from "@/settings/settings-modal";
import { HiringView, type FirstMove } from "@/stage/hiring";
import { ArrivalView } from "@/org/arrival-view";
import { ConversationPane } from "@/thread/conversation";
import { LiveConversation } from "@/thread/live-conversation";
import { History } from "@/thread/history";
import { AllConversations } from "@/thread/all-conversations";
import { refreshConversationLists } from "@/thread/conversation-list";
import { ComputerView } from "@/computer/computer-view";
import { FileView } from "@/thread/file-view";
import { acknowledge, readComposeRequest, registerFrame } from "@/thread/compose-bridge";
import { identityGenes } from "@/identity/seeded-identity";
import { pressSlot } from "@/identity/palette";
import { useHuddle } from "@/call/use-huddle";
import { CallScreen } from "@/call/call-screen";
import { CallBar } from "@/call/call-bar";
import { isLandingPreview, previewAboutForStep, previewTabForStep } from "@/marketing/preview-mode";
import { DesktopNotices } from "@/desktop/desktop-notices";
import { ReconnectStrip } from "./reconnect-strip";
import { useOpenSettingsEvent } from "@/desktop/open-settings";
import { useVoiceConfigured } from "@/call/voice-config";
import { PaneDivider } from "./pane-divider";
import { RightPaneToolbar, usePhoneWidth } from "./pane-sheet";
import { clampPaneWidth, layoutForTab } from "./split-tabs";
import { AppFrameView } from "@/desktop/app-frame";

/** How long a tab takes to get out of the way. Matches `tab-out` in the
 *  stylesheet; the wait and the animation have to be one number or the row
 *  either vanishes mid-collapse or sits there finished. */
const TAB_CLOSE_MS = 180;

/* Layout effects run on the client only; on the server this is the plain
   effect, which does nothing there either. */
const useBeforePaint = typeof window === "undefined" ? useEffect : useLayoutEffect;

const ORG_KEY = key("org");
const TAB_KEY = key("tabs");
const LAST_POD_KEY = key("last-pod");

/** The list a thing lives in, for a breadcrumb that has no history to go on. */
function homeListOf(tab: Tab): string | null {
    switch (tab.kind) {
        case "table":
        case "record":
            return "space:tables";
        case "file":
            /* Pages lists the space's markdown; personal files are in Files. */
            return /\.md$/i.test(tab.path) && !tab.path.startsWith("/me/") ? "space:pages" : "space:files";
        case "app":
            return "space:apps";
        case "run":
        case "workflow":
            return "space:workflows";
        default:
            return null;
    }
}

function readJson<T>(key: string, fallback: T): T {
    try {
        const raw = localStorage.getItem(key);
        return raw ? (JSON.parse(raw) as T) : fallback;
    } catch {
        return fallback;
    }
}

const SPACE_TABS: Tab[] = ([["home", "Home"], ["pages", "Pages"], ["apps", "Apps"], ["tables", "Tables"], ["files", "Files"], ["chats", "Chats"], ["workflows", "Workflows"], ["settings", "Settings"], ["about", "About"]] as [SpaceView, string][])
    .map(([view, label]) => ({ id: "space:" + view, kind: "space", label, view }));

export function AppShell({ demoStep, demoRevision, onPreviewPainted }: { demoStep?: number; demoRevision?: number; onPreviewPainted?: () => void } = {}) {
    const preview = isLandingPreview();
    const [previewPod, setPreviewPod] = useState<string | null>("kit");
    const pathname = usePathname();
    const incoming = useSearchParams();
    const [orgId, setOrgId] = useState<string | null>(() => incoming.get("org") ?? readJson<string | null>(ORG_KEY, null));
    /** Where the address bar says you are. `address.ts` has the grammar and
     *  the reasoning; what matters here is that reading it through
     *  `usePathname` makes Back and Forward work for nothing, because the
     *  shell derives from the URL rather than keeping a second copy of it. */
    const address = useMemo(() => readAddress(pathname), [pathname]);
    /** Zoomed out: every teammate at once, and no space open. The tour keeps
     *  its own copy, because it never touches the address bar. */
    const [previewTeam, setPreviewTeam] = useState(false);
    const atTeam = preview ? previewTeam : Boolean(address.team);
    const podId = atTeam ? null : preview ? previewPod : address.podId;
    const goToPod = useCallback((id: string | null) => {
        if (preview) { setPreviewTeam(false); setPreviewPod(id); return; }
        // Native history integrates with Next and preserves the mounted workspace layout.
        window.history.pushState(null, "", writeAddress({ ...NOWHERE, podId: id }));
    }, [preview]);
    /** The teammate just zoomed out of, so its card on the Teammates page
     *  can say where you were. */
    const [leftFrom, setLeftFrom] = useState<string | null>(null);
    const goToTeam = useCallback((from: string | null) => {
        setLeftFrom(from);
        if (preview) { setPreviewTeam(true); return; }
        window.history.pushState(null, "", writeAddress(TEAMMATES));
    }, [preview]);
    /** Where a card was pressed, so the space it opens grows out of it. */
    const zoomFrom = useRef<DOMRect | null>(null);
    const mainRef = useRef<HTMLElement>(null);
    const [selection, setSelection] = useState<{ id: string | null; generation: number }>({ id: null, generation: 0 });
    const conversationId = selection.id;
    /** Words handed to the Chat tab to send, and the bot to start the
     *  conversation with. Cleared by any other change of conversation, so a
     *  hand-over never outlives the new chat it was for. */
    const [handoff, setHandoff] = useState<{ text: string; createWith?: Record<string, unknown>; id: number; podId: string; sent: boolean } | null>(null);
    const setConversationId = useCallback((id: string | null) => {
        setHandoff(null);
        setSelection(previous => ({ id, generation: previous.generation + 1 }));
    }, []);
    /** What a widget, an app or the reveal asked the app to put in the
     *  composer. Held here rather than inside the thread because a request for
     *  a new conversation changes `selection`, which remounts the thread — the
     *  ask has to outlive the component it is destined for.
     *
     *  `podId` is which teammate's box it is for, because the box that takes
     *  it is not always the one on screen: every pod that has been opened keeps
     *  its conversation mounted, and the reveal sets a fill in the same batch
     *  that switches pods. Without it the words land in whichever composer was
     *  already standing there, and the new teammate opens empty. */
    const [fill, setFill] = useState<{ text: string; id: number; podId: string } | null>(() => {
        const raw = incoming.get("remixSource");
        if (!raw || !podId) return null;
        try {
            const url = new URL(raw);
            if (!["http:", "https:"].includes(url.protocol)) return null;
            return { text: "Help me rebuild or adapt this app for our workspace: " + url.href, id: 1, podId };
        } catch {
            return null;
        }
    });
    const [tabs, setTabs] = useState<Record<string, string>>(() => preview ? { kit: "conversation" } : readJson<Record<string, string>>(TAB_KEY, {}));
    /** Which teammate `/t` opens when the address names none: the one last
     *  open in that organization, so the front door and every organization
     *  switch land where somebody left off rather than on the first pod. */
    const [lastPods, setLastPods] = useState<LastPods>(() => preview ? {} : readLastPods(readJson<unknown>(LAST_POD_KEY, {})));
    /** Apps stay mounted once opened — hidden, never unmounted, so coming
     *  back to a tab does not cold-boot someone's app. */
    const appFrames = useRef<Record<string, HTMLIFrameElement | null>>({});
    /** Drops each app frame's registration when it navigates or its tab closes,
     *  so the app never trusts a window it is no longer showing. */
    const appFrameGuests = useRef<Record<string, (() => void) | undefined>>({});
    useEffect(() => () => { Object.values(appFrameGuests.current).forEach(drop => drop?.()); }, []);
    const [openedApps, setOpenedApps] = useState<Record<string, string>>({});
    /** Tabs opened on demand — the history, a file the agent showed. They
     *  stay in the strip until closed, the way an opened tab does. */
    const [extraTabs, setExtraTabs] = useState<Record<string, Tab[]>>({});
    const [searching, setSearching] = useState(false);
    /* `?connect=` is a connector round trip coming home. With no return path
       recorded the API sends it to the app root, and the panel that reads it
       is the one that has to be open. */
    const [settings, setSettings] = useState<SettingsSection | null>(() =>
        settingsFromQuery(incoming.get("settings")) ?? (incoming.get("connect") ? "connectors" : null));
    /* The desktop menu and tray open Settings at a section by raising
       `lemma:open-settings` in this page. Keyed by request, so asking for a
       different section while Settings is already open moves it there. */
    const [settingsRequest, setSettingsRequest] = useState(0);
    const [settingsFocus, setSettingsFocus] = useState<string | null>(null);
    useOpenSettingsEvent(useCallback((section: SettingsSection, focus: string | null) => {
        setSettings(section);
        setSettingsFocus(focus);
        setSettingsRequest((count) => count + 1);
        setMobileOpen(false);
    }, []));
    /* Hiring takes the whole pane, like organization settings — a candidate
       gets the same profile page a hired teammate gets, and that does not
       fit in a dialog. */
    const [hiring, setHiring] = useState(() => incoming.get('hire') === '1');
    const [collapsed, setCollapsed] = useState(() => readJson(key("sidebar-collapsed"), false));
    const [mobileOpen, setMobileOpen] = useState(false);
    const [sidebarHidden, setSidebarHidden] = useState(() => readJson(key("sidebar-hidden"), false));
    /** The header hides itself on an app, a file or the profile, where the
     *  view wants the whole pane. On the conversation it never did, which is
     *  the one place people read for a long time — so it can be put away by
     *  hand there too, and stays away until it is asked back. */
    const [headerHidden, setHeaderHidden] = useState(() => readJson(key("header-hidden"), false));
    useEffect(() => { try { localStorage.setItem(key("header-hidden"), JSON.stringify(headerHidden)); } catch { /* optional preference */ } }, [headerHidden]);
    useEffect(() => { try { localStorage.setItem(key("sidebar-hidden"), JSON.stringify(sidebarHidden)); } catch { /* optional preference */ } }, [sidebarHidden]);
    useEffect(() => {
        try { localStorage.setItem(key("sidebar-collapsed"), JSON.stringify(collapsed)); } catch { /* preferences are optional */ }
    }, [collapsed]);
    useEffect(() => {
        const shortcut = (event: KeyboardEvent) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "\\") {
                event.preventDefault();
                if (window.matchMedia("(max-width: 767px)").matches) setMobileOpen(v => !v);
                else { setSidebarHidden(false); setCollapsed(v => !v); }
            }
            /* ⌘K, and `/` when nothing is being typed into. The second is the
               one people reach for without thinking, and stealing it from a
               composer mid-sentence would be unforgivable — hence the check
               for where the caret actually is. */
            if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
                event.preventDefault();
                setSearching(true);
            }
            if (event.key === "/" && !event.metaKey && !event.ctrlKey && !event.altKey) {
                const on = document.activeElement;
                const typing = on instanceof HTMLInputElement || on instanceof HTMLTextAreaElement || (on as HTMLElement | null)?.isContentEditable;
                if (!typing) { event.preventDefault(); setSearching(true); }
            }
            if (event.key === "Escape") setMobileOpen(false);
        };
        window.addEventListener("keydown", shortcut);
        return () => window.removeEventListener("keydown", shortcut);
    }, []);
    useEffect(() => {
        if (!mobileOpen) return;
        const previous = document.activeElement as HTMLElement | null;
        const sidebar = document.getElementById("app-sidebar");
        sidebar?.querySelector<HTMLElement>("button")?.focus();
        const trap = (event: KeyboardEvent) => {
            if (event.key !== "Tab" || document.querySelector('[role="dialog"]')) return;
            const items = Array.from(sidebar?.querySelectorAll<HTMLElement>('button:not([disabled]), input, a[href]') ?? []).filter(el => el.getClientRects().length);
            if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items.at(-1)?.focus(); }
            if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0]?.focus(); }
        };
        const resize = () => { if (window.innerWidth > 767) setMobileOpen(false); };
        document.addEventListener("keydown", trap);
        window.addEventListener("resize", resize);
        return () => { document.removeEventListener("keydown", trap); window.removeEventListener("resize", resize); if (previous?.isConnected) previous.focus(); };
    }, [mobileOpen]);
    const [addingPeople, setAddingPeople] = useState(false);
    /* Channels are normally opened from the header, where `Surfaces` keeps
       the sheet's state next to the marks it belongs to. The reveal has no
       header — it is a whole-pane view that closes on its way here — so the
       one other door into that sheet is mounted beside the people dialog,
       which is here for the same reason. */
    const [reaching, setReaching] = useState(() => incoming.get('reach') === '1');
    /** Which part of About a link asked for. Counted, so asking for the same
     *  section twice scrolls to it twice. An old `?section=` still lands. */
    const [aboutSection, setAboutSection] = useState<{ at: AboutSection | null; id: number }>(() => {
        const asked = incoming.get("section");
        return { at: isAboutSection(asked) ? asked : null, id: 0 };
    });

    // Only the explicitly labelled, isolated product-tour document accepts this prop.
    // These are the same UI states reached by the shell's own buttons. Each step
    // puts back everything a visitor could have moved while exploring, so the
    // screen matches the copy beside it; the sidebar folds away once the
    // teammate is hired, leaving the space to what the step is about.
    useEffect(() => {
        if (!preview || demoStep === undefined) return;
        setPreviewTeam(false);
        setPreviewPod("kit");
        setAboutSection((was) => ({ at: previewAboutForStep(demoStep), id: was.id + 1 }));
        setSelection(previous => previous.id === null ? previous : { id: null, generation: previous.generation + 1 });
        setSettings(null);
        setSearching(false);
        setMobileOpen(false);
        setHeaderHidden(false);
        setSidebarHidden(false);
        setCollapsed(demoStep >= 1);
        setExpandedTab(null);
        setExtraTabs({});
        setOpenAgentName(null);
        setHiring(demoStep === 0);
        setAddingPeople(demoStep === 1);
        setReaching(demoStep === 4);
        setTabs(previous => ({ ...previous, kit: previewTabForStep(demoStep) }));
        for (const frame of Object.values(appFrames.current)) frame?.contentWindow?.postMessage({ type: "lemma-tour:step", step: demoStep }, window.location.origin);
    }, [demoStep, demoRevision, preview]);

    const orgs = useQuery({ queryKey: ["orgs"], queryFn: () => source.listOrgs(), staleTime: 10 * 60_000 });

    /* Who is signed in, for the arrival screen: their email domain decides
       what it may offer. The same key `HumanProfile` reads, so the two share
       one request rather than racing for the same answer. */
    const me = useQuery({
        queryKey: ["current-user"],
        /* `first_name`/`last_name`, not `name` — there is no `name` on the
           account, and reading one gave the arrival screen nothing to work
           with, so it fell back to the local part of an address and offered
           to call somebody's workspace "deepakjha0196+99's Personal". */
        queryFn: () => lemma().users.current() as Promise<{ id?: string; email?: string; first_name?: string; last_name?: string } | undefined>,
        enabled: source.label !== "sample",
        staleTime: 5 * 60_000,
    });

    /* A remembered organization only counts while it is still one of yours.
       Leaving an org — or, here, switching to the sample source — leaves an id
       in storage that matches nothing, and every `find` against it returns
       undefined, so the app quietly loses its organization without ever
       saying it could not find it. */
    const linkedPod = useQuery({
        queryKey: ["pod-access", podId],
        queryFn: () => source.getPod(podId!),
        enabled: Boolean(podId),
        staleTime: 0,
        retry: false,
    });
    const remembered = orgs.data?.some((candidate) => candidate.id === orgId) ? orgId : null;
    const activeOrgId = linkedPod.data?.orgId ?? remembered ?? orgs.data?.[0]?.id ?? null;
    useEffect(() => {
        if (source.label === "sample") return;
        let active = true;
        void startAnalytics().then(() => {
            if (active && me.data?.id) setAnalyticsIdentity({
                userId: me.data.id,
                organizationId: activeOrgId ?? undefined,
                podId: podId ?? undefined,
            });
        });
        return () => { active = false; };
    }, [me.data?.id, activeOrgId, podId]);
    const activeOrg = orgs.data?.find((candidate) => candidate.id === activeOrgId) ?? null;

    const pods = useQuery({
        queryKey: ["pods", activeOrgId],
        queryFn: () => source.listPods(activeOrgId as string),
        enabled: Boolean(activeOrgId),
        staleTime: 5 * 60_000,
    });

    /* What each teammate is waiting on you for: the queue the inbox beside
       the bell reads, under the same key, so the rail's badges and the
       Teammates page cost nothing it was not already fetching. Not in the
       tour, whose teammates say what they are waiting on in their own words. */
    const waiting = useQuery({
        queryKey: ["workflow-waiting", (pods.data ?? []).map((one) => one.id).join(",")],
        queryFn: () => gather(pods.data ?? [], source.label === "sample"),
        enabled: !preview && (pods.data?.length ?? 0) > 0,
        staleTime: 60_000,
    });
    /* And the conversations paused on you: a question or an approval a run
       stopped on, scheduled ones included. Same key Home reads. */
    const asked = useQuery({
        queryKey: ["conversation-asks", (pods.data ?? []).map((one) => one.id).join(",")],
        queryFn: () => gatherAsked(pods.data ?? [], source.label === "sample"),
        enabled: !preview && (pods.data?.length ?? 0) > 0,
        staleTime: 60_000,
    });
    const owed = useMemo(() => owedFrom(waiting.data?.rows ?? [], asked.data ?? []), [waiting.data, asked.data]);

    /* Changing teammate drops the conversation you were reading — unless the
       address named one, which is exactly what a link to a conversation is.
       Read from `window.location` rather than from `address`, so this stays an
       effect about the pod changing and does not re-run every time the mirror
       below rewrites the URL. */
    useEffect(() => {
        const named = readAddress(window.location.pathname).conversationId;
        setSelection(previous => ({ id: named, generation: previous.generation + 1 }));
    }, [podId]);

    /* Zoomed out, nobody is chosen — not even the one a bare `/t` would open. */
    const access = atTeam
        ? { state: "ready" as const, pod: null }
        : podAccess(podId, pods.data, linkedPod, activeOrgId ? lastPods[activeOrgId] ?? null : null);
    const listedPod = access.pod;
    const stranger = access.state === "denied" ? podId : null;
    /* Keyed by the pod's own organization, not the active one: a link into
       another organization's teammate is remembered where it belongs. */
    useEffect(() => {
        if (preview || !listedPod) return;
        setLastPods(previous => rememberPod(previous, listedPod.orgId, listedPod.id));
    }, [preview, listedPod]);

    /* Only the teammate you are looking at pays for its roster. */
    const detail = useQuery({
        queryKey: ["pod-detail", listedPod?.id],
        queryFn: () => source.getPodDetail(listedPod!.id, listedPod!.name, listedPod!.iconUrl),
        enabled: Boolean(listedPod),
        staleTime: 5 * 60_000,
    });

    const pod = useMemo(
        () => (listedPod && detail.data ? { ...listedPod, ...detail.data } : listedPod),
        [listedPod, detail.data],
    );
    /* Opening a resource's conversation from search. The toolbar has its own;
       both go through the same find-or-create, so neither can make a second
       conversation for a resource the other already bound. */
    const discussion = useResourceConversation(listedPod?.id ?? "");

    const history = useQuery({
        queryKey: ["conversations", pod?.id],
        queryFn: () => source.listConversations(pod!.id),
        enabled: Boolean(pod),
    });

    /* `useAssistantSession` loads nothing without an id — it has no notion of
       "the latest one". So the newest conversation is resolved here and named
       explicitly; otherwise opening a teammate showed an empty transcript
       beside a history panel full of conversations. */
    const openConversationId =
        conversationId ?? (history.data && history.data.length > 0 ? history.data[0].id : null);

    /* A call is with a teammate, not with whatever is on screen — so the pod
       it started in is frozen for its duration. Without this, walking into
       another pod mid-call would re-point the call's own conversation at a
       pod the person is not talking to. */
    const [callPod, setCallPod] = useState<{ id: string; name: string; teammate: string } | null>(null);
    const callIn = callPod ?? { id: pod?.id ?? "", name: pod?.name ?? "", teammate: pod?.teammate?.name ?? "" };
    const huddle = useHuddle({
        pod: { id: callIn.id, name: callIn.name },
        teammate: callIn.teammate,
        conversationId: openConversationId,
    });
    const queryClient = useQueryClient();
    const callConversationId = huddle.callConversationId;
    useEffect(() => {
        if (!callConversationId || callIn.id !== pod?.id) return;
        // Adopt the regular conversation created from an empty/new pane.
        if (!openConversationId || openConversationId === NEW_CONVERSATION) {
            setConversationId(callConversationId);
        }
        void refreshConversationLists(queryClient, callIn.id);
    }, [callConversationId, queryClient, pod?.id, callIn.id, openConversationId]);

    const { start: openCall, end: closeCall } = huddle;
    const startCall = useCallback(() => {
        if (!pod) return;
        setCallPod({ id: pod.id, name: pod.name, teammate: pod.teammate.name });
        void openCall();
    }, [pod, openCall]);
    const endCall = useCallback(() => { closeCall(); setCallPod(null); }, [closeCall]);
    /* Offered only once the install says it can place one. Nothing to link
       to otherwise: calls run on the web app's own voice gateway, which no
       settings page configures. */
    const voiceReady = useVoiceConfigured() === true;

    const podTabs = useQuery({
        queryKey: ["tabs", pod?.id],
        queryFn: () => source.listTabs(pod!.id),
        enabled: Boolean(pod),
        staleTime: 5 * 60_000,
    });

    const allTabs: Tab[] = useMemo(() => {
        const base = [...SPACE_TABS, ...(podTabs.data ?? [])];
        const extras = (pod && extraTabs[pod.id]) || [];
        if (extras.length === 0) return base;
        /* Opened tabs sit before the profile, which stays the last thing. */
        const at = base.findIndex((tab) => tab.kind === "profile");
        return at < 0 ? [...base, ...extras] : [...base.slice(0, at), ...extras, ...base.slice(at)];
    }, [podTabs.data, pod, extraTabs]);

    /* The old profile is About now: an address or a search result that
       still asks for it lands on the teammate's own page. */
    const rawTabId = (pod && tabs[pod.id]) || "space:home";
    const activeTabId = rawTabId === "profile" ? "space:about" : rawTabId;
    const activeTab: Tab | undefined = allTabs.find((tab) => tab.id === activeTabId) ?? allTabs[0];

    /* The list you were last on in each space, which is where anything you
       open next sits beside. */
    const [lastList, setLastList] = useState<Record<string, string>>({});
    useEffect(() => {
        if (!pod || activeTab?.kind !== "space") return;
        const id = activeTab.id;
        setLastList(previous => previous[pod.id] === id ? previous : { ...previous, [pod.id]: id });
    }, [pod, activeTab]);
    const activeKind = useRef(activeTab?.kind);
    activeKind.current = activeTab?.kind;
    const lastListRef = useRef(lastList);
    lastListRef.current = lastList;

    const [paneWidth, setPaneWidth] = useState(() => clampPaneWidth(readJson<unknown>(key("sidebar-width"), 52)));
    useEffect(() => {
        try { localStorage.setItem(key("sidebar-width"), JSON.stringify(paneWidth)); } catch { /* Display preferences are optional. */ }
    }, [paneWidth]);
    const [tabOrigins, setTabOrigins] = useState<Record<string, string>>({});
    const [expandedTab, setExpandedTab] = useState<string | null>(null);
    const expanded = Boolean(pod && expandedTab === pod.id + "|" + activeTab?.id);
    const origin = pod && activeTab ? tabOrigins[pod.id + "|" + activeTab.id] : undefined;
    /* Where the open thing came from: a list, or the conversation. */
    const beside = origin && allTabs.some(tab => tab.id === origin) ? origin : (pod && lastList[pod.id]) || "space:home";
    /* Only the conversation keeps a thing beside it — a canvas next to the
       chat that made it. Opened from a list, a page takes the whole stage and
       the list is one step back: in the breadcrumb, and in the browser's Back. */
    const layout = layoutForTab(activeTab?.id ?? "space:home", expanded,
        beside === "conversation" ? beside : activeTab?.id ?? beside);
    /* The list named in the breadcrumb: the one it was opened from, or — for
       a link opened cold, which came from nowhere — the list it lives in. */
    const fromList = origin && origin.startsWith("space:") && origin !== "space:home" ? origin : activeTab ? homeListOf(activeTab) : null;
    const cameFrom = fromList && activeTab?.kind !== "space" && activeTab?.kind !== "conversation"
        ? allTabs.find(tab => tab.id === fromList) : undefined;
    const rightTab = allTabs.find(tab => tab.id === layout.right);
    /* On a phone the right pane is a sheet over the conversation, lowered to
       its bar or raised. Kept per tab so opening a different resource, or
       picking this one again, always brings it up. */
    const phone = usePhoneWidth();
    const bodyRef = useRef<HTMLDivElement>(null);
    const [peekedTab, setPeekedTab] = useState<string | null>(null);
    const sheetLowered = Boolean(phone && pod && rightTab && peekedTab === pod.id + "|" + rightTab.id);
    const isVisible = (id: string) => id === layout.main || id === layout.right;
    /* The thing on the stage, which the floating chat is about. None while the
       conversation itself is on screen: it is the chat already. */
    const chat = useFloatingChat();
    const chatResource: ChatResource | null = !pod || stranger || isVisible("conversation") ? null
        : activeTab?.kind === "file" ? { kind: "file", name: activeTab.path, label: activeTab.label }
        : activeTab?.kind === "app" ? { kind: "app", name: activeTab.id.replace(/^app:/, ""), label: activeTab.label }
        : activeTab?.kind === "table" ? { kind: "table", name: activeTab.name, label: activeTab.label }
        : activeTab?.kind === "record" ? { kind: "table", name: activeTab.table, label: readableName(activeTab.table) }
        : null;
    const [shareOpen, setShareOpen] = useState(false);
    const [settingsSection, setSettingsSection] = useState<SpaceSettingsSection>("agents");
    const shareSubject: ShareSubject = !chatResource ? { kind: "space" }
        : chatResource.kind === "file" ? { kind: "file", path: chatResource.name, label: chatResource.label }
        : { kind: chatResource.kind === "app" ? "app" : "table", label: chatResource.label };
    const docAsk = useMemo(() => chatResource && pod ? { label: pod.name, ask: chat.ask } : null, [chatResource !== null, pod?.name, chat.ask]);  
    const paneProps = (id: string) => ({
        hidden: !isVisible(id),
        "data-side": id === layout.right ? "right" : "left",
        inert: sheetLowered && id === layout.right,
    });
    const toggleExpanded = () => {
        if (pod && activeTab) setExpandedTab(expanded ? null : pod.id + "|" + activeTab.id);
    };

    const openTab = useCallback(
        (tab: Tab, origin = "conversation", besideChat = false) => {
            if (!pod) return;
            /* Opened from inside Chat, a thing sits beside the conversation.
               Opened from anywhere else it takes the stage, and remembers the
               list you were on so the breadcrumb can take you back to it. */
            if (origin === "conversation" && !besideChat && activeKind.current !== "conversation") origin = lastListRef.current[pod.id] ?? "space:home";
            setExtraTabs((previous) => {
                const existing = previous[pod.id] ?? [];
                if (existing.some((entry) => entry.id === tab.id)) return previous;
                return { ...previous, [pod.id]: [...existing, tab] };
            });
            setTabOrigins(previous => ({ ...previous, [pod.id + "|" + tab.id]: origin }));
            setExpandedTab(null);
            setPeekedTab(null);
            setTabs(previous => ({ ...previous, [pod.id]: tab.id }));
        },
        /* Stable on purpose: the effect that applies the address depends on
           this, and a callback that changed with the view re-ran it on every
           click — putting back whatever the URL still said. */
        [pod],
    );

    /* Tabs on their way out.
     *
     *  Removing the node is instantaneous and the strip closes the gap in the
     *  same frame, so every remaining tab jumps left by the width of the one
     *  that went. Marking it first lets it collapse and take its neighbours
     *  with it, and the removal happens when there is nothing left to see. */
    const [closingTabs, setClosingTabs] = useState<Record<string, true>>({});
    const closeTimers = useRef<number[]>([]);
    useEffect(() => () => { closeTimers.current.forEach(window.clearTimeout); }, []);

    const closeTab = useCallback(
        (tabId: string) => {
            if (!pod) return;
            const podId = pod.id;
            const sourceId = tabOrigins[podId + "|" + tabId];
            const returnTo = allTabs.some(tab => tab.id === sourceId && tab.id !== tabId) ? sourceId : "conversation";
            if (activeTabId === tabId && returnTo !== "conversation") setExpandedTab(podId + "|" + returnTo);
            const drop = () => {
                setExtraTabs((previous) => ({
                    ...previous,
                    [podId]: (previous[podId] ?? []).filter((entry) => entry.id !== tabId),
                }));
                setTabs((previous) =>
                    previous[podId] === tabId ? { ...previous, [podId]: returnTo } : previous,
                );
                setClosingTabs((previous) => {
                    if (!previous[tabId]) return previous;
                    const next = { ...previous };
                    delete next[tabId];
                    return next;
                });
            };

            /* Somebody who has asked for less motion has asked for less of it
               here too: no collapse, and no wait for one that is not running. */
            if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
                drop();
                return;
            }
            setClosingTabs((previous) => ({ ...previous, [tabId]: true }));
            closeTimers.current.push(window.setTimeout(drop, TAB_CLOSE_MS));
        },
        [pod, tabOrigins, allTabs, activeTabId],
    );

    const openTable = useCallback(
        (name: string, origin?: string) => openTab({ id: "table:" + name, kind: "table", label: readableName(name), name }, origin),
        [openTab],
    );

    /* Keyed by table and id, so the same row opened twice is one tab and two
       rows from one table are two. */
    const openRecord = useCallback(
        (table: string, recordId: string, origin = "conversation") => openTab({
            id: "record:" + table + ":" + recordId,
            kind: "record",
            label: readableName(table) + " row",
            table,
            recordId,
        }, origin),
        [openTab],
    );

    const openHistory = useCallback(
        () => openTab({ id: "history", kind: "history", label: "History" }),
        [openTab],
    );

    const openComputer = useCallback(
        () => openTab({ id: "computer", kind: "computer", label: "Computer" }),
        [openTab],
    );

    /* Which agent the Agents tab is showing, held here rather than inside the
       view: search lands straight on one, and the pane is mounted only while
       it is in front — a selection kept in the view would be lost every time
       somebody glanced back at the conversation. */
    const [openAgentName, setOpenAgentName] = useState<string | null>(null);

    const openAbout = useCallback((at: AboutSection | null) => {
        if (!pod) return;
        setAboutSection((was) => ({ at, id: was.id + 1 }));
        setExpandedTab(null);
        setPeekedTab(null);
        setTabs((previous) => ({ ...previous, [pod.id]: "space:about" }));
    }, [pod]);

    /* Agents are a section of the teammate's profile rather than a view of
       their own — a tab per teammate for a list most pods have three rows of
       was a lot of permanent furniture. So this lands on the profile and names
       which agent to open there. */
    const openAgent = useCallback(
        (name: string) => {
            /* The space's own agent is the teammate, and its page is About. */
            if (isPodDefaultAgent(name)) { openAbout(null); return; }
            setOpenAgentName(name);
            /* A bot opens as its own page: who it is, a box to ask it, and
               what it is made of beside that. */
            openTab({ id: "bot:" + name, kind: "bot", label: displayAgentName(name), name });
        },
        [openTab, openAbout],
    );

    const openRun = useCallback(
        (runId: string, label: string) => openTab({ id: "run:" + runId, kind: "run", label: label === "Workflow run" ? label : label + " · run", runId }),
        [openTab],
    );
    /* A tab rebuilt from an address knows only its id; the page it shows
       learns the proper name and hands it back here. */
    const renameTab = useCallback(
        (tabId: string, label: string) => {
            if (!pod) return;
            setExtraTabs((previous) => {
                const list = previous[pod.id] ?? [];
                if (!list.some((entry) => entry.id === tabId && entry.label !== label)) return previous;
                return { ...previous, [pod.id]: list.map((entry) => (entry.id === tabId ? { ...entry, label } : entry)) };
            });
        },
        [pod],
    );
    const openWorkflow = useCallback(
        (name: string) => openTab({ id: "workflow:" + name, kind: "workflow", label: name, name }),
        [openTab],
    );

    const openFile = useCallback(
        (path: string, origin = "conversation") => {
            const label = docTitle(path);
            openTab({ id: "file:" + path, kind: "file", label, path }, origin);
        },
        [openTab],
    );

    /* A page is a markdown file, made where the space keeps its pages and
       opened straight away with the caret in it. */
    const newPage = useCallback(async () => {
        if (!pod) return;
        /* Named like a person would: Untitled, Untitled 2. Typing the title
           renames nothing, but a list of stamped names is unreadable. */
        let taken = new Set<string>();
        try {
            const listed = await source.listLibrary(pod.id, "files", "/pages");
            taken = new Set(listed.items.map(item => item.name.toLowerCase()));
        } catch {
            /* No listing: names are then found by trying, below. */
        }
        /* Create-only, so a stale or failed listing can cost a retry but never
           an existing page. Thrown on purpose — the button that asked shows
           why it failed. */
        const path = await makePage((at, text) => source.createFile(pod.id, at, text), "Untitled", "# Untitled\n\n", taken);
        void queryClient.invalidateQueries({ queryKey: ["library", pod.id] });
        openFile(path, "space:pages");
    }, [pod, openFile, queryClient]);

    /** A page's file follows its title. Moves the file (and what it owns —
     *  see `docpages/rename.ts`), then swaps the open tab in place: same pane,
     *  so the editor with its caret is not rebuilt, and the address replaced
     *  rather than pushed. Returns the new path. */
    const renamePageAt = useCallback(async (from: string, title: string): Promise<string> => {
        if (!pod) return from;
        const podId = pod.id;
        const to = await renamePage({
            move: (a, b) => source.renameFile(podId, a, b),
            read: async (at) => {
                try { return (await source.readFile(podId, at)).text ?? null; }
                catch (error) { if ((error as { statusCode?: number } | null)?.statusCode === 404) return null; throw error; }
            },
            write: (at, text) => source.writeFile(podId, at, text),
            moveComments: (a, b) => moveComments(podId, a, b),
        }, from, title);
        if (to === from) return from;
        const was = queryClient.getQueryData<FileContent>(["file", podId, from]);
        if (was) queryClient.setQueryData<FileContent>(["file", podId, to], { ...was, path: to, name: to.slice(to.lastIndexOf("/") + 1) });
        const oldId = "file:" + from;
        const newId = "file:" + to;
        renamed.current[podId + "|" + oldId] = newId;
        replaceNext.current = true;
        setExtraTabs((previous) => ({
            ...previous,
            [podId]: (previous[podId] ?? []).map((tab) => (tab.id === oldId && tab.kind === "file"
                ? { ...tab, id: newId, path: to, label: docTitle(to), pane: tab.pane ?? tab.id }
                : tab)),
        }));
        setTabOrigins((previous) => {
            const key = podId + "|" + oldId;
            if (!(key in previous)) return previous;
            const { [key]: origin, ...rest } = previous;
            return { ...rest, [podId + "|" + newId]: origin };
        });
        setTabs((previous) => (previous[podId] === oldId ? { ...previous, [podId]: newId } : previous));
        void queryClient.invalidateQueries({ queryKey: ["library", podId] });
        void queryClient.invalidateQueries({ queryKey: ["file", podId, to] });
        return to;
    }, [pod, queryClient]);

    /* A sign-in card asks for the site; it opens beside whatever you are on,
       as the browser itself with one thin bar, rather than a dialog. */
    useEffect(() => {
        const open = (event: Event) => {
            const ask = (event as CustomEvent<SignInRequest>).detail;
            if (!pod || !ask) return;
            event.preventDefault();
            openTab({
                id: "signin:" + ask.toolCallId,
                kind: "signin",
                label: "Sign in · " + ask.host,
                conversationId: ask.conversationId,
                toolCallId: ask.toolCallId,
                host: ask.host,
            }, activeKind.current === "conversation" ? "conversation" : undefined);
        };
        window.addEventListener(SIGN_IN_EVENT, open);
        return () => window.removeEventListener(SIGN_IN_EVENT, open);
    }, [pod, openTab]);

    /** A link, honoured.
     *
     *  The address is the truth about where somebody is trying to be, and the
     *  strip is not always ready for it: a link to a table or a row or a file
     *  names a tab that has never been opened here. Five kinds carry enough in
     *  their own id to be rebuilt on the spot (`tabFromId`), so those open
     *  immediately; an `app:` tab waits for the pod's app list, which is the
     *  only place its frame URL exists, and is then found in `allTabs` like
     *  any other.
     *
     *  `applied` is what keeps this and the mirror below from fighting, and it
     *  is load-bearing in both directions. It stops this effect reading back
     *  an address the mirror just wrote — rewriting the URL to the same place
     *  is not a new instruction — and it stops the mirror writing over an
     *  address nobody has read yet.
     *
     *  That second direction is not hypothetical. The mirror needs only a pod
     *  and a tab, and the tab is restored from `localStorage` before any of
     *  this runs, so on a cold load it would fire first and replace the
     *  incoming link with wherever that teammate was last left — and then mark
     *  that URL as applied, so the link was never read at all. Opening
     *  `/t/{pod}/record/launch_tasks/2` put the row in the strip and then
     *  selected the Library, because the Library was what the browser
     *  remembered. So: an address is claimed here first, and only a claimed
     *  address is mirrored. */
    const applied = useRef<string | null>(null);
    /* The tab an address has just asked for. Until it is the one on screen,
       the mirror below would read the tab being replaced and write *its*
       address back — which the next pass then applied, undoing the link. */
    const pendingTab = useRef<string | null>(null);
    /* The same for a conversation: Back to an older chat sets its id a render
       after the tab, and the mirror would otherwise write the chat being left
       over the one asked for — as a new history entry. */
    const pendingConversation = useRef<string | null>(null);
    /* Pages renamed in this session, old tab id to new, so an address still in
       the browser's history (Back to the page under its old name) lands on
       the page instead of on a file that is not there any more. */
    const renamed = useRef<Record<string, string>>({});
    /* The next address written replaces the current one instead of pushing:
       a rename, or an old address redirected, is not somewhere you went. */
    const replaceNext = useRef(false);
    useEffect(() => {
        if (preview) return;
        if (!pod) return;
        if (applied.current === pathname) return;
        /* The address names a teammate that is not the one on screen — either
           the list has not resolved yet, or it has and this teammate is not
           one of yours. Both are answered by leaving the address alone: the
           mirror only writes a claimed address, so not claiming here is what
           holds the URL still while `stranger` puts the door on the stage. An
           address bar that quietly rewrote itself to a teammate you did not
           ask for would destroy the link before you could see what it was.

           A bare `/t` names nobody, so there is no instruction here to
           protect and it claims itself — that is what lets the mirror answer
           it with whoever the shell opened. Testing `pod.id !== address.podId`
           without the first clause caught this case too, and left the root
           sitting at `/t` while showing a teammate: the one address bar in the
           app less specific than its own screen. */
        if (address.podId && address.podId !== pod.id) return;
        applied.current = pathname;
        if (!address.tabId) return;
        let wanted = address.tabId;
        for (let hops = 0; renamed.current[pod.id + "|" + wanted] && hops < 20; hops++) wanted = renamed.current[pod.id + "|" + wanted];
        if (wanted !== address.tabId) replaceNext.current = true;

        setTabs((previous) => (previous[pod.id] === wanted ? previous : { ...previous, [pod.id]: wanted }));
        if (address.agentName) setOpenAgentName(address.agentName);
        if (address.conversationId && address.conversationId !== selection.id) {
            pendingConversation.current = address.conversationId;
            setConversationId(address.conversationId);
        }

        const known = allTabs.some((tab) => tab.id === wanted);
        const rebuilt = known ? null : tabFromId(wanted);
        if (rebuilt) openTab(rebuilt);
        /* Only a tab that will exist: one that never arrives would hold the
           mirror still for good. */
        if (known || rebuilt) pendingTab.current = wanted === "profile" ? "space:about" : wanted;
    }, [pathname, address, pod, allTabs, openTab, selection.id, setConversationId, preview]);

    /** The address bar, kept in step with where you actually are.
     *
     *  Moving to a different view — a list to a page, Home to a workflow, one
     *  chat to another — is navigation, and it is pushed, so the browser's
     *  Back walks back through the app instead of out of it. The effect above
     *  reads the popped address like any other link, and the screen follows.
     *
     *  Everything else replaces: the same view getting more specific (a new
     *  chat receiving its id) or a bare `/t/{pod}` being filled in with the
     *  view restored from `tabs`. Neither is somewhere you went, and a Back
     *  that stepped through them would feel stuck. */
    useEffect(() => {
        if (preview) return;
        if (!pod || !activeTab) return;

        if (applied.current !== pathname) return;
        if (pendingTab.current) {
            if (pendingTab.current !== activeTab.id) return;
            pendingTab.current = null;
        }
        if (pendingConversation.current) {
            if (activeTab.kind === "conversation" && pendingConversation.current !== conversationId) return;
            pendingConversation.current = null;
        }

        const url = writeAddress({
            podId: pod.id,
            tabId: activeTab.id,
            /* A conversation that does not exist yet has no id worth sending
               anybody, so the sentinel stays out of the address. */
            conversationId:
                activeTab.kind === "conversation" && conversationId !== NEW_CONVERSATION ? conversationId : null,
            agentName: activeTab.kind === "profile" ? openAgentName : null,
        });
        if (url === pathname) return;
        applied.current = url;
        if (replaceNext.current) { replaceNext.current = false; window.history.replaceState(null, "", url); }
        else if (isNewPlace(readAddress(pathname), readAddress(url))) window.history.pushState(null, "", url);
        else window.history.replaceState(null, "", url);
    }, [pod, activeTab, conversationId, openAgentName, pathname, preview]);

    useEffect(() => {
        try {
            localStorage.setItem(TAB_KEY, JSON.stringify(tabs));
            if (activeOrgId) localStorage.setItem(ORG_KEY, JSON.stringify(activeOrgId));
            if (!preview) localStorage.setItem(LAST_POD_KEY, JSON.stringify(lastPods));
        } catch {
            /* storage refused; the app still works */
        }
    }, [tabs, activeOrgId, lastPods, preview]);

    /* Remember every app tab that has been opened, with its URL. */
    useEffect(() => {
        if (!pod) return;
        for (const tab of [activeTab, rightTab]) {
            if (tab?.kind !== "app") continue;
            const key = pod.id + "|" + tab.id;
            setOpenedApps(previous => previous[key] ? previous : { ...previous, [key]: tab.url });
        }
    }, [activeTab, rightTab, pod]);

    /* The landing tour: the page keeps its placeholder up until there is a
       teammate and a conversation list to show, and once the visitor is in
       the tour the teammate's app loads out of sight, so the step that opens
       it does not open onto a blank frame. */
    useEffect(() => {
        if (preview && pod && history.isSuccess) onPreviewPainted?.();
    }, [preview, pod, history.isSuccess, onPreviewPainted]);
    useEffect(() => {
        if (!preview || !pod || demoStep === undefined || demoStep < 0) return;
        for (const tab of allTabs) {
            if (tab.kind !== "app") continue;
            const key = pod.id + "|" + tab.id;
            setOpenedApps(previous => previous[key] ? previous : { ...previous, [key]: tab.url });
        }
    }, [preview, pod, demoStep, allTabs]);

    /** An organization with nobody in it opens on the hiring floor.
     *
     *  Once per organization, and that is the whole of the care needed here:
     *  somebody who steps back to look around should not be walked into the
     *  shelf again every time the pod list settles. After that the empty
     *  screen has its own door. */
    const offered = useRef<Set<string>>(new Set());
    useEffect(() => {
        if (podId || !activeOrgId || !pods.isSuccess || pods.data.length > 0) return;
        if (offered.current.has(activeOrgId)) return;
        offered.current.add(activeOrgId);
        setHiring(true);
    }, [podId, activeOrgId, pods.isSuccess, pods.data]);

    const pickTab = useCallback(
        (tabId: string) => {
            if (!pod) return;
            setExpandedTab(null);
            setPeekedTab(null);
            setTabs((previous) => ({ ...previous, [pod.id]: tabId }));
        },
        [pod],
    );

    /* The one door from a framed view into the conversation.
     *
     *  One listener for every frame the app shows — a widget several
     *  components deep in a transcript, an app on a stage tab — because the
     *  thing it has to reach, the composer, is neither of their parents.
     *  `readComposeRequest` is what makes that safe: it answers null for any
     *  window this app did not put on screen.
     *
     *  Nothing here sends. It fills the box and brings it forward, and the
     *  person decides. */
    const asks = useRef(0);
    /* Start a conversation somewhere that is not one — Home, a bot's page —
       by handing the words to the Chat tab, which creates it and sends them. */
    const startChat = useCallback((text: string, createWith?: Record<string, unknown>) => {
        if (!pod) return;
        asks.current += 1;
        setConversationId(NEW_CONVERSATION);
        if (source.label === "live") setHandoff({ text, createWith, id: asks.current, podId: pod.id, sent: false });
        else setFill({ text, id: asks.current, podId: pod.id });
        pickTab("conversation");
    }, [pod, setConversationId, pickTab]);
    const collapseCall = huddle.collapse;
    useEffect(() => {
        const listen = (event: MessageEvent) => {
            const request = readComposeRequest(event);
            if (!request || !pod) return;
            /* Brought forward every time. Filling a composer that is hidden
               behind an app tab is the same as doing nothing, and somebody just
               clicked a button expecting the teammate to answer. */
            pickTab("conversation");
            /* The composer is behind the call screen while a call is expanded,
               so filling it there would be the same as doing nothing. Step
               back to the pod — the call keeps running on the bar — and let
               the person read what the widget asked. */
            collapseCall();
            if (request.newConversation) setConversationId(NEW_CONVERSATION);
            asks.current += 1;
            setFill({ text: request.text, id: asks.current, podId: pod.id });
            acknowledge(event, request);
        };
        window.addEventListener("message", listen);
        return () => window.removeEventListener("message", listen);
    }, [pod, pickTab, setConversationId, collapseCall]);

    /** What the reveal asked for, once the app is standing on the new
     *  teammate. `say` takes the same road a widget's compose request takes —
     *  the words go into the box and stay there. `fill` survives the pod
     *  switch because it lives up here and is cleared by whoever consumes it,
     *  so there is no race with a conversation that has not mounted yet. */
    const onFirstMove = useCallback((podId: string, move: FirstMove | undefined) => {
        if (!move) return;
        if ("say" in move) {
            asks.current += 1;
            setFill({ text: move.say, id: asks.current, podId });
            return;
        }
        if (move.open === "reach") setReaching(true);
        else setAddingPeople(true);
    }, []);

    const selectedTabRef = useRef<HTMLButtonElement | null>(null);
    useEffect(() => { selectedTabRef.current?.scrollIntoView({ block: "nearest", inline: "nearest" }); }, [activeTab?.id]);
    const [visitedLibraries, setVisitedLibraries] = useState<Record<string, boolean>>({});
    useEffect(() => { if (pod && (activeTab?.kind === "library" || rightTab?.kind === "library")) setVisitedLibraries(previous => previous[pod.id] ? previous : { ...previous, [pod.id]: true }); }, [pod?.id, activeTab?.kind, rightTab?.kind]);

    /* Zooming in: the space grows out of the card that was pressed, and its
       sidebar slides in beside it. Once, on arrival — not on every change of
       view inside the space. */
    useBeforePaint(() => {
        const from = zoomFrom.current;
        if (!from || atTeam || !pod) return;
        zoomFrom.current = null;
        const main = mainRef.current;
        if (!main || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
        const box = main.getBoundingClientRect();
        main.style.transformOrigin = `${from.left + from.width / 2 - box.left}px ${from.top + from.height / 2 - box.top}px`;
        main.animate(
            [{ transform: "scale(0.9)", opacity: 0 }, { transform: "none", opacity: 1 }],
            { duration: 320, easing: "cubic-bezier(.2,.8,.2,1)" },
        );
        document.querySelector(".side__space")?.animate(
            [{ transform: "translateX(-12px)", opacity: 0 }, { transform: "none", opacity: 1 }],
            { duration: 260, easing: "cubic-bezier(.2,.8,.2,1)" },
        );
    }, [atTeam, pod?.id]);

    if (orgs.isPending) {
        return <WorkspaceLoading />;
    }

    /* A 401 has already told `SessionGate` to show the door; this component is
       on its way out and simply has nothing useful to say in the frame between
       the two. Without this, that frame is an error message accusing the API of
       being broken when the only thing that happened is that a session ended. */
    if (orgs.isError && isUnauthorized(orgs.error)) {
        return <div className="screen"><div className="screen__inner"><p role="status">Signing out…</p></div></div>;
    }

    /* Anything else really is a failure, and it is not the person's fault or
       their session's. Folded into the expired-session screen, it asks
       somebody whose network blipped to go and find a bearer token. */
    if (orgs.isError) {
        return <div className="screen"><div className="screen__inner">
            <h2>Lemma is not answering</h2>
            <p>{String(orgs.error).slice(0, 200)}</p>
            <div className="screen__actions">
                <button className="btn btn--primary" onClick={() => void orgs.refetch()}>Try again</button>
                <a className="screen__aside" href="/connect">Connection settings</a>
            </div>
        </div></div>;
    }

    /* Signed in, and belonging to nothing — a real account on its first day.
       Telling them to go to the platform and make an organization is wrong
       twice: this app can make one, and somebody may already have invited
       them. `ArrivalView` asks the backend which of those it is. */
    if (orgs.data.length === 0) {
        return (
            <ArrivalView
                email={me.data?.email ?? null}
                name={[me.data?.first_name, me.data?.last_name].filter(Boolean).join(" ") || null}
                /* The teammate the invitation named, if it named one. The pod
                   list has not been fetched yet — this component is above the
                   branch that fetches it — so this writes the address and lets
                   the render that follows resolve it, the same way the rail
                   does. */
                onEntered={(podId) => { if (podId) goToPod(podId); }}
            />
        );
    }

    /* `focusedView` is the view asking for the whole pane; `compactView` is
       only about whether the header is on screen. They were one flag, which
       is why hiding the header by hand would also have restyled the pane. */
    /* Closing a tab now happens from Recents, which does not exist yet; kept
       so the tab machinery underneath stays whole. */
    void closingTabs;
    const focusedView = !rightTab && (activeTab?.kind === "apps" || activeTab?.kind === "app" || activeTab?.kind === "file" || activeTab?.kind === "profile" || activeTab?.kind === "library" || activeTab?.kind === "table" || activeTab?.kind === "record" || activeTab?.kind === "computer");
    const compactView = focusedView || headerHidden;
    const activeKey = pod && activeTab ? pod.id + "|" + activeTab.id : "";
    /* The teammate's own column beside the rail: whenever one is open, even
       while it is still arriving, so the rail does not jump sideways. Not
       while hiring, which is about somebody new rather than the one behind it. */
    const spaceOpen = !atTeam && !stranger && !hiring && Boolean(pod ?? podId);

    return (
        <div className={`shell${collapsed ? " shell--collapsed" : ""}${mobileOpen ? " shell--mobile-open" : ""}${sidebarHidden ? " shell--hidden" : ""}${spaceOpen ? "" : " shell--rail-only"}`}>
            {searching && pod && (
                <SearchPalette
                    podId={pod.id}
                    pods={pods.data ?? []}
                    onClose={() => setSearching(false)}
                    actions={{
                        openPod: goToPod,
                        openConversation: (id) => { pickTab("conversation"); setConversationId(id); },
                        openFile: openFile,
                        openTable,
                        openRecord,
                        openApp: (name) => pickTab("app:" + name),
                        openAgent,
                        openProfile: () => openAbout(null),
                        discuss: (kind, name) => {
                            void (async () => {
                                const id = await discussion.open(kind, name);
                                if (id) { setConversationId(id); pickTab("conversation"); }
                            })();
                        },
                    }}
                />
            )}
            {/* The desktop app's background work: connecting this computer and
                the sandbox download. Renders nothing in a browser. */}
            {!preview && <DesktopNotices orgId={activeOrgId} />}
            {!preview && <ReconnectStrip />}
            {mobileOpen && <button className="sidebar-backdrop" aria-label="Close navigation" onClick={() => setMobileOpen(false)} />}
            <aside className="side" id="app-sidebar" aria-label="Workspace navigation">
                <TeammateRail
                    pods={pods.data ?? []}
                    activeId={atTeam || stranger || hiring ? null : pod?.id ?? podId}
                    atTeam={atTeam && !hiring}
                    hiring={hiring}
                    owed={owed}
                    orgName={activeOrg?.name ?? "this organization"}
                    onTeam={() => {
                        setHiring(false);
                        setSettings(null);
                        setMobileOpen(false);
                        if (!atTeam) goToTeam(pod?.id ?? null);
                    }}
                    onPick={(id) => {
                        setSettings(null);
                        setHiring(false);
                        setMobileOpen(false);
                        /* Its own face, from inside its space, is the way home. */
                        if (!atTeam && id === pod?.id) { pickTab("space:home"); return; }
                        goToPod(id);
                        setConversationId(null);
                    }}
                    onHire={activeOrgId ? () => { setSettings(null); setHiring(true); setMobileOpen(false); } : null}
                    foot={<>
                        {/* Above the account, because it is about the account — and
                            silent unless the allowance is close or spent. */}
                        <AllowanceNote orgId={activeOrgId} compact onOpenPlan={() => { setSettings("plan"); setMobileOpen(false); }} />
                        <HumanProfile compact onOpen={() => { setSettings("account"); setMobileOpen(false); }} />
                    </>}
                />
                {spaceOpen && (
                    <div className="side__space" role="group" aria-label={(pod?.name ?? "This teammate") + "’s space"}>
                        {pod && <>
                            <div className="side__head">
                                {/* The top of a space is whose it is. Its face opens
                                    the teammate itself; switching is the rail's. */}
                                <button className="side__mate" aria-current={activeTab?.id === "space:about" ? "page" : undefined}
                                    title={"About " + pod.name} onClick={() => { openAbout(null); setMobileOpen(false); }}>
                                    <TeammateFace pod={pod} size={36} />
                                    <span className="side__mate-text">
                                        <span>{pod.name}</span>
                                        {pod.description && <small>{pod.description}</small>}
                                    </span>
                                </button>
                                {/* On a phone the toolbar is already full; the
                                    drawer has space for it. */}
                                <button className="icon-button side__search" title="Search" aria-label="Search"
                                    onClick={() => { setSearching(true); setMobileOpen(false); }}>
                                    <SearchIcon size={18} />
                                </button>
                                <button className="icon-button sidebar-toggle" title={mobileOpen ? "Close navigation" : "Collapse sidebar (⌘\\)"}
                                    aria-label={mobileOpen ? "Close navigation" : "Collapse sidebar"} aria-controls="app-sidebar"
                                    onClick={() => { if (mobileOpen) setMobileOpen(false); else { setSidebarHidden(false); setCollapsed(true); } }}>
                                    <SidebarIcon size={19} />
                                </button>
                            </div>
                            <SpaceNav
                                pod={pod}
                                activeId={activeTab?.id ?? "space:home"}
                                recents={((pod && extraTabs[pod.id]) || []).slice().reverse()}
                                onPick={(id) => { pickTab(id); setMobileOpen(false); }}
                                onWorkflows={() => { pickTab("space:workflows"); setMobileOpen(false); }}
                                onSettings={() => { setSettingsSection("agents"); pickTab("space:settings"); setMobileOpen(false); }}
                                openChatId={openConversationId}
                                onOpenChat={(id) => { setConversationId(id); pickTab("conversation"); setMobileOpen(false); }}
                            />
                        </>}
                    </div>
                )}
            </aside>

            <main className="main" inert={mobileOpen} ref={mainRef}>
                {sidebarHidden && <button className="desktop-nav-toggle icon-button" aria-label="Show sidebar" title="Show sidebar" onClick={() => { setSidebarHidden(false); setCollapsed(false); }}><SidebarIcon size={21} /></button>}
                <button className="mobile-nav-toggle icon-button" aria-label="Open navigation" aria-expanded={mobileOpen} aria-controls="app-sidebar" onClick={() => setMobileOpen(true)}><MenuIcon size={22} /></button>
                {settings && <SettingsModal
                    key={settingsRequest}
                    orgs={orgs.data ?? []}
                    activeOrgId={activeOrgId}
                    onPickOrg={(id) => { setOrgId(id); goToPod(null); setConversationId(null); }}
                    initial={settings}
                    initialFocus={settingsFocus}
                    onClose={() => { setSettings(null); setSettingsFocus(null); }}
                />}
                {hiring && activeOrgId && (
                    <div className="settings-view">
                        <HiringView
                            key={preview ? demoRevision : undefined}
                            orgId={activeOrgId}
                            orgName={activeOrg?.name ?? "this organization"}
                            initialJob={preview && demoStep === 0 ? { name: "Kit", job: "Keep our launch moving: track tasks, prepare the assets, and bring decisions to Priya before anything goes out." } : undefined}
                            onClose={() => setHiring(false)}
                            onHired={(id, move) => {
                                setHiring(false);
                                goToPod(id);
                                setConversationId(null);
                                /* A brand-new pod has no remembered tab, so
                                   it lands on the conversation on its own —
                                   and `pickTab` could not help here anyway:
                                   it writes against the pod this render still
                                   thinks is current, which is the old one. */
                                onFirstMove(id, move);
                            }}
                        />
                    </div>
                )}
                {atTeam && !hiring && !huddle.expanded && (
                    <TeammatesPage
                        pods={pods.data ?? []}
                        pending={pods.isPending}
                        failed={pods.isError}
                        onRetry={() => void pods.refetch()}
                        owed={owed}
                        orgName={activeOrg?.name ?? "this organization"}
                        orgs={orgs.data ?? []}
                        orgId={activeOrgId}
                        cameFrom={leftFrom}
                        lead={
                            <button className="icon-button crumb__menu" aria-label="Open navigation" aria-expanded={mobileOpen} aria-controls="app-sidebar" onClick={() => setMobileOpen(true)}>
                                <MenuIcon size={20} />
                            </button>
                        }
                        tools={!preview && <WaitingInbox pods={pods.data ?? []} />}
                        onOpen={(id, from) => { zoomFrom.current = from; goToPod(id); setConversationId(null); }}
                        onHire={activeOrgId ? () => { setSettings(null); setHiring(true); } : null}
                        onPickOrg={(id) => { setLeftFrom(null); setOrgId(id); }}
                    />
                )}
                {stranger && (
                    <div className="settings-view">
                        <NotYours
                            podId={stranger}
                            /* Admitted. The pod list is refetching, and when it
                               comes back with this teammate in it `stranger`
                               goes null on its own and the stage takes over —
                               so there is nothing to navigate to. Clearing the
                               conversation is the same reset a rail click does,
                               because arriving in a teammate for the first time
                               should not inherit whatever was selected in the
                               stand-in behind the door. */
                            onArrived={() => { void linkedPod.refetch(); setConversationId(null); }}
                        />
                    </div>
                )}
                {huddle.expanded && (
                    <div className="settings-view">
                        <CallScreen
                            teammate={callIn.teammate}
                            podId={callIn.id}
                            conversationId={huddle.callConversationId}
                            status={huddle.status}
                            error={huddle.error}
                            muted={huddle.muted}
                            thinking={huddle.thinking}
                            level={huddle.level}
                            resources={huddle.resources}
                            transcript={huddle.transcript}
                            plan={huddle.plan}
                            onMute={huddle.toggleMute}
                            onEnd={endCall}
                            onCollapse={huddle.collapse}
                            /* Opening lands on the stage, which is behind the
                               call screen — so step back to the bar first, or
                               the tab opens somewhere nobody can see it. */
                            onOpenFile={(path) => { huddle.collapse(); openFile(path); }}
                            onOpenApp={(name) => { huddle.collapse(); pickTab("app:" + name); }}
                            onOpenTable={(name) => { huddle.collapse(); openTab({ id: "table:" + name, kind: "table", label: readableName(name), name }); }}
                        />
                    </div>
                )}
                {huddle.active && !huddle.expanded && (
                    <CallBar
                        teammate={callIn.teammate}
                        status={huddle.status}
                        error={huddle.error}
                        muted={huddle.muted}
                        thinking={huddle.thinking}
                        plan={huddle.plan}
                        level={huddle.level}
                        onMute={huddle.toggleMute}
                        onEnd={endCall}
                        onExpand={huddle.expand}
                    />
                )}
                <div
                    className={`workspace${focusedView ? " workspace--focused" : ""}${compactView ? " workspace--headless" : ""}`}
                    /* The pod wears the teammate's own tone. It is the same
                       field their badge is printed in and the same one their
                       header carries, so switching pods is a colour change
                       rather than a title change — which is the whole reason
                       the roster is drawn in colour in the first place. */
                    style={pod ? {
                        ["--field" as string]: pressSlot(identityGenes(pod.id).tone).field,
                        ["--field-ink" as string]: pressSlot(identityGenes(pod.id).tone).ink,
                    } : undefined}
                    hidden={Boolean(hiring || huddle.expanded || stranger || atTeam)}
                >
                {!pod ? (access.state === "loading" || (!podId && pods.isPending) ? <WorkspaceLoading embedded /> :
                    access.state === "error" || access.state === "missing" ? (
                        <div className="screen"><div className="screen__inner">
                            <h2>{access.state === "missing" ? "We couldn’t find this teammate" : "We couldn’t open this teammate"}</h2>
                            <p>{access.state === "missing" ? "Check the link, or try again." : "Access could not be verified. Check your connection and try again."}</p>
                            <div className="screen__actions"><button className="btn btn--primary" onClick={() => void linkedPod.refetch()}>Try again</button></div>
                        </div></div>
                    ) :
                    <div className="screen">
                        <div className="screen__inner">
                            <h2>Nobody here yet</h2>
                            <p>
                                {"Hire your first " + AI_MATE + " and this is where they will be."}
                            </p>
                            {/* A screen that tells somebody what they could do
                                and gives them no way to do it is a screen that
                                sends them to look for the button elsewhere. */}
                            {!pods.isPending && activeOrgId && (
                                <div className="screen__actions">
                                    <button className="btn btn--primary" onClick={() => setHiring(true)}>{NEW_MATE}</button>
                                </div>
                            )}
                        </div>
                    </div>
                ) : (
                    <>
                        {/* The header collapses through this wrapper, not through
                            its own height. A `max-height` guess has to be larger
                            than the content, so most of the animation is spent
                            travelling through empty numbers and the visible part
                            happens in whatever time is left — which is the whole
                            of why it snapped. A grid row of `1fr` knows the real
                            height without being told it. */}
                        <div className={`head-slot${compactView ? " head-slot--collapsed" : ""}`}>
                        <header id="app-head" className={`head${compactView ? " head--collapsed" : ""}`} inert={compactView} aria-hidden={compactView}>
                            <Mark seed={pod.id} name={pod.name} icon={pod.iconUrl} size={30} className="head__mark" />
                            <div className="head__title">
                                <h1>{pod.name}</h1>
                                <p>{pod.subtitle}</p>
                            </div>
                            <div className="head__channels"><Surfaces pod={pod} /></div>
                            <details className="head__mobile-channels" key={pod.id} onKeyDown={event => {
                                if (event.key === "Escape") { event.currentTarget.open = false; event.currentTarget.querySelector("summary")?.focus(); }
                            }}>
                                {/* A glyph, not a word. On a phone this sits in
                                    the row of controls, where 80px of label
                                    would come straight off the pod's name —
                                    and it is the only one of them that was
                                    ever spelled out. `LinkIcon` because
                                    `channels.tsx` already draws a surface it
                                    has no logo for with exactly that. */}
                                <summary aria-label="Channels" title="Channels">
                                    <LinkIcon size={17} aria-hidden="true" />
                                </summary>
                                <div className="head__channel-popover"><Surfaces pod={pod} expanded /></div>
                            </details>
                            <div className="head__spacer" />
                            <button
                                className="head__faces"
                                onClick={() => setAddingPeople((was) => !was)}
                                title="People with access"
                                aria-label="People with access"
                            >
                                {pod.members.slice(0, 4).map((member) => (
                                    <span
                                        key={member.id}
                                        className={"face" + (member.kind === "teammate" ? " face--teammate" : "")}
                                    >
                                        {member.initials}
                                    </span>
                                ))}
                                <span className="head__addface"><PlusIcon size={14} /></span>
                            </button>
                            {source.label !== "live" && <span className="head__flag">{source.label}</span>}
                            {/* Beside the other things about this pod rather
                                than in the rail: what is waiting on you is
                                per-teammate, the way the endpoint is. */}
                            {/* A shortcut nobody can see is not a feature. */}
                            <button
                                className="icon-button"
                                title="Search (⌘K)"
                                aria-label="Search"
                                onClick={() => setSearching(true)}
                            >
                                <SearchIcon size={19} />
                            </button>
                            {!preview && <Notifications podId={pod.id} />}
                            {/* Beside the bell, and not inside it. The panel
                                next door is a log of things said to you, most
                                of which are over; this is a queue of work with
                                your name on it that stays owed until it is
                                answered. Folding the queue into the log is how
                                the queue stops being looked at.
                             *
                             *  Every pod, not this one, which is the one place
                                this control disagrees with its neighbour: a
                                workflow stuck on you in a teammate you have
                                not opened today is exactly the one you will
                                never find. The endpoint is pod-scoped, so the
                                list already loaded for the rail is what it
                                fans out over. */}
                            {!preview && <WaitingInbox pods={pods.data ?? []} />}
                            <button
                                className="head__gear head__collapse"
                                title="Hide the header"
                                aria-label="Hide the header"
                                aria-expanded={true}
                                aria-controls="app-head"
                                onClick={() => setHeaderHidden(true)}
                            >
                                <ChevronUpIcon size={19} />
                            </button>
                        </header>
                        </div>

                        <div className="workspace-toolbar">
                            {compactView && (
                                <button
                                    className="toolbar-identity"
                                    title={focusedView ? `Back to ${pod.name} conversation` : "Show the header"}
                                    onClick={() => { if (focusedView) pickTab("conversation"); else setHeaderHidden(false); }}
                                >
                                    <Mark seed={pod.id} name={pod.name} icon={pod.iconUrl} size={24}/><span>{pod.name}</span>
                                </button>
                            )}
                        {/* No tab strip: the sidebar is how you move. What is on the
                            stage is named here, the way Space's breadcrumb does. */}
                        {/* A hidden sidebar comes back from here, the first thing in
                            the row — not from a button floating under it. */}
                        {(sidebarHidden || collapsed) && (
                            <button className="icon-button crumb__side" aria-label="Show sidebar" title="Show sidebar (⌘\)"
                                onClick={() => { setSidebarHidden(false); setCollapsed(false); }}>
                                <SidebarIcon size={18} />
                            </button>
                        )}
                        {/* On a phone the sidebar is a drawer, and this row is
                            where it opens from — not a button floating over it. */}
                        <button className="icon-button crumb__menu" aria-label="Open navigation" aria-expanded={mobileOpen} aria-controls="app-sidebar" onClick={() => setMobileOpen(true)}>
                            <MenuIcon size={20} />
                        </button>
                        <div className="crumb">
                            {/* Up one altitude: every teammate in the organization. */}
                            <button type="button" className="crumb__up crumb__org" title="All teammates" onClick={() => goToTeam(pod.id)}>
                                {activeOrg?.name ?? "Teammates"}
                            </button>
                            <span className="crumb__sep crumb__sep--org">/</span>
                            <button type="button" className="crumb__up crumb__mate" onClick={() => pickTab("space:home")}>
                                <TeammateFace pod={pod} size={18} />{pod.name}
                            </button>
                            <span className="crumb__sep">/</span>
                            {cameFrom && <>
                                <button type="button" className="crumb__from" onClick={() => pickTab(cameFrom.id)}>{cameFrom.label}</button>
                                <span className="crumb__sep crumb__sep--from">/</span>
                            </>}
                            <span className="crumb__here">{activeTab?.kind === "conversation" ? "Chat" : activeTab?.label ?? "Pages"}</span>
                        </div>
                            {activeTab?.kind !== "space" && <ViewActions key={activeTab?.id} tab={activeTab} podId={pod.id}
                                onNew={() => { setConversationId(NEW_CONVERSATION); pickTab("conversation"); }}
                                onHistory={openHistory}
                                onComputer={openComputer}
                                onReload={() => {
                                    /* The src it was given, which on macOS is
                                       the app's alias rather than its URL. */
                                    const frame = appFrames.current[activeKey];
                                    if (frame && activeTab?.kind === "app") frame.src = frame.getAttribute("src") ?? activeTab.url;
                                }} />}
                            {expanded && <button className="icon-button" title="Return to sidebar" aria-label="Return to sidebar" onClick={toggleExpanded}><MinimizeIcon size={18} /></button>}
                            {activeTab?.kind === "file" && isDoc(activeTab.path) && !/^\/me(\/|$)/.test(activeTab.path) && <CommentsButton path={activeTab.path} />}
                            <button className="icon-button crumb__search" title="Search (⌘K)" aria-label="Search" onClick={() => setSearching(true)}>
                                <SearchIcon size={18} />
                            </button>
                            <button className="share-pill" onClick={() => setShareOpen(true)} title={"Share " + (chatResource?.label ?? pod.name)}>Share</button>
                            {/* The header row is gone, as it is in Space; what it
                                carried that is still owed to you lives here. */}
                            {!preview && <Notifications podId={pod.id} />}
                            {!preview && <WaitingInbox pods={pods.data ?? []} />}
                        </div>

                        <div ref={bodyRef} className={"body" + (rightTab ? " body--dual" : "") + (sheetLowered ? " body--peek" : "") + (chat.open && chatResource ? " body--chat" : "")} style={{ ["--split-position" as string]: paneWidth + "%" }}>
                            {rightTab && <PaneDivider value={paneWidth} onChange={setPaneWidth} />}
                            {pod && !stranger && !isVisible("conversation") && activeTab?.kind !== "profile" && activeTab?.kind !== "bot" && activeTab?.id !== "space:home" && activeTab?.id !== "space:about" && (
                                <FloatingChat
                                    pod={pod}
                                    resource={chatResource}
                                    live={source.label === "live"}
                                    state={chat}
                                    onOpenFile={openFile}
                                    onOpenTable={openTable}
                                    onOpenApp={(name) => pickTab("app:" + name)}
                                    onExpand={(id, resource) => {
                                        /* The same conversation, full size, with
                                           what it is about in the panel beside it. */
                                        chat.setOpen(false);
                                        setConversationId(id);
                                        if (resource?.kind === "file") {
                                            openTab({ id: "file:" + resource.name, kind: "file", label: docTitle(resource.name), path: resource.name }, "conversation", true);
                                        } else if (resource?.kind === "table") {
                                            openTab({ id: "table:" + resource.name, kind: "table", label: readableName(resource.name), name: resource.name }, "conversation", true);
                                        } else {
                                            pickTab("conversation");
                                        }
                                    }}
                                />
                            )}
                            {rightTab && <RightPaneToolbar
                                body={bodyRef}
                                label={rightTab.label}
                                peeked={sheetLowered}
                                onPeek={lowered => setPeekedTab(lowered ? pod.id + "|" + rightTab.id : null)}
                                onExpand={toggleExpanded}
                                onClose={() => {
                                    const main = layout.main;
                                    pickTab(main);
                                    if (main !== "conversation") setExpandedTab(pod.id + "|" + main);
                                }}
                            />}
                            {Object.entries(openedApps).map(([key, url]) => (
                                <div className="pane app-pane" key={key} {...paneProps(key.startsWith(pod.id + "|") ? key.slice(pod.id.length + 1) : "")}>
                                <AppFrameView
                                    url={url}
                                    hidden={key !== activeKey && key !== pod.id + "|" + rightTab?.id}
                                    frameRef={element => { appFrames.current[key] = element; }}
                                    /* Registered on load, not on mount: an app
                                       that navigates gets a new contentWindow,
                                       and the window that asks the app for
                                       something has to be one we vouched for. */
                                    onFrameLoad={view => {
                                        appFrameGuests.current[key]?.();
                                        appFrameGuests.current[key] = registerFrame(view);
                                    }}
                                />
                                </div>
                            ))}

                            <div className="split" {...paneProps("conversation")}>
                                    <div className="convo-host">
                                        {source.label === "live" ? (
                                            <LiveConversation
                                                key={pod.id + ":" + selection.generation}
                                                pod={pod}
                                                conversationId={openConversationId}
                                                fill={fill?.podId === pod.id ? fill : null}
                                                onFilled={() => setFill(null)}
                                                createWith={handoff?.podId === pod.id ? handoff.createWith : undefined}
                                                autoSend={handoff?.podId === pod.id && !handoff.sent ? handoff : null}
                                                onAutoSent={() => setHandoff(previous => previous && { ...previous, sent: true })}
                                                onCreated={id => setSelection(previous => previous.generation === selection.generation
                                                    ? { ...previous, id } : previous)}
                                                onOpenApp={(name) => pickTab("app:" + name)}
                                                onOpenFile={openFile}
                                                onOpenTable={openTable}
                                                onVoice={voiceReady ? startCall : undefined}
                                                callError={huddle.error}
                                                callRefresh={callConversationId === openConversationId ? `${huddle.active}:${huddle.thinking}:${huddle.expanded}` : ""}
                                            />
                                        ) : (
                                            <ConversationPane
                                                key={pod.id + ":" + (conversationId ?? "latest")}
                                                pod={pod}
                                                conversationId={conversationId}
                                                fill={fill?.podId === pod.id ? fill : null}
                                                onFilled={() => setFill(null)}
                                                onCreated={setConversationId}
                                                onOpenApp={(name) => pickTab("app:" + name)}
                                                onOpenFile={openFile}
                                                onOpenTable={openTable}
                                            />
                                        )}
                                    </div>
                                    <History
                                        pod={pod}
                                        conversationId={openConversationId}
                                        onPick={setConversationId}
                                        onSeeAll={openHistory}
                                    />
                            </div>
                            {allTabs.filter((tab): tab is Extract<Tab, {kind: "space"}> => tab.kind === "space").map(tab => (
                                <div className="pane space-pane" key={pod.id + tab.id} {...paneProps(tab.id)}>
                                    {tab.view === "home" ? (
                                        <Home
                                            pod={pod}
                                            pods={pods.data ?? []}
                                            onNewPage={newPage}
                                            onOpenRun={openRun}
                                            onOpenConversation={(id) => { setConversationId(id); pickTab("conversation"); }}
                                            onAbout={() => openAbout(null)}
                                            onAsk={(text) => startChat(text)}
                                        />
                                    ) : tab.view === "about" ? (
                                        <AboutPage
                                            pod={pod}
                                            orgId={activeOrgId}
                                            orgName={activeOrg?.name ?? "this organization"}
                                            section={aboutSection.at}
                                            request={aboutSection.id}
                                            onAsk={() => { setConversationId(NEW_CONVERSATION); pickTab("conversation"); }}
                                            onOpenAgent={openAgent}
                                            onAskFor={(text) => { pickTab("conversation"); asks.current += 1; setFill({ text, id: asks.current, podId: pod.id }); }}
                                            onOpenRun={openRun}
                                            onOpenConversation={(id) => { setConversationId(id); pickTab("conversation"); }}
                                            onFile={(path) => openFile(path, "space:about")}
                                            onSettings={() => { setSettingsSection("agents"); pickTab("space:settings"); }}
                                        />
                                    ) : tab.view === "chats" ? (
                                        <ChatsPage
                                            pod={pod}
                                            openId={openConversationId}
                                            onOpen={(id) => { setConversationId(id); pickTab("conversation"); }}
                                            onOpenRun={(runId) => openRun(runId, "Workflow run")}
                                            onNew={() => { setConversationId(NEW_CONVERSATION); pickTab("conversation"); }}
                                        />
                                    ) : tab.view === "workflows" ? (
                                        <WorkflowsPage pod={pod} pods={pods.data ?? []} onOpenWorkflow={openWorkflow} onOpenRun={openRun} />
                                    ) : tab.view === "settings" ? (
                                        <SettingsPage
                                            pod={pod}
                                            orgId={activeOrgId}
                                            orgName={activeOrg?.name ?? "this organization"}
                                            section={settingsSection}
                                            onSection={setSettingsSection}
                                            onAbout={() => openAbout(null)}
                                        />
                                    ) : <AllView
                                        podId={pod.id}
                                        spaceName={pod.name}
                                        botName={pod.teammate?.name || pod.name}
                                        members={pod.members}
                                        view={tab.view}
                                        apps={allTabs.filter((each): each is Extract<Tab, {kind: "app"}> => each.kind === "app")}
                                        onOpenFile={(path) => openFile(path, tab.id)}
                                        onOpenTable={(name) => openTable(name, tab.id)}
                                        onOpenApp={pickTab}
                                        onOpenFolder={() => pickTab("library")}
                                        onNewPage={newPage}
                                        onNewChat={() => { setConversationId(NEW_CONVERSATION); pickTab("conversation"); }}
                                    />}
                                </div>
                            ))}
                            {isVisible("apps") && <div className="pane" {...paneProps("apps")}><AppsPane name={pod.name} tabs={allTabs} onOpen={pickTab} onAsk={(text) => { pickTab("conversation"); asks.current += 1; setFill({ text, id: asks.current, podId: pod.id }); }} /></div>}
                            {isVisible("history") && (<div className="pane" {...paneProps("history")}>
                                <AllConversations
                                    pod={pod}
                                    conversationId={conversationId}
                                    onPick={(id) => {
                                        setConversationId(id);
                                        pickTab("conversation");
                                    }}
                                />
                            </div>)}
                            {allTabs.some((tab) => tab.kind === "computer") && (
                                <div className="pane library-pane" {...paneProps("computer")}>
                                    {/* The sentinel is a conversation that does not
                                        exist yet, so there is no directory to ask
                                        about — the view opens on the whole machine
                                        instead of fetching a conversation by a name
                                        the server has never seen. */}
                                    <ComputerView
                                        podId={pod.id}
                                        conversationId={openConversationId === NEW_CONVERSATION ? null : openConversationId}
                                        visible={isVisible("computer")}
                                    />
                                </div>
                            )}
                            {allTabs.filter((tab): tab is Extract<Tab, {kind: "run"}> => tab.kind === "run").map(tab => (
                                <div className="pane run-pane" key={pod.id + tab.id} {...paneProps(tab.id)}>
                                    <div className="run-page">
                                        <RunPage pod={pod} runId={tab.runId} label={tab.label} onBack={() => pickTab(lastList[pod.id] ?? "space:workflows")}
                                            onOpenConversation={(id) => { setConversationId(id); pickTab("conversation"); }}
                                            onOpenRun={openRun} onOpenWorkflow={openWorkflow}
                                            onNamed={(name) => renameTab(tab.id, name + " · run")} />
                                    </div>
                                </div>
                            ))}
                            {allTabs.filter((tab): tab is Extract<Tab, {kind: "workflow"}> => tab.kind === "workflow").map(tab => (
                                <div className="pane run-pane" key={pod.id + tab.id} {...paneProps(tab.id)}>
                                    <div className="run-page">
                                        <WorkflowPage pod={pod} orgId={activeOrgId} name={tab.name}
                                            onBack={() => pickTab("space:workflows")}
                                            onOpenRun={openRun}
                                            onConnect={() => setSettings("connectors")}
                                            onDiscuss={(name) => {
                                                void (async () => {
                                                    const id = await discussion.open("workflow", name);
                                                    if (id) { setConversationId(id); pickTab("conversation"); }
                                                })();
                                            }} />
                                    </div>
                                </div>
                            ))}
                            {allTabs.filter((tab): tab is Extract<Tab, {kind: "bot"}> => tab.kind === "bot").map(tab => (
                                <div className="pane bot-pane" key={pod.id + tab.id} {...paneProps(tab.id)}>
                                    <AgentPage
                                        pod={pod}
                                        name={tab.name}
                                        live={source.label === "live"}
                                        onBack={() => openAbout("agents")}
                                        onOpenConversation={(id) => { setConversationId(id); pickTab("conversation"); }}
                                        onAsk={startChat}
                                        onOpenSchedules={() => openAbout("schedules")}
                                        onOpenWorkflows={() => pickTab("space:workflows")}
                                    />
                                </div>
                            ))}
                            {allTabs.filter((tab): tab is Extract<Tab, {kind: "signin"}> => tab.kind === "signin").map(tab => (
                                <div className="pane signin-pane" key={pod.id + tab.id} {...paneProps(tab.id)}>
                                    <SignInPane compact conversationId={tab.conversationId} toolCallId={tab.toolCallId} onDone={() => closeTab(tab.id)} />
                                </div>
                            ))}
                            {allTabs.filter((tab): tab is Extract<Tab, {kind: "file"}> => tab.kind === "file").map(tab => (
                                <div className={"pane file-tab-pane" + (isDoc(tab.path) ? " doc-tab-pane" : "")} key={tab.pane ?? tab.id} {...paneProps(tab.id)}>
                                    {isDoc(tab.path)
                                        ? <DocAskContext.Provider value={docAsk}><DocSpace pod={pod} path={tab.path} renamePage={renamePageAt} openFile={openFile} openTable={(name) => openTable(name)} openConversation={(id) => { setConversationId(id); pickTab("conversation"); }} sendToBot={chatResource ? chat.send : null} /></DocAskContext.Provider>
                                        : <div className="pane__inner"><FileView podId={pod.id} path={tab.path} full /></div>}
                                </div>
                            ))}
                            <div className="pane library-pane" {...paneProps("library")} key={pod.id + ":library"}>
                                {(isVisible("library") || visitedLibraries[pod.id]) && <Library podId={pod.id} onFile={path => openFile(path, "library")} onTable={name => openTab({ id: "table:" + name, kind: "table", label: readableName(name), name }, "library")}/>}
                            </div>
                            {allTabs.filter((tab): tab is Extract<Tab, {kind: "table"}> => tab.kind === "table").map(tab => <div className="pane library-pane" key={pod.id + tab.id} {...paneProps(tab.id)}><TableView podId={pod.id} name={tab.name} onOpenRecord={(table, recordId) => openRecord(table, recordId, tab.id)}/></div>)}
                            {allTabs.filter((tab): tab is Extract<Tab, {kind: "record"}> => tab.kind === "record").map(tab => (
                                <div className="pane library-pane" key={pod.id + tab.id} {...paneProps(tab.id)}>
                                    <RecordView
                                        podId={pod.id}
                                        tableName={tab.table}
                                        recordId={tab.recordId}
                                        onOpenTable={openTable}
                                        onOpenRecord={openRecord}
                                    />
                                </div>
                            ))}
                            {/* Mounted only while it is in front, the way the
                                profile is: the list is a request per pod, and
                                a pane kept alive behind every conversation
                                would spend one on every teammate somebody
                                clicks past. */}
                            {!activeTab && !podTabs.isPending && <div className="blank">Nothing pinned here yet.</div>}
                        </div>
                    </>
                )}
                </div>
            </main>

            {reaching && pod && <ReachSheet pod={pod} onClose={() => setReaching(false)} />}
            {shareOpen && pod && <ShareSheet pod={pod} orgId={activeOrgId} subject={shareSubject} onClose={() => setShareOpen(false)} />}

            {/* Here rather than on the arrival screen: this branch is the
                first render that has somewhere to belong, whichever of the
                ways in somebody took. */}
            <FirstProfileStep />

            {addingPeople && pod && (
                <Modal
                    title={"People with access to " + pod.name}
                    onClose={() => setAddingPeople(false)}
                >
                    <AddPeople pod={pod} orgId={activeOrgId} />
                </Modal>
            )}
        </div>
    );
}
