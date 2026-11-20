"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type Org, type Pod } from "@/data";
import { lemma } from "@/session/client";
import { key } from "@/session/storage";
import { readAddress } from "@/shell/address";
import { NotYours } from "@/shell/not-yours";
import { isForbidden } from "@/session/auth-state";
import { useAnalyticsIdentity } from "@/site/analytics/identity";
import { settingsFromQuery } from "@/site/legacy-address";
import { isAboutSection, settingsSectionOf } from "@/space/about-sections";
import { acknowledge, readComposeRequest } from "@/thread/compose-bridge";
import { gather } from "@/workflow/waiting-inbox";
import { useConversationAsks } from "@/thread/use-conversation-asks";
import { byAge, CONVERSATION_ASKS } from "@/thread/waiting-on-you";
import { channelTitle, type Channel } from "@/channels/model";
import { listStamp } from "@/data/stamp";
import { SettingsModal, type SettingsSection } from "@/settings/settings-modal";
import { NewOrgDialog } from "@/org/new-org-dialog";
import { ReachSheet } from "@/shell/reach";
import { ShareSheet, type ShareSubject } from "@/space/share-sheet";
import { AddPeople } from "@/shell/add-people";
import { AtTheDoor } from "@/shell/at-the-door";
import { Modal } from "@/shell/modal";
import { ReconnectStrip } from "@/shell/reconnect-strip";
import { DesktopNotices } from "@/desktop/desktop-notices";
import { FirstProfileStep } from "@/session/first-profile-step";
import { useOpenSettingsEvent } from "@/desktop/open-settings";
import {
    NEW_CHAT,
    HOME, activityOfRecents, chatOpenOfView, chatView, daysOfRecents, outcomeView, podOfView, queueFromNeeds, queueItems, readTrialView, readView, refreshKept, viewOfAddress, writeView,
    type AskWith, type ContactWaiting, type Fill, type Go, type Handoff, type KeptApp, type View,
} from "./model";
import { useNeedsYou, usePodActivity, usePodDetails, useRecents } from "./data";
import { looksNamed, named, slug, unnamed, type Names } from "./named-address";
import { appName as nameOfApp, appsOf } from "./lemma-apps";
import { FrameArrival } from "./arrival";
import { firstLemma } from "./first-lemma";
import { ArrivalFlow, type ArrivalStart, type Landing } from "@/arrival/flow";
import { asksWorkOn, stillAsksWork } from "@/arrival/work";
import { NewLemmaDialog } from "./new-lemma";
import { Bars, Icon, Empty } from "./parts";
import { Sidebar } from "./sidebar";
import { Today } from "./today";
import { AppsPage } from "./apps";
import { ConnectorsPage } from "./connectors";
import { OutcomesList } from "./list";
import { NeedsYou } from "./needs";
import { History } from "./history";
import { People } from "./people";
import { OutcomePage } from "./outcome";
import { ChatPane, useContactsWaiting } from "./chats";
import { AgentView, AppView, ComputerPage, FilePage, RecordPage, RunView, SettingsView, TablePage, WorkflowView } from "./stage";
import type { FrameActions } from "./actions";
import { Palette } from "./palette";
import { CallDock, CallStage, useCalls } from "./calls";

/** The organization the workspace last opened, shared with the classic
 *  workspace (`/l`) so the two agree on where you are. */
const ORG_KEY = key("org");
/** No Lemmas: for a read that is made per Lemma only where the organization
 *  has no one read for it. */
const NO_PODS: readonly Pod[] = [];
/** Whether the reader put the sidebar away: theirs, on this browser. */
const SIDE_KEY = key("outcomes-side-closed");
/** The Lemma the page arrived at, before the reader has gone anywhere: a
 *  word no Lemma's id is. */
const ARRIVAL = "arrival";

function storedSideClosed(): boolean {
    try {
        return localStorage.getItem(SIDE_KEY) === "1";
    } catch {
        return false;
    }
}

/** The app a remix link names, if it is a web address at all. */
function remixOf(raw: string | null): string | null {
    if (!raw) return null;
    try {
        const url = new URL(raw);
        return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
    } catch {
        return null;
    }
}

/** A workspace address as a view here, with the old `?section=` a link may
 *  carry for a part of About folded in, as the workspace reads it. */
function viewOfLink(path: string, query: URLSearchParams): View | null {
    const address = readAddress(path);
    const asked = query.get("section");
    return viewOfAddress(!address.section && isAboutSection(asked) ? { ...address, section: asked } : address);
}

function storedOrg(): string | null {
    try {
        const raw = localStorage.getItem(ORG_KEY);
        return raw ? (JSON.parse(raw) as string) : null;
    } catch {
        return null;
    }
}

export interface Me {
    id?: string;
    email?: string;
    first_name?: string;
    last_name?: string;
    /** What they said they do, once asked; absent from a server that does
     *  not ask. */
    work?: unknown;
    created_at?: string;
}

/** How many steps into the frame this history entry is, kept on the entry
 *  itself so browser Back and Forward keep it true. `go` passes only this
 *  key: Next.js copies its own state onto the entry, and an object that
 *  already carried that state would skip the router's sync. */
function step(): number {
    return (window.history.state as { oxStep?: number } | null)?.oxStep ?? 0;
}

export function nameOf(me: Me | undefined): string {
    return [me?.first_name, me?.last_name].filter(Boolean).join(" ").trim() || me?.email?.split("@")[0] || "You";
}

/** Views that fill the main area and scroll inside themselves: a chat, and
 *  anything inside a Lemma that the workspace draws as its own pane. */
function fills(view: View): boolean {
    return view.name === "chats" || view.name === "run" || view.name === "workflow" || view.name === "app" || view.name === "file"
        || view.name === "table" || view.name === "record" || view.name === "agent" || view.name === "computer";
}

/** The outcome-first frame: a sidebar of places and of every outcome with
 *  its chats, and whatever is open beside it. The sidebar names where people
 *  go and talk, not the kinds of things inside a pod; those are one level
 *  down, in an outcome's X-ray. Everything opens in here. */
export function OutcomesShell() {
    const pathname = usePathname();
    /* What the address asked for on arrival besides a place: the query words
       links from email, WhatsApp and a connector's round trip still carry.
       Read once; each is answered where its state starts below. */
    const [arrival] = useState(() => new URLSearchParams(window.location.search));
    /* Where the frame was last, for a workspace address to fall back on: an
       embedded thread or chat page writes its links as workspace addresses,
       and following one should stay here. */
    const last = useRef<View>({ name: "today" });

    /* Read ahead of the address: an address may name the organization and
       the Lemma, and these are what say which they are. */
    const orgs = useQuery({ queryKey: ["orgs"], queryFn: () => source.listOrgs(), staleTime: 10 * 60_000 });
    const [chosen, setChosen] = useState<string | null>(() => arrival.get("org") ?? storedOrg());
    const orgId = orgs.data?.some((one) => one.id === chosen) ? chosen : orgs.data?.[0]?.id ?? null;
    const org: Org | null = orgs.data?.find((one) => one.id === orgId) ?? null;
    const pods = useQuery({
        queryKey: ["pods", orgId],
        queryFn: () => source.listPods(orgId as string),
        enabled: Boolean(orgId),
        staleTime: 5 * 60_000,
    });
    const list = useMemo(() => pods.data ?? [], [pods.data]);

    const names = useMemo<Names | null>(
        () => (org && pods.isSuccess ? { org: org.name, lemmas: list.map((one) => ({ id: one.id, name: one.name })) } : null),
        [org, pods.isSuccess, list],
    );
    const path = pathname ?? HOME;
    /* An address spelled in names says nothing until the names are read. It
       waits where it is rather than being taken for an old link and
       rewritten as one. */
    const unread = !names && looksNamed(path);
    const plain = useMemo(() => (names ? unnamed(path, names) ?? path : path), [names, path]);
    /* The app's own address first; then the frame's first one (`/t/outcome/…`)
       and the workspace's (`/l/{pod}/conversation/…`), which links written
       before the app moved still carry. */
    const view = useMemo<View>(() => {
        if (unread) return last.current;
        return readView(plain) ?? readTrialView(plain) ?? viewOfLink(plain, arrival) ?? last.current;
    }, [plain, arrival, unread]);
    useEffect(() => { last.current = view; }, [view]);
    useEffect(() => {
        if (unread) return;
        const search = window.location.search + window.location.hash;
        if (readView(plain)) {
            /* The app's own address, written back in names where it has them. */
            const spelled = names ? named(plain, names) : path;
            if (spelled !== path) window.history.replaceState(window.history.state, "", spelled + search);
            return;
        }
        /* An old address, written back as the app's own for the same place,
           the query kept for whatever on the page still has to read it. One
           that names nowhere it knows opens Home. */
        const here = readTrialView(plain) ?? viewOfLink(plain, arrival) ?? { name: "today" };
        window.history.replaceState(window.history.state, "", (names ? named(writeView(here), names) : writeView(here)) + search);
    }, [plain, path, arrival, unread, names]);
    /* A named address in another of the reader's organizations: the frame
       moves there, as it does for a link that names a Lemma by its id. */
    useEffect(() => {
        if (!org || !orgs.data || !looksNamed(path)) return;
        const word = path.split("/").filter(Boolean)[1];
        if (slug(org.name) === word) return;
        const other = orgs.data.find((one) => slug(one.name) === word);
        if (other) setChosen(other.id);
    }, [org, orgs.data, path]);
    /* `go` is one function for the life of the frame, so it reads the names
       as they are when it is called. */
    const spelling = useRef<Names | null>(null);
    useEffect(() => { spelling.current = names; }, [names]);
    /* Which Lemma was asked for as itself, to open on its first app: the one
       the page arrived at, until the reader goes anywhere; then whichever
       one a list's name was followed to. */
    const [landing, setLanding] = useState<string | null>(ARRIVAL);

    /* Words on their way to a chat: what Home or ⌘K asked, or what an app put
       in the box. Both belong to the navigation that carried them, and any
       other step drops them, so they never land in a chat they were not for. */
    const [handoff, setHandoff] = useState<Handoff | null>(null);
    const [fill, setFill] = useState<Fill | null>(null);
    const [navOpen, setNavOpen] = useState(false);
    const [sideClosed, setSideClosed] = useState(storedSideClosed);
    const toggleSide = useCallback(() => setSideClosed((was) => !was), []);
    useEffect(() => {
        try { localStorage.setItem(SIDE_KEY, sideClosed ? "1" : "0"); } catch { /* kept for this visit only */ }
    }, [sideClosed]);
    useEffect(() => {
        const onKey = (event: KeyboardEvent) => {
            if ((event.metaKey || event.ctrlKey) && (event.key === "\\" || event.code === "Backslash")) {
                event.preventDefault();
                toggleSide();
            }
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [toggleSide]);
    const go = useCallback<Go>((next, options = {}) => {
        const url = spelling.current ? named(writeView(next), spelling.current) : writeView(next);
        setLanding(options.landing && next.name === "outcome" && next.tab === "home" ? next.podId : null);
        if (options.replace) {
            window.history.replaceState({ oxStep: step() }, "", url);
            if (options.handoff) setHandoff(options.handoff);
            if (options.fill) setFill(options.fill);
            return;
        }
        window.history.pushState({ oxStep: step() + 1 }, "", url);
        setHandoff(options.handoff ?? null);
        setFill(options.fill ?? null);
        setNavOpen(false);
        if (!fills(next)) document.querySelector(".ox-main")?.scrollTo({ top: 0 });
    }, []);
    const back = useCallback((fallback: View) => {
        if (step() > 0) {
            window.history.back();
        } else {
            go(fallback);
        }
    }, [go]);
    const sentHandoff = useCallback(() => setHandoff((was) => (was ? { ...was, sent: true } : was)), []);
    const { huddle, calls, resume } = useCalls(go);
    /* The call has the main side beside its own chat; stepping away from that
       chat folds it to the bar. Only the step folds it -- going back to the
       call opens its chat and expands it in one go, and an expand that lands
       before the address has moved must not be folded straight back. */
    const { expanded: calling, collapse: foldCall, callConversationId: callHeldIn } = huddle;
    const atCall = view.name === "chats" && view.open.kind === "chat" && view.open.id === callHeldIn;
    useEffect(() => { if (!atCall) foldCall(); }, [atCall, foldCall]);
    const filled = useCallback(() => setFill(null), []);
    const queryClient = useQueryClient();

    const pickOrg = useCallback((id: string) => {
        setChosen(id);
        try { localStorage.setItem(ORG_KEY, JSON.stringify(id)); } catch { /* a private window keeps the choice for this tab only */ }
        go({ name: "today" });
    }, [go]);

    const me = useQuery({
        queryKey: ["current-user"],
        queryFn: () => lemma().users.current() as Promise<Me | undefined>,
        enabled: source.label !== "sample",
        staleTime: 5 * 60_000,
    });
    useAnalyticsIdentity(source.label === "sample" ? null : { userId: me.data?.id, organizationId: orgId, podId: podOfView(view) });

    /* What waits on you is the organization's one list (#1045's needs-you):
       pauses, forms, decisions, contacts and the questions a Lemma asked in
       your chat with it, in one read. The sample source has no such read, so
       it keeps the per-Lemma ones. */
    const sampled = source.label !== "live";
    const needs = useNeedsYou(sampled ? null : orgId);
    /* A form is filled in from Needs you, and its wait carries the form, so
       the waits are read -- but only in the Lemmas the one list says have a
       form waiting, which is usually none of them. */
    const owing = useMemo(() => {
        if (sampled) return list;
        const withForm = new Set((needs.data ?? []).flatMap((need) => (need.kind === "FORM" ? [need.podId] : [])));
        return list.filter((one) => withForm.has(one.id));
    }, [sampled, list, needs.data]);
    const waits = useQuery({
        queryKey: ["workflow-waiting", owing.map((one) => one.id).join(",")],
        queryFn: () => gather(owing, source.label === "sample", queryClient),
        enabled: owing.length > 0,
        staleTime: 60_000,
    });
    const asked = useConversationAsks(list, { enabled: sampled });
    /* People outside a Lemma who wrote in and wait on a person: answered
       from Needs you like any other question, not listed in the sidebar.
       Their live feeds are still what notices one arriving first. */
    const [contactsWaiting, contactFeeds] = useContactsWaiting(sampled ? list : NO_PODS);
    const contactsSaid = useMemo(() => [...contactsWaiting.values()].flat().map((channel) => channel.id).sort().join(","), [contactsWaiting]);
    useEffect(() => {
        if (!sampled) void queryClient.invalidateQueries({ queryKey: [CONVERSATION_ASKS, "organization"] });
    }, [contactsSaid, sampled, queryClient]);
    const contacts = useMemo<ContactWaiting[]>(() => list.flatMap((pod) => (contactsWaiting.get(pod.id) ?? []).map((channel) => {
        const at = Date.parse(channel.attention?.since ?? channel.lastMessageAt ?? channel.createdAt);
        return {
            podId: pod.id,
            podName: pod.name,
            channelId: channel.id,
            name: channelTitle(channel, pod.name),
            reason: channel.attention?.reason ?? null,
            sinceMs: Number.isNaN(at) ? Date.now() : at,
        };
    })), [list, contactsWaiting]);
    const queue = useMemo(
        () => (sampled ? queueItems(byAge(asked.data ?? []).fresh, waits.data?.rows ?? [], contacts) : queueFromNeeds(needs.data ?? [], waits.data?.rows ?? [])),
        [sampled, asked.data, waits.data, contacts, needs.data],
    );
    const queueLoading = sampled ? asked.isPending || waits.isPending : needs.isPending;
    /* Live, the queue is this one read: with nothing read, an empty queue
       is not "nothing waiting". */
    const queueFailed = !sampled && needs.isError && needs.data === undefined;
    const { refetch: refetchNeeds } = needs;
    const retryQueue = useCallback(() => void refetchNeeds(), [refetchNeeds]);
    /* Every Lemma's recent chats and runs come from the organization's one
       overview read. The Lemma in view also reads its own list: longer, and
       patched as its chats are renamed or put away, so the place the reader
       is working in stays exact. If the overview fails or is too slow, each
       Lemma is read on its own, as before it existed. */
    const recents = useRecents(sampled ? null : orgId);
    const overviewDown = !sampled && recents.isError && recents.data === undefined;
    const inView = podOfView(view);
    const readAlone = useMemo(
        () => (sampled || overviewDown ? list : list.filter((one) => one.id === inView)),
        [sampled, overviewDown, list, inView],
    );
    const alone = usePodActivity(readAlone);
    const activity = useMemo(() => {
        if (sampled || overviewDown) return alone.byPod;
        const all = activityOfRecents(recents.data?.pods ?? [], (iso) => listStamp(iso));
        if (!alone.pending) for (const [podId, rows] of alone.byPod) all.set(podId, rows);
        return all;
    }, [sampled, overviewDown, alone.byPod, alone.pending, recents.data]);
    /* The board's bars: the overview counts every conversation by day,
       where a Lemma's own list holds only its latest. */
    const days = useMemo(() => (sampled || overviewDown ? null : daysOfRecents(recents.data?.pods ?? [])), [sampled, overviewDown, recents.data]);
    /* What waits on the reader in each Lemma, for the dot on a folded one. */
    const waitingIn = useMemo(() => {
        const count = new Map<string, number>();
        for (const item of queue) count.set(item.podId, (count.get(item.podId) ?? 0) + 1);
        return count;
    }, [queue]);
    /* The shared chats the sidebar has read, for Home's list of recent ones. */
    const [sideChannels, setSideChannels] = useState<ReadonlyMap<string, readonly Channel[]>>(() => new Map());
    /* Asking -- from Home, ⌘K or an outcome -- starts a chat in the outcome
       named and opens it. The pane that shows it sends the words, so the
       reply streams into the thread on screen; without a live session the
       words wait in the box instead. */
    const asks = useRef(0);
    const ask = useCallback((podId: string, text: string, extra: AskWith = {}) => {
        asks.current += 1;
        const target = chatView({ kind: "chat", podId, id: NEW_CHAT });
        if (source.label === "live") go(target, { handoff: { text, id: asks.current, podId, sent: false, createWith: extra.createWith, files: extra.files, folder: extra.folder } });
        else go(target, { fill: { text, id: asks.current, podId, files: extra.files } });
    }, [go]);
    /* Words for a chat's box, without sending: what a table's "ask" and an
       app's button put there. */
    const prompt = useCallback((podId: string, text: string, fresh = true) => {
        asks.current += 1;
        const words: Fill = { text, id: asks.current, podId };
        const open = chatOpenOfView(view);
        if (!fresh && open.kind === "chat" && open.podId === podId) setFill(words);
        else go(chatView({ kind: "chat", podId, id: NEW_CHAT }), { fill: words });
    }, [go, view]);

    /* A Lemma asked for as itself opens on an app, where it has one: the one
       somebody chose for it, else its first. Its apps are how people use
       it, and Home is a list of them. A Lemma with none stays on Home, which
       says how to start it. */
    const landingPod = view.name === "outcome" && view.tab === "home" && (landing === ARRIVAL || landing === view.podId) ? view.podId : null;
    const landed = useQuery({
        queryKey: ["outcome-apps", landingPod],
        queryFn: () => source.listTabs(landingPod as string),
        enabled: Boolean(landingPod),
        staleTime: 5 * 60_000,
    });
    const landingHome = list.find((one) => one.id === landingPod)?.homeApp;
    useEffect(() => {
        /* The list says which app was chosen: landing before it is read
           would open the first app and then stay there. */
        if (!landingPod || !landed.data || !pods.data) return;
        const first = appsOf(landed.data, landingHome)[0];
        if (first) go({ name: "app", podId: landingPod, app: nameOfApp(first) }, { replace: true });
        else setLanding(null);
    }, [landingPod, landed.data, pods.data, landingHome, go]);

    /* Where the app in front says it is, and the app it asks for. Either is a
       step the address takes, so Back undoes it and the app is told. */
    const moveApp = useCallback((podId: string, app: string, path: string) => {
        const now = last.current;
        const at = path === "/" ? undefined : path;
        if (now.name === "app" && now.podId === podId && now.app === app && now.at === at) return;
        go(at ? { name: "app", podId, app, at } : { name: "app", podId, app });
    }, [go]);

    /* Two arrivals that start something rather than open a place, answered
       once: `?hire=1`, from the old create-a-pod door, and `?remixSource=`,
       an app somebody chose to rebuild, which lands on the Lemma they picked
       with the ask waiting in a new chat's box. */
    const arrived = useRef(false);
    useEffect(() => {
        if (arrived.current) return;
        arrived.current = true;
        if (arrival.get("hire") === "1") {
            go({ name: "hire" }, { replace: true });
            return;
        }
        const remix = remixOf(arrival.get("remixSource"));
        const podId = podOfView(view);
        if (!remix || !podId) return;
        asks.current += 1;
        go(chatView({ kind: "chat", podId, id: NEW_CHAT }), { replace: true, fill: { text: "Help me rebuild or adapt this app for our workspace: " + remix, id: asks.current, podId } });
    }, [arrival, go, view]);

    /* The one door from a framed app or widget into a chat: it fills a box
       and brings it forward, and the reader decides. A request from a frame
       this page did not put on screen reads as nothing. */
    useEffect(() => {
        const listen = (event: MessageEvent) => {
            const request = readComposeRequest(event);
            if (!request) return;
            const podId = podOfView(view);
            if (!podId) return;
            prompt(podId, request.text, request.newConversation);
            acknowledge(event, request);
        };
        window.addEventListener("message", listen);
        return () => window.removeEventListener("message", listen);
    }, [view, prompt]);

    /* The workspace's own dialogs, opened from anywhere in the frame. */
    const [settings, setSettings] = useState<{ section: SettingsSection; focus: string | null; request: number } | null>(() => {
        /* `?connect=` is a connector's round trip coming home, and the panel
           that reads it has to be open; an old `?section=` naming a part of
           the organization opens Settings there. */
        const section = settingsFromQuery(arrival.get("settings")) ?? (arrival.get("connect") ? "connectors" : null) ?? settingsSectionOf(arrival.get("section"));
        return section ? { section, focus: null, request: 1 } : null;
    });
    const [makingOrg, setMakingOrg] = useState(false);
    /* The way in, held open while it runs: making somebody's organization
       fills the list of organizations, and the frame must not take over in
       the middle of a step. `first` is latched the moment the list comes
       back empty; the others are opened from here. */
    const firstArrival = orgs.isSuccess && orgs.data.length === 0;
    const [arriving, setArriving] = useState<ArrivalStart | "first" | null>(null);
    useEffect(() => {
        if (firstArrival) setArriving((was) => was ?? "first");
    }, [firstArrival]);
    /* Somebody who came in another way -- a chat app, an install page -- and
       has not been asked what they do, is asked once here, when they come to
       Home. Never over a link to a chat, an app or a Lemma: they came for
       that, as a link wins over a place of their own on the first arrival.
       Ending the step any way at all, a failed save included, stops it for
       this visit. */
    const asksWork = source.label === "live" && stillAsksWork(me.data);
    const asksName = Boolean(me.data) && !(me.data?.first_name ?? "").trim();
    const [workSettled, setWorkSettled] = useState(false);
    /* Read off the address itself, not the view: while a named address waits
       for its names, and while an old link is being rewritten, the view
       stands at Home without the reader being there. */
    const workHere = !unread && asksWorkOn(readView(plain)?.name ?? "");
    useEffect(() => {
        if (asksWork && workHere && !workSettled && orgs.isSuccess && orgs.data.length > 0) setArriving((was) => was ?? { kind: "work" });
    }, [asksWork, workHere, workSettled, orgs.isSuccess, orgs.data]);
    const showingArrival = arriving ?? (firstArrival ? "first" : null);
    /* A new Lemma is made in a dialog over wherever the reader is, as a chat
       app makes a channel; its old address (`/l/new`) opens the same dialog
       over the list of Lemmas. */
    const [makingLemma, setMakingLemma] = useState(view.name === "hire");
    useEffect(() => {
        if (view.name === "hire") setMakingLemma(true);
    }, [view.name]);
    const [reaching, setReaching] = useState<string | null>(() => (arrival.get("reach") === "1" ? podOfView(view) : null));
    const [shared, setShared] = useState<{ podId: string; subject: ShareSubject } | null>(null);
    const sharing = shared?.podId ?? null;
    const [addingPeople, setAddingPeople] = useState<string | null>(null);
    const openSettings = useCallback((section: SettingsSection = "account", focus: string | null = null) => {
        setSettings((was) => ({ section, focus, request: (was?.request ?? 0) + 1 }));
    }, []);
    useOpenSettingsEvent(openSettings);
    const actions = useMemo<FrameActions>(() => ({
        openSettings: (section) => openSettings(section),
        connect: setReaching,
        share: (podId, subject = { kind: "space" }) => setShared({ podId, subject }),
        addPeople: setAddingPeople,
        newLemma: () => setMakingLemma(true),
        prompt: (podId, text) => prompt(podId, text),
        fill: (podId, text) => prompt(podId, text, false),
        ask: (podId, text, createWith, files) => ask(podId, text, { createWith, files }),
    }), [openSettings, prompt, ask]);

    const [palette, setPalette] = useState<{ open: boolean; podId: string | null }>({ open: false, podId: null });
    const openPalette = useCallback((podId: string | null = null) => setPalette({ open: true, podId }), []);
    useEffect(() => {
        const onKey = (event: KeyboardEvent) => {
            if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
                event.preventDefault();
                setPalette((now) => ({ open: !now.open, podId: podOfView(view) }));
            }
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [view]);

    /* Every app opened this visit stays mounted, hidden while something else
       is in front, so coming back to one never cold-boots it -- the one thing
       the workspace's README says must never happen to an app. */
    const [keptApps, setKeptApps] = useState<readonly KeptApp<Pod>[]>([]);
    const appPod = view.name === "app" ? view.podId : null;
    const appName = view.name === "app" ? view.app : null;
    const keeps = (list: readonly KeptApp<Pod>[]) => list.some((each) => each.podId === appPod && each.app === appName);
    const kept = appPod && appName && !keeps(keptApps) ? [...keptApps, { podId: appPod, app: appName, orgId, pod: null }] : keptApps;
    useEffect(() => {
        if (!appPod || !appName) return;
        setKeptApps((was) => (was.some((each) => each.podId === appPod && each.app === appName) ? was : [...was, { podId: appPod, app: appName, orgId, pod: null }]));
    }, [appPod, appName, orgId]);

    const myName = nameOf(me.data);
    const scoped = podOfView(view);
    const enterFrom = useCallback(async (landing: Landing) => {
        const was = arriving;
        /* The organization list is read again before the way in lets go:
           closed over a list still empty, it would open again at once. */
        await Promise.all([
            queryClient.invalidateQueries({ queryKey: ["orgs"] }),
            queryClient.invalidateQueries({ queryKey: ["pods"] }),
        ]).catch(() => undefined);
        setArriving(null);
        setWorkSettled(true);
        if (landing.orgId) {
            setChosen(landing.orgId);
            try { localStorage.setItem(ORG_KEY, JSON.stringify(landing.orgId)); } catch { /* a private window keeps the choice for this tab only */ }
        }
        if (landing.startNew) {
            go({ name: "hire" });
            return;
        }
        /* Only the job question: they were somewhere already, and stay. */
        if (was && was !== "first" && was.kind === "work") return;
        /* Already at the Lemma they came for: stay on the exact address,
           section and all, rather than its summary. */
        if (landing.podId && landing.podId === scoped) return;
        go(landing.podId ? outcomeView(landing.podId) : { name: landing.orgId ? "today" : "outcomes" });
    }, [arriving, queryClient, go, scoped]);
    /* Who is in a Lemma costs two reads apiece, so it is asked only for the
       pods something on screen reads it for: every one on People and the
       Lemmas board, which list rosters and owners; otherwise the one in view
       and any whose sheet is open. */
    const rostered = useMemo(() => {
        if (view.name === "people" || view.name === "outcomes") return list;
        const wanted = new Set([scoped, reaching, sharing, addingPeople].filter(Boolean));
        return list.filter((one) => wanted.has(one.id));
    }, [list, view.name, scoped, reaching, sharing, addingPeople]);
    const details = usePodDetails(rostered);
    const withPeople = useMemo(() => list.map((pod) => {
        const detail = details.get(pod.id);
        return detail ? { ...pod, ...detail } : pod;
    }), [list, details]);
    const pod = scoped && view.name !== "chats" ? withPeople.find((one) => one.id === scoped) ?? null : null;

    /* Each kept app holds the Lemma it was last drawn with, so a list that
       leaves that Lemma out -- another organization's, or one being read
       again -- does not unmount it. Only its own organization's list, read
       and without it, lets the app go. */
    useEffect(() => {
        if (!pods.isSuccess) return;
        setKeptApps((was) => refreshKept(was, withPeople, orgId));
    }, [withPeople, pods.isSuccess, orgId]);

    /* A link to a Lemma this organization's list does not have -- from
       WhatsApp, an email, a teammate: one in another of your organizations,
       which the frame moves to, or one you are not in, which offers the
       workspace's own door to ask to join. */
    const unlisted = scoped && pods.isSuccess && !list.some((one) => one.id === scoped) ? scoped : null;
    const linked = useQuery({
        queryKey: ["pod-access", unlisted],
        queryFn: () => source.getPod(unlisted as string),
        enabled: Boolean(unlisted),
        retry: false,
        staleTime: 0,
    });
    const linkedOrg = linked.data && linked.data.id === unlisted ? linked.data.orgId : null;
    const elsewhere = linkedOrg && linkedOrg !== orgId && orgs.data?.some((one) => one.id === linkedOrg) ? linkedOrg : null;
    useEffect(() => {
        if (!elsewhere) return;
        setChosen(elsewhere);
        try { localStorage.setItem(ORG_KEY, JSON.stringify(elsewhere)); } catch { /* a private window keeps the choice for this tab only */ }
    }, [elsewhere]);
    const stranger = unlisted && linked.isError && isForbidden(linked.error) ? unlisted : null;

    /* The way in, one look on every door: somebody who belongs nowhere yet
       (an invitation accepted, their company's organization joined, or one
       made with a Lemma everyone in it shares), an invitation just accepted
       from Home, or the job question somebody has not been asked. */
    if (showingArrival === "first") {
        return <FrameArrival linkedPodId={scoped} asksWork={asksWork} asksName={asksName} onEntered={enterFrom} />;
    }
    if (showingArrival) {
        return <ArrivalFlow start={showingArrival} asksWork={asksWork} asksName={asksName} onEntered={enterFrom} />;
    }

    let page;
    const orgsFailed = orgs.isError && !org;
    const podsFailed = pods.isError && !pods.data;
    if (orgsFailed || podsFailed) {
        /* Asks again for whichever read put this here: an org list that
           failed in the background with its data still cached is not one. */
        const retry = () => {
            if (orgsFailed) void orgs.refetch();
            if (podsFailed) void pods.refetch();
        };
        page = (
            <div className="ox-page">
                <Empty title="Couldn’t load your workspace.">
                    <button type="button" className="ox-btn" onClick={retry}>Try again</button>
                </Empty>
            </div>
        );
    } else if (!org || pods.isPending) {
        page = <p className="ox-loading">Loading {org?.name ?? "your organization"}…</p>;
    } else if (view.name === "today") {
        page = <Today org={org} pods={withPeople} activity={activity} channels={sideChannels} queue={queue} queueFailed={queueFailed} onRetryQueue={retryQueue} me={myName} go={go} onAsk={ask} onJoined={(invite) => setArriving({ kind: "joined", orgId: invite.orgId, orgName: invite.orgName, podId: invite.podId })} />;
    } else if (view.name === "apps") {
        page = <AppsPage pods={withPeople} go={go} />;
    } else if (view.name === "connectors") {
        page = <ConnectorsPage org={org} />;
    } else if (view.name === "outcomes" || view.name === "hire") {
        page = <OutcomesList org={org} pods={withPeople} activity={activity} days={days} queue={queue} go={go} actions={actions} />;
    } else if (view.name === "needs") {
        page = <NeedsYou pods={withPeople} queue={queue} loading={queueLoading} failed={queueFailed} onRetry={retryQueue} go={go} />;
    } else if (view.name === "people") {
        page = <People pods={withPeople} go={go} />;
    } else if (view.name === "history") {
        page = <History org={org} pods={withPeople} podId={view.podId} go={go} />;
    } else if (stranger) {
        page = (
            <div className="ox-hire">
                <NotYours podId={stranger} onArrived={() => { void pods.refetch(); void linked.refetch(); }} />
            </div>
        );
    } else if (unlisted && (linked.isPending || elsewhere)) {
        page = <p className="ox-loading">Opening…</p>;
    } else if (view.name === "chats") {
        page = <ChatPane pods={withPeople} open={view.open} activity={activity} go={go} actions={actions} calls={calls} handoff={handoff} onHandoffSent={sentHandoff} fill={fill} onFilled={filled} />;
    } else if (!pod) {
        page = <div className="ox-page"><Empty title="This Lemma isn’t one you can open."><p>It may have been removed, or you were never added to it.</p></Empty></div>;
    } else if (view.name === "outcome" && view.tab === "settings") {
        page = <SettingsView key={pod.id} pod={pod} org={org} section={view.section ?? null} go={go} actions={actions} />;
    } else if (view.name === "outcome" && landingPod === pod.id && landed.isPending) {
        /* About to open on its first app, or on Home if it has none: not
           Home for a moment and then the app. */
        page = <p className="ox-loading">Opening {pod.name}…</p>;
    } else if (view.name === "outcome") {
        page = <OutcomePage pod={pod} pods={list} orgName={org.name} tab={view.tab} section={view.section ?? null} activity={activity.get(pod.id) ?? []} queue={queue.filter((item) => item.podId === pod.id)} me={myName} go={go} onAsk={ask} actions={actions} />;
    } else if (view.name === "run") {
        page = <RunView key={view.runId} pod={pod} runId={view.runId} go={go} back={back} />;
    } else if (view.name === "workflow") {
        page = <WorkflowView key={view.workflow} pod={pod} workflow={view.workflow} go={go} />;
    } else if (view.name === "app") {
        /* Drawn below, with every app opened this visit. */
        page = null;
    } else if (view.name === "file") {
        page = <FilePage key={view.path} pod={pod} path={view.path} go={go} actions={actions} />;
    } else if (view.name === "table") {
        page = <TablePage key={view.table} pod={pod} table={view.table} go={go} actions={actions} />;
    } else if (view.name === "record") {
        page = <RecordPage key={view.table + view.recordId} pod={pod} table={view.table} recordId={view.recordId} go={go} actions={actions} />;
    } else if (view.name === "computer") {
        page = <ComputerPage key={view.conversationId ?? "machine"} pod={pod} conversationId={view.conversationId} go={go} />;
    } else if (view.name === "agent") {
        page = <AgentView key={view.agent} pod={pod} agent={view.agent} go={go} actions={actions} back={back} />;
    }

    return (
        <div className={"ox" + (sideClosed ? " ox--railed" : "")} data-nav={navOpen || undefined}>
            {contactFeeds}
            <Sidebar
                org={org}
                orgs={orgs.data ?? []}
                onPickOrg={pickOrg}
                pods={withPeople}
                view={view}
                go={go}
                activity={activity}
                needs={queue.length}
                waitingIn={waitingIn}
                onChannels={setSideChannels}
                me={myName}
                onFind={() => openPalette(podOfView(view))}
                onSettings={() => openSettings("account")}
                onPlan={() => openSettings("plan")}
                onNewOrg={() => setMakingOrg(true)}
                onNewLemma={() => setMakingLemma(true)}
                onAddApp={(podId) => prompt(podId, "Build an app that ")}
                open={navOpen}
                closed={sideClosed}
                onToggle={toggleSide}
            />
            {navOpen && <button type="button" className="ox-navscrim" aria-label="Close the menu" onClick={() => setNavOpen(false)} />}
            <div className="ox-body">
                <header className="ox-topbar">
                    <button type="button" className="ox-topbar__menu" aria-label="Menu" aria-expanded={navOpen} onClick={() => setNavOpen((was) => !was)}><Icon name="menu" size={18} /></button>
                    <Bars size={18} />
                    <span className="ox-topbar__org">{org?.name ?? "Lemma"}</span>
                    <button type="button" className="ox-topbar__find" aria-label="Ask or find" onClick={() => openPalette(podOfView(view))}><Icon name="search" size={16} /></button>
                </header>
                <main className={"ox-main" + (fills(view) ? " ox-main--fill" : "") + (calling ? " ox-main--calling" : "")}>
                    {calling && <CallStage huddle={huddle} go={go} />}
                    {page}
                    {kept.map((one) => {
                        const keptPod = withPeople.find((candidate) => candidate.id === one.podId) ?? one.pod;
                        if (!keptPod) return null;
                        const shown = Boolean(pod) && view.name === "app" && view.podId === one.podId && view.app === one.app;
                        return (
                            <div key={one.podId + "/" + one.app} className="ox-keep" hidden={!shown}>
                                <AppView pod={keptPod} app={one.app} at={shown && view.name === "app" ? view.at ?? "/" : "/"} go={go} actions={actions} shown={shown} onMove={moveApp} />
                            </div>
                        );
                    })}
                </main>
                <CallDock huddle={huddle} onResume={resume} />
            </div>
            {/* The desktop app's background work -- connecting this computer,
                the sandbox download, a missing model. Nothing in a browser. */}
            <DesktopNotices orgId={orgId} />
            <ReconnectStrip />
            <FirstProfileStep />
            {settings && (
                <SettingsModal
                    key={settings.request}
                    orgs={orgs.data ?? []}
                    activeOrgId={orgId}
                    onPickOrg={(id) => { setSettings(null); pickOrg(id); }}
                    onNewOrg={() => setMakingOrg(true)}
                    initial={settings.section}
                    initialFocus={settings.focus}
                    onClose={() => setSettings(null)}
                />
            )}
            {makingLemma && org && (
                <NewLemmaDialog
                    org={org}
                    pods={withPeople}
                    onClose={() => { setMakingLemma(false); if (view.name === "hire") go({ name: "outcomes" }); }}
                    onCreated={(podId, missed) => {
                        setMakingLemma(false);
                        /* It opens on its home, which says how to start it:
                           ask it, bring things in, or add a template. */
                        go(outcomeView(podId));
                        /* Someone could not be added: who has access opens,
                           to try again, rather than a line nobody reads. */
                        if (missed > 0) setAddingPeople(podId);
                    }}
                />
            )}
            {makingOrg && (
                <NewOrgDialog
                    taken={(orgs.data ?? []).map((one) => one.name)}
                    onClose={() => setMakingOrg(false)}
                    onCreated={(made) => {
                        setMakingOrg(false);
                        setSettings(null);
                        pickOrg(made.id);
                        /* A new organization opens in its own Lemma, as the
                           first one does, not on an empty list. */
                        void firstLemma(made.id, made.name).then((podId) => {
                            if (!podId) return;
                            void queryClient.invalidateQueries({ queryKey: ["pods", made.id] });
                            go(outcomeView(podId));
                        });
                    }}
                />
            )}
            {reaching && withPeople.some((one) => one.id === reaching) && <ReachSheet pod={withPeople.find((one) => one.id === reaching)!} onClose={() => setReaching(null)} />}
            {shared && withPeople.some((one) => one.id === shared.podId) && <ShareSheet pod={withPeople.find((one) => one.id === shared.podId)!} orgId={orgId} subject={shared.subject} onClose={() => setShared(null)} />}
            {addingPeople && withPeople.some((one) => one.id === addingPeople) && (
                <Modal title={"People with access to " + withPeople.find((one) => one.id === addingPeople)!.name} onClose={() => setAddingPeople(null)}>
                    <AtTheDoor podId={addingPeople} teammate={withPeople.find((one) => one.id === addingPeople)!.name} />
                    <AddPeople pod={withPeople.find((one) => one.id === addingPeople)!} orgId={orgId} />
                </Modal>
            )}
            {palette.open && org && (
                <Palette
                    pods={withPeople}
                    podId={palette.podId}
                    activity={activity}
                    onClose={() => setPalette({ open: false, podId: null })}
                    go={(next) => { setPalette({ open: false, podId: null }); go(next); }}
                    onAsk={(podId, text) => { setPalette({ open: false, podId: null }); ask(podId, text); }}
                />
            )}
        </div>
    );
}
