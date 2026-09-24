"use client";
import { startAnalytics, setAnalyticsIdentity } from '@/site/analytics/client';
import { settingsFromQuery } from "@/site/legacy-address";

import { WorkspaceLoading } from "@/shell/workspace-loading";
import { Library, TableView } from "@/library/library";
import { readableName } from "@/library/reading";
import { RecordView } from "@/library/record-view";
import { ViewActions } from "./view-actions";
import { HumanProfile } from "@/session/human-profile";
import { AllowanceNote } from "@/usage/allowance-note";
import { ChevronUpIcon, LemmaLogo, SidebarIcon, MenuIcon, PlusIcon, CloseIcon, ChatIcon, ProfileIcon, HistoryIcon, FileIcon, TableIcon, LibraryIcon, AppsIcon, SearchIcon, ComputerIcon, LinkIcon } from "@/ui/icons";
import { usePathname, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { source, NEW_CONVERSATION } from "@/data";
import type { Tab } from "@/data";
import { AppsPane } from "@/stage/apps";
import { lemma } from "@/session/client";
import { key } from "@/session/storage";
import { isUnauthorized } from "@/session/auth-state";
import { AI_MATE, NEW_MATE } from "@/copy";
import { NOWHERE, readAddress, tabFromId, writeAddress } from "./address";
import { podAccess } from "./pod-access";
import { NotYours } from "./not-yours";
import { OrgSwitcher } from "./org-switcher";
import { Rail } from "./rail";
import { Mark } from "./mark";
import { Surfaces } from "./surfaces";
import { Notifications } from "./notifications";
import { WaitingInbox } from "@/workflow/waiting-inbox";
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
import { ProfilePane } from "@/stage/profile";
import { History } from "@/thread/history";
import { AllConversations } from "@/thread/all-conversations";
import { ComputerView } from "@/computer/computer-view";
import { FileView } from "@/thread/file-view";
import { acknowledge, readComposeRequest, registerFrame } from "@/thread/compose-bridge";
import { identityGenes } from "@/identity/seeded-identity";
import { pressSlot } from "@/identity/palette";
import { useHuddle } from "@/call/use-huddle";
import { CallScreen } from "@/call/call-screen";
import { CallBar } from "@/call/call-bar";
import { isLandingPreview, previewTabForStep } from "@/marketing/preview-mode";

/** How long a tab takes to get out of the way. Matches `tab-out` in the
 *  stylesheet; the wait and the animation have to be one number or the row
 *  either vanishes mid-collapse or sits there finished. */
const TAB_CLOSE_MS = 180;

const ORG_KEY = key("org");
const TAB_KEY = key("tabs");

function readJson<T>(key: string, fallback: T): T {
    try {
        const raw = localStorage.getItem(key);
        return raw ? (JSON.parse(raw) as T) : fallback;
    } catch {
        return fallback;
    }
}

export function AppShell({ demoStep, demoRevision }: { demoStep?: number; demoRevision?: number } = {}) {
    const preview = isLandingPreview();
    const [previewPod, setPreviewPod] = useState<string | null>("kit");
    const pathname = usePathname();
    const incoming = useSearchParams();
    const [orgId, setOrgId] = useState<string | null>(() => incoming.get("org") ?? readJson<string | null>(ORG_KEY, null));
    const [entrySection] = useState(() => incoming.get("section") ?? undefined);
    /** Where the address bar says you are. `address.ts` has the grammar and
     *  the reasoning; what matters here is that reading it through
     *  `usePathname` makes Back and Forward work for nothing, because the
     *  shell derives from the URL rather than keeping a second copy of it. */
    const address = useMemo(() => readAddress(pathname), [pathname]);
    const podId = preview ? previewPod : address.podId;
    const goToPod = useCallback((id: string | null) => {
        if (preview) { setPreviewPod(id); return; }
        // Native history integrates with Next and preserves the mounted workspace layout.
        window.history.pushState(null, "", writeAddress({ ...NOWHERE, podId: id }));
    }, [preview]);
    const [selection, setSelection] = useState<{ id: string | null; generation: number }>({ id: null, generation: 0 });
    const conversationId = selection.id;
    const setConversationId = useCallback((id: string | null) => {
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
    const [settings, setSettings] = useState<SettingsSection | null>(() => settingsFromQuery(incoming.get("settings")));
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

    // Only the explicitly labelled, isolated product-tour document accepts this prop.
    // These are the same UI states reached by the shell's own buttons.
    useEffect(() => {
        if (!preview || demoStep === undefined) return;
        setPreviewPod("kit");
        setSettings(null);
        setSearching(false);
        setMobileOpen(false);
        setHeaderHidden(false);
        setHiring(demoStep === 0);
        setAddingPeople(demoStep === 1);
        setReaching(demoStep === 4);
        setTabs(previous => ({ ...previous, kit: previewTabForStep(demoStep) }));
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

    /* Changing teammate drops the conversation you were reading — unless the
       address named one, which is exactly what a link to a conversation is.
       Read from `window.location` rather than from `address`, so this stays an
       effect about the pod changing and does not re-run every time the mirror
       below rewrites the URL. */
    useEffect(() => {
        const named = readAddress(window.location.pathname).conversationId;
        setSelection(previous => ({ id: named, generation: previous.generation + 1 }));
    }, [podId]);

    const access = podAccess(podId, pods.data, linkedPod);
    const listedPod = access.pod;
    const stranger = access.state === "denied" ? podId : null;

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
        void queryClient.invalidateQueries({ queryKey: ["conversations", callIn.id] });
    }, [callConversationId, queryClient, pod?.id, callIn.id, openConversationId]);

    const { start: openCall, end: closeCall } = huddle;
    const startCall = useCallback(() => {
        if (!pod) return;
        setCallPod({ id: pod.id, name: pod.name, teammate: pod.teammate.name });
        void openCall();
    }, [pod, openCall]);
    const endCall = useCallback(() => { closeCall(); setCallPod(null); }, [closeCall]);

    const podTabs = useQuery({
        queryKey: ["tabs", pod?.id],
        queryFn: () => source.listTabs(pod!.id),
        enabled: Boolean(pod),
        staleTime: 5 * 60_000,
    });

    const allTabs: Tab[] = useMemo(() => {
        const base = podTabs.data ?? [];
        const extras = (pod && extraTabs[pod.id]) || [];
        if (extras.length === 0) return base;
        /* Opened tabs sit before the profile, which stays the last thing. */
        const at = base.findIndex((tab) => tab.kind === "profile");
        return at < 0 ? [...base, ...extras] : [...base.slice(0, at), ...extras, ...base.slice(at)];
    }, [podTabs.data, pod, extraTabs]);

    const activeTabId = (pod && tabs[pod.id]) || "conversation";
    const activeTab: Tab | undefined = allTabs.find((tab) => tab.id === activeTabId) ?? allTabs[0];

    const openTab = useCallback(
        (tab: Tab) => {
            if (!pod) return;
            setExtraTabs((previous) => {
                const existing = previous[pod.id] ?? [];
                if (existing.some((entry) => entry.id === tab.id)) return previous;
                return { ...previous, [pod.id]: [...existing, tab] };
            });
            setTabs((previous) => ({ ...previous, [pod.id]: tab.id }));
        },
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
            const drop = () => {
                setExtraTabs((previous) => ({
                    ...previous,
                    [podId]: (previous[podId] ?? []).filter((entry) => entry.id !== tabId),
                }));
                setTabs((previous) =>
                    previous[podId] === tabId ? { ...previous, [podId]: "conversation" } : previous,
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
        [pod],
    );

    const openTable = useCallback(
        (name: string) => openTab({ id: "table:" + name, kind: "table", label: readableName(name), name }),
        [openTab],
    );

    /* Keyed by table and id, so the same row opened twice is one tab and two
       rows from one table are two. */
    const openRecord = useCallback(
        (table: string, recordId: string) => openTab({
            id: "record:" + table + ":" + recordId,
            kind: "record",
            label: readableName(table) + " row",
            table,
            recordId,
        }),
        [openTab],
    );

    const openHistory = useCallback(
        () => openTab({ id: "history", kind: "history", label: "All conversations" }),
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

    /* Agents are a section of the teammate's profile rather than a view of
       their own — a tab per teammate for a list most pods have three rows of
       was a lot of permanent furniture. So this lands on the profile and names
       which agent to open there. */
    const openAgent = useCallback(
        (name: string) => {
            setOpenAgentName(name);
            if (pod) setTabs((previous) => ({ ...previous, [pod.id]: "profile" }));
        },
        [pod],
    );

    const openFile = useCallback(
        (path: string) => {
            const label = path.split("/").filter(Boolean).pop() ?? path;
            openTab({ id: "file:" + path, kind: "file", label, path });
        },
        [openTab],
    );

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

        setTabs((previous) => (previous[pod.id] === address.tabId ? previous : { ...previous, [pod.id]: address.tabId! }));
        if (address.agentName) setOpenAgentName(address.agentName);
        if (address.conversationId && address.conversationId !== selection.id) setConversationId(address.conversationId);

        const known = allTabs.some((tab) => tab.id === address.tabId);
        const rebuilt = known ? null : tabFromId(address.tabId);
        if (rebuilt) openTab(rebuilt);
    }, [pathname, address, pod, allTabs, openTab, selection.id, setConversationId, preview]);

    /** The address bar, kept in step with where you actually are.
     *
     *  `replaceState`, never `push`. The README's one architectural rule is
     *  that tabs change the stage beside the conversation and do not compete
     *  with it — "if that ever stops being true, the tabs have become
     *  navigation and the product is worse". Putting a tab switch in the
     *  history stack is precisely making them navigation, so Back moves
     *  between teammates (`goToPod` pushes) and glancing between things you
     *  already have open leaves no trace, the way switching browser tabs does
     *  not.
     *
     *  This is also what answers a bare `/t/{pod}`: the tab is restored from
     *  `tabs`, and the URL is rewritten to name it, so the address bar is
     *  never less specific than the screen. */
    useEffect(() => {
        if (preview) return;
        if (!pod || !activeTab) return;

        if (applied.current !== pathname) return;

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
        window.history.replaceState(null, "", url);
    }, [pod, activeTab, conversationId, openAgentName, pathname, preview]);

    useEffect(() => {
        try {
            localStorage.setItem(TAB_KEY, JSON.stringify(tabs));
            if (activeOrgId) localStorage.setItem(ORG_KEY, JSON.stringify(activeOrgId));
        } catch {
            /* storage refused; the app still works */
        }
    }, [tabs, activeOrgId]);

    /* Remember every app tab that has been opened, with its URL. */
    useEffect(() => {
        if (!activeTab || activeTab.kind !== "app" || !pod) return;
        const key = pod.id + "|" + activeTab.id;
        setOpenedApps((previous) => (previous[key] ? previous : { ...previous, [key]: activeTab.url }));
    }, [activeTab, pod]);

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
    useEffect(() => { if (pod && activeTab?.kind === "library") setVisitedLibraries(previous => previous[pod.id] ? previous : { ...previous, [pod.id]: true }); }, [pod?.id, activeTab?.kind]);

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
    const focusedView = activeTab?.kind === "apps" || activeTab?.kind === "app" || activeTab?.kind === "file" || activeTab?.kind === "profile" || activeTab?.kind === "library" || activeTab?.kind === "table" || activeTab?.kind === "record" || activeTab?.kind === "computer";
    const compactView = focusedView || headerHidden;
    const activeKey = pod && activeTab ? pod.id + "|" + activeTab.id : "";

    return (
        <div className={`shell${collapsed ? " shell--collapsed" : ""}${mobileOpen ? " shell--mobile-open" : ""}${sidebarHidden ? " shell--hidden" : ""}`}>
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
                        openProfile: () => pickTab("profile"),
                        discuss: (kind, name) => {
                            void (async () => {
                                const id = await discussion.open(kind, name);
                                if (id) { setConversationId(id); pickTab("conversation"); }
                            })();
                        },
                    }}
                />
            )}
            {mobileOpen && <button className="sidebar-backdrop" aria-label="Close navigation" onClick={() => setMobileOpen(false)} />}
            <aside className="side" id="app-sidebar" aria-label="Workspace navigation">
                <div className="side__brand"><LemmaLogo compact={collapsed && !mobileOpen} /><button className="icon-button sidebar-toggle" title={collapsed ? "Expand sidebar (⌘\\)" : "Collapse sidebar (⌘\\)"} aria-label={mobileOpen ? "Close navigation" : collapsed ? "Expand sidebar" : "Collapse sidebar"} aria-expanded={!collapsed} aria-controls="app-sidebar" onClick={() => { if (mobileOpen) setMobileOpen(false); else { setSidebarHidden(false); setCollapsed(v => !v); } }}><SidebarIcon size={19} /></button></div>
                <OrgSwitcher
                    compact={collapsed && !mobileOpen}
                    orgs={orgs.data}
                    activeId={activeOrgId}
                    onPick={(id) => {
                        setOrgId(id);
                        goToPod(null);
                        setConversationId(null);
                    }}
                />
                <Rail
                    compact={collapsed && !mobileOpen}
                    pods={pods.data ?? []}
                    /* Nothing is highlighted while the door is up: `pod` is a
                       stand-in there, and marking it would say you are in a
                       teammate you are looking at from outside. */
                    activeId={stranger ? null : pod?.id ?? null}
                    onPick={(id) => {
                        goToPod(id);
                        setConversationId(null);
                        setSettings(null);
                        setHiring(false);
                        setMobileOpen(false);
                    }}
                    onHire={() => { setSettings(null); setHiring(true); setMobileOpen(false); }}
                    orgId={activeOrgId}
                />
                <div className="side__foot">
                    {/* Above the account, because it is about the account — and
                        silent unless the allowance is close or spent. */}
                    <AllowanceNote
                        orgId={activeOrgId}
                        compact={collapsed && !mobileOpen}
                        onOpenPlan={() => { setSettings("plan"); setMobileOpen(false); }}
                    />
                    {collapsed && !mobileOpen && <button className="side__settings sidebar-hide" title="Hide sidebar" aria-label="Hide sidebar" onClick={() => setSidebarHidden(true)}><CloseIcon size={18} /></button>}
                    <HumanProfile compact={collapsed && !mobileOpen} onOpen={() => { setSettings("account"); setMobileOpen(false); }} />
                </div>
            </aside>

            <main className="main" inert={mobileOpen}>
                {sidebarHidden && <button className="desktop-nav-toggle icon-button" aria-label="Show sidebar" title="Show sidebar" onClick={() => { setSidebarHidden(false); setCollapsed(false); }}><SidebarIcon size={21} /></button>}
                <button className="mobile-nav-toggle icon-button" aria-label="Open navigation" aria-expanded={mobileOpen} aria-controls="app-sidebar" onClick={() => setMobileOpen(true)}><MenuIcon size={22} /></button>
                {settings && <SettingsModal
                    orgs={orgs.data ?? []}
                    activeOrgId={activeOrgId}
                    onPickOrg={(id) => { setOrgId(id); goToPod(null); setConversationId(null); }}
                    initial={settings}
                    onClose={() => setSettings(null)}
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
                    hidden={Boolean(hiring || huddle.expanded || stranger)}
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
                                title="People here"
                                aria-label="People here"
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
                                title="Search this teammate (⌘K)"
                                aria-label="Search this teammate"
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
                        <div className="tabs" role="tablist" aria-label="Views">
                            {allTabs.map((tab) => (
                                <span className={`tab__slot${closingTabs[tab.id] ? " tab__slot--closing" : ""}`} key={tab.id}>
                                    <span className="tab__slot-inner">
                                    <button
                                        ref={tab.id === activeTab?.id ? selectedTabRef : undefined}
                                        className="tab"
                                        role="tab"
                                        aria-selected={tab.id === activeTab?.id}
                                        onClick={() => pickTab(tab.id)}
                                    >
                                        {tab.kind === "conversation" ? <ChatIcon size={17} /> : tab.kind === "profile" ? <ProfileIcon size={17} /> : tab.kind === "history" ? <HistoryIcon size={17} /> : tab.kind === "library" ? <LibraryIcon size={17} /> : tab.kind === "table" ? <TableIcon size={17} /> : tab.kind === "file" ? <FileIcon size={17} /> : tab.kind === "record" ? <TableIcon size={17} /> : tab.kind === "computer" ? <ComputerIcon size={17} /> : <AppsIcon size={17} />}
                                        {tab.label}

                                    </button>
                                    {(tab.kind === "file" || tab.kind === "history" || tab.kind === "table" || tab.kind === "record" || tab.kind === "computer") && (
                                        <button
                                            className="tab__close"
                                            title={"Close " + tab.label}
                                            aria-label={"Close " + tab.label}
                                            onClick={() => closeTab(tab.id)}
                                        >
                                            <CloseIcon size={13} />
                                        </button>
                                    )}
                                    </span>
                                </span>
                            ))}
                            {podTabs.isPending && <span className="tab">…</span>}
                        </div>
                            <ViewActions key={activeTab?.id} tab={activeTab} podId={pod.id}
                                teammate={pod.teammate?.name}
                                onDiscuss={(conversationId) => { setConversationId(conversationId); pickTab("conversation"); }}
                                onNew={() => { setConversationId(NEW_CONVERSATION); pickTab("conversation"); }}
                                onHistory={openHistory}
                                onComputer={openComputer}
                                onReload={() => { const frame = appFrames.current[activeKey]; if (frame && activeTab?.kind === "app") frame.src = activeTab.url; }} />
                        </div>

                        <div className="body">
                            {Object.entries(openedApps).map(([key, url]) => (
                                <iframe
                                    ref={element => { appFrames.current[key] = element; }}
                                    key={key}
                                    className="frame"
                                    title="App"
                                    src={url}
                                    hidden={key !== activeKey}
                                    /* Registered on load, not on mount: an app
                                       that navigates gets a new contentWindow,
                                       and the window that asks the app for
                                       something has to be one we vouched for. */
                                    onLoad={event => {
                                        appFrameGuests.current[key]?.();
                                        appFrameGuests.current[key] = registerFrame(event.currentTarget.contentWindow);
                                    }}
                                />
                            ))}

                            <div className="split" hidden={activeTab?.kind !== "conversation"}>
                                    <div className="convo-host">
                                        {source.label === "live" ? (
                                            <LiveConversation
                                                key={pod.id + ":" + selection.generation}
                                                pod={pod}
                                                conversationId={openConversationId}
                                                fill={fill?.podId === pod.id ? fill : null}
                                                onFilled={() => setFill(null)}
                                                onCreated={id => setSelection(previous => previous.generation === selection.generation
                                                    ? { ...previous, id } : previous)}
                                                onOpenApp={(name) => pickTab("app:" + name)}
                                                onOpenFile={openFile}
                                                onOpenTable={openTable}
                                                onVoice={startCall}
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
                            {activeTab?.kind === "apps" && <AppsPane name={pod.name} tabs={allTabs} onOpen={pickTab} onAsk={(text) => { pickTab("conversation"); asks.current += 1; setFill({ text, id: asks.current, podId: pod.id }); }} />}
                            {activeTab?.kind === "history" && (
                                <AllConversations
                                    pod={pod}
                                    conversationId={conversationId}
                                    onPick={(id) => {
                                        setConversationId(id);
                                        pickTab("conversation");
                                    }}
                                />
                            )}
                            {allTabs.some((tab) => tab.kind === "computer") && (
                                <div className="pane library-pane" hidden={activeTab?.kind !== "computer"}>
                                    {/* The sentinel is a conversation that does not
                                        exist yet, so there is no directory to ask
                                        about — the view opens on the whole machine
                                        instead of fetching a conversation by a name
                                        the server has never seen. */}
                                    <ComputerView
                                        podId={pod.id}
                                        conversationId={openConversationId === NEW_CONVERSATION ? null : openConversationId}
                                        visible={activeTab?.kind === "computer"}
                                    />
                                </div>
                            )}
                            {allTabs.filter((tab): tab is Extract<Tab, {kind: "file"}> => tab.kind === "file").map(tab => (
                                <div className="pane file-tab-pane" key={tab.id} hidden={activeTab?.id !== tab.id}>
                                    <div className="pane__inner"><FileView podId={pod.id} path={tab.path} full /></div>
                                </div>
                            ))}
                            <div className="pane library-pane" hidden={activeTab?.kind !== "library"} key={pod.id + ":library"}>
                                {(activeTab?.kind === "library" || visitedLibraries[pod.id]) && <Library podId={pod.id} onFile={openFile} onTable={name => openTab({ id: "table:" + name, kind: "table", label: readableName(name), name })}/>}
                            </div>
                            {allTabs.filter((tab): tab is Extract<Tab, {kind: "table"}> => tab.kind === "table").map(tab => <div className="pane library-pane" key={pod.id + tab.id} hidden={activeTab?.id !== tab.id}><TableView podId={pod.id} name={tab.name} onOpenRecord={openRecord}/></div>)}
                            {allTabs.filter((tab): tab is Extract<Tab, {kind: "record"}> => tab.kind === "record").map(tab => (
                                <div className="pane library-pane" key={pod.id + tab.id} hidden={activeTab?.id !== tab.id}>
                                    <RecordView
                                        podId={pod.id}
                                        tableName={tab.table}
                                        recordId={tab.recordId}
                                        onOpenTable={openTable}
                                        onOpenRecord={openRecord}
                                    />
                                </div>
                            ))}
                            {activeTab?.kind === "profile" && (
                                <ProfilePane
                                    key={pod.id}
                                    initialSection={preview && demoStep === 2 ? "skills" : entrySection}
                                    openAgentName={openAgentName}
                                    onOpenAgentName={setOpenAgentName}
                                    /* Same path a widget's compose request
                                       takes: land on the conversation and put
                                       the words in the box, rather than
                                       sending them. What gets asked for is
                                       still the person's to edit or drop. */
                                    onAskFor={(text) => {
                                        pickTab("conversation");
                                        asks.current += 1;
                                        setFill({ text, id: asks.current, podId: pod.id });
                                    }}
                                    pod={pod}
                                    orgName={activeOrg?.name ?? "this organization"}
                                    others={(pods.data ?? []).filter((other) => other.id !== pod.id)}
                                    onOpenTab={pickTab}
                                    onPickPod={(id) => {
                                        goToPod(id);
                                        setConversationId(null);
                                    }}
                                    onMessage={() => pickTab("conversation")}
                                    onDiscussAgent={(name) => {
                                        void (async () => {
                                            const id = await discussion.open("agent", name);
                                            if (id) { setConversationId(id); pickTab("conversation"); }
                                        })();
                                    }}
                                    onDiscussWorkflow={(name) => {
                                        void (async () => {
                                            const id = await discussion.open("workflow", name);
                                            if (id) { setConversationId(id); pickTab("conversation"); }
                                        })();
                                    }}
                                />
                            )}
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

            {addingPeople && pod && (
                <Modal
                    title={"Add someone to " + pod.name}
                    subtitle="Anyone already in this organization"
                    onClose={() => setAddingPeople(false)}
                >
                    <AddPeople pod={pod} orgId={activeOrgId} onDone={() => setAddingPeople(false)} />
                </Modal>
            )}
        </div>
    );
}
