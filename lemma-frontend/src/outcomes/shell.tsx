"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type Org, type Pod } from "@/data";
import { lemma } from "@/session/client";
import { key } from "@/session/storage";
import { readAddress } from "@/shell/address";
import { directionOf, heroOf, move } from "./move";
import { NotYours } from "@/shell/not-yours";
import { isForbidden, isMissing } from "@/session/auth-state";
import { useAnalyticsIdentity } from "@/site/analytics/identity";
import { settingsFromQuery } from "@/site/legacy-address";
import { isAboutSection, settingsSectionOf } from "@/space/about-sections";
import { acknowledge, readComposeRequest } from "@/thread/compose-bridge";
import { gather } from "@/workflow/waiting-inbox";
import { useConversationAsks } from "@/thread/use-conversation-asks";
import { byAge, CONVERSATION_ASKS } from "@/thread/waiting-on-you";
import { channelTitle, type Channel } from "@/channels/model";
import { listStamp } from "@/data/stamp";
import { type SettingsSection } from "@/settings/settings-modal";
import { SettingsPage } from "@/settings/settings-page";
import { NewOrgDialog } from "@/org/new-org-dialog";
import { ReachSheet } from "@/shell/reach";
import { ShareSheet, type ShareSubject } from "@/space/share-sheet";
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
import { named, readNamed, type Names } from "./named-address";
import { FrameArrival } from "./arrival";
import { firstLemma } from "./first-lemma";
import { ArrivalFlow, type ArrivalStart, type Landing } from "@/arrival/flow";
import { asksWorkOn, stillAsksWork } from "@/arrival/work";
import { StartLemma } from "./start-lemma";
import { AddPeopleDialog } from "./invite-dialog";
import { FittingApps } from "./add-app";
import { Bars, Icon } from "./parts";
import { Calm } from "./calm";
import { StartPage, StartScreen, startingDone } from "./start-screen";
import { PageBoundary } from "./page-boundary";
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

/** The status a failed read came back with, if it came back at all. */
function statusOf(error: unknown): number | null {
    if (!error || typeof error !== "object") return null;
    const said = (error as { statusCode?: unknown; status?: unknown }).statusCode ?? (error as { status?: unknown }).status;
    return typeof said === "number" ? said : null;
}

/** What is still true when a read failed, with its code where it has one. */
function failedLine(error: unknown): string {
    const status = statusOf(error);
    return status
        ? `The server had a problem answering (${status}). Only reading it failed; the Lemma itself is as it was.`
        : `The server didn’t answer. Only reading it failed; the Lemma itself is as it was.`;
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
        () => (org && pods.isSuccess ? { org: org.name, handle: org.handle, lemmas: list.map((one) => ({ id: one.id, name: one.storedName ?? one.name })) } : null),
        [org, pods.isSuccess, list],
    );
    const path = pathname ?? HOME;
    /* An address spelled in names says nothing until the names are read: the
       reader's own, or -- for a Lemma none of their lists has, one they are
       not in -- the server's word on which Lemma it is. It waits where it is
       rather than being taken for an old link and rewritten as one. */
    const asking = useMemo(() => {
        const question = readNamed(path, { orgs: orgs.data ?? null, orgId, names });
        return question.kind === "ask" ? question : null;
    }, [path, orgs.data, orgId, names]);
    const lookup = useQuery({
        queryKey: ["named-lemma", asking?.lemma, ...(asking?.orgs ?? [])],
        /* Each handle in turn, until one of them has a Lemma by that name. */
        queryFn: async () => {
            for (const org of asking?.orgs ?? []) {
                const podId = await source.findPodByName(org, asking?.lemma ?? "");
                if (podId) return { podId, org };
            }
            return { podId: null, org: null };
        },
        enabled: Boolean(asking),
        staleTime: 5 * 60_000,
    });
    /* A lookup that failed outright opens nothing, as one that found nothing
       does: there is no id to show a door for. */
    const found = useMemo(() => lookup.data ?? (lookup.isError ? { podId: null, org: null, failed: true } : undefined), [lookup.data, lookup.isError]);
    const reading = useMemo(
        () => readNamed(path, { orgs: orgs.data ?? null, orgId, names, found }),
        [path, orgs.data, orgId, names, found],
    );
    const unread = reading.kind === "wait" || reading.kind === "ask" || reading.kind === "elsewhere";
    /* Names nothing the reader may open: said as much, at the address they
       followed, never read as an id it is not. */
    const nowhere = reading.kind === "nowhere";
    const plain = reading.kind === "plain" ? reading.path : path;
    /* The server said which Lemma it is: the reader is not in it, and the
       address keeps the names it came with. */
    const notTheirs = reading.kind === "plain" && reading.asked;
    /* The app's own address first; then the frame's first one (`/t/outcome/…`)
       and the workspace's (`/l/{pod}/conversation/…`), which links written
       before the app moved still carry. */
    const view = useMemo<View>(() => {
        if (unread || nowhere) return last.current;
        return readView(plain) ?? readTrialView(plain) ?? viewOfLink(plain, arrival) ?? last.current;
    }, [plain, arrival, unread, nowhere]);
    useEffect(() => { last.current = view; }, [view]);
    /* Where the frame is, for a move to know where it is going from; and,
       while a page change is moving, the way to say the new page is on
       screen -- once the address it pushed has been drawn. */
    const viewNow = useRef(view);
    viewNow.current = view;
    const landed = useRef<(() => void) | null>(null);
    useLayoutEffect(() => {
        landed.current?.();
        landed.current = null;
    }, [view]);
    useEffect(() => {
        if (unread || nowhere) return;
        const search = window.location.search + window.location.hash;
        if (readView(plain)) {
            /* The app's own address, written back in names where it has them. */
            if (notTheirs) return;
            const spelled = names ? named(plain, names) : path;
            if (spelled !== path) window.history.replaceState(window.history.state, "", spelled + search);
            return;
        }
        /* An old address, written back as the app's own for the same place,
           the query kept for whatever on the page still has to read it. One
           that names nowhere it knows opens Home. */
        const here = readTrialView(plain) ?? viewOfLink(plain, arrival) ?? { name: "today" };
        window.history.replaceState(window.history.state, "", (names ? named(writeView(here), names) : writeView(here)) + search);
    }, [plain, path, arrival, unread, nowhere, notTheirs, names]);
    /* A named address in another of the reader's organizations: the frame
       moves there, as it does for a link that names a Lemma by its id. */
    const otherOrg = reading.kind === "elsewhere" ? reading.orgId : null;
    useEffect(() => {
        if (otherOrg) setChosen(otherOrg);
    }, [otherOrg]);
    /* The way in for somebody new settles once which Lemma their link came
       for (`FrameArrival`), so it opens only after a link in names has been
       read; once open, it stays, whatever the address reads as later. */
    const [linkRead, setLinkRead] = useState(!unread);
    useEffect(() => {
        if (!unread) setLinkRead(true);
    }, [unread]);
    /* `go` is one function for the life of the frame, so it reads the names
       as they are when it is called. */
    const spelling = useRef<Names | null>(null);
    useEffect(() => { spelling.current = names; }, [names]);

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
        if (options.replace) {
            window.history.replaceState({ oxStep: step() }, "", url);
            if (options.handoff) setHandoff(options.handoff);
            if (options.fill) setFill(options.fill);
            return;
        }
        const from = viewNow.current;
        move(directionOf(from, next), heroOf(from, next), () => new Promise<void>((done) => {
            landed.current = done;
            window.history.pushState({ oxStep: step() + 1 }, "", url);
            setHandoff(options.handoff ?? null);
            setFill(options.fill ?? null);
            setNavOpen(false);
            if (!fills(next)) document.querySelector(".ox-main")?.scrollTo({ top: 0 });
            /* An address that draws nothing new is still a change made. */
            window.setTimeout(done, 300);
        }));
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

    /* A Lemma asked for as itself opens on its page: itself, the box to ask
       it, and its apps and everything else it holds, one click away. */

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
       with the ask waiting in a new chat's box. A link in names is read
       first: until then the view is the last one, not the Lemma it names. */
    const arrived = useRef(false);
    useEffect(() => {
        if (arrived.current || unread) return;
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
    }, [arrival, go, view, unread]);

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

    /* Settings is a page (`/settings/<section>`). A link that asks for it
       the old way -- `?settings=`, a connector's round trip coming home with
       `?connect=`, an old `?section=` naming a part of the organization --
       lands on that page, the query kept for the panel that reads it. */
    const [settingsFocus, setSettingsFocus] = useState<string | null>(null);
    useEffect(() => {
        const section = settingsFromQuery(arrival.get("settings")) ?? settingsSectionOf(arrival.get("section"));
        /* Connectors is a place of its own, and a connector's round trip
           comes home to it. */
        if (arrival.get("connect") || section === "connectors") {
            window.history.replaceState(window.history.state, "", writeView({ name: "connectors" }) + window.location.search);
            return;
        }
        if (section) window.history.replaceState(window.history.state, "", writeView({ name: "settings", section }) + window.location.search);
    }, [arrival]);
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
    const workHere = !unread && !nowhere && asksWorkOn(readView(plain)?.name ?? "");
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
    useEffect(() => {
        if (view.name === "people") go({ name: "settings", section: "people" }, { replace: true });
        if (view.name === "settings" && view.section === "connectors") go({ name: "connectors" }, { replace: true });
    }, [view, go]);
    const openSettings = useCallback((section: SettingsSection = "account", focus: string | null = null) => {
        setSettingsFocus(focus);
        go(section === "connectors" ? { name: "connectors" } : { name: "settings", section });
    }, [go]);
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
        /* Only the job question: they were somewhere already, and stay --
           unless it ended on an app it added or a first thing to ask. */
        if (was && was !== "first" && was.kind === "work" && !landing.app && !landing.ask) return;
        /* Already at the Lemma they came for: stay on the exact address,
           section and all, rather than its summary. */
        /* An app the way in just added: open on it, already working, rather
           than on the Lemma with the app one click further in. */
        /* One of the first things the way in offered to ask: a chat with
           the Lemma, asking it. */
        if (landing.podId && landing.ask) {
            ask(landing.podId, landing.ask);
            return;
        }
        if (landing.podId && landing.app) {
            go({ name: "app", podId: landing.podId, app: landing.app });
            return;
        }
        if (landing.podId && landing.podId === scoped) return;
        go(landing.podId ? outcomeView(landing.podId) : { name: landing.orgId ? "today" : "outcomes" });
    }, [arriving, queryClient, go, scoped, ask]);
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

    /* Starting up: until the organization and its Lemmas are first read, the
       frame is the start-up screen the gate and the device cache drew before
       it, so opening the app is one screen filling in. Once the frame has
       been drawn, a later wait -- another organization picked -- loads inside
       it instead. */
    const firstDraw = useRef(true);
    const orgsFailed = orgs.isError && !org;
    const podsFailed = pods.isError && !pods.data;
    const stillReading = !orgsFailed && !podsFailed && (!org || pods.isPending);
    const booting = firstDraw.current && stillReading;
    useEffect(() => {
        /* The way in, when it shows, is a screen of its own: a start-up
           screen after it counts its two seconds afresh. */
        if (booting && !showingArrival) return;
        if (!booting) firstDraw.current = false;
        startingDone();
    }, [booting, showingArrival]);

    /* The way in, one look on every door: somebody who belongs nowhere yet
       (an invitation accepted, their company's organization joined, or one
       made with a Lemma everyone in it shares), an invitation just accepted
       from Home, or the job question somebody has not been asked. */
    if (showingArrival === "first") {
        if (!linkRead) return <StartScreen words={org ? "Opening " + org.name : "Opening Lemma"} />;
        return <FrameArrival linkedPodId={scoped} asksWork={asksWork} asksName={asksName} onEntered={enterFrom} />;
    }
    if (showingArrival) {
        return <ArrivalFlow start={showingArrival} asksWork={asksWork} asksName={asksName} onEntered={enterFrom} />;
    }
    if (booting) {
        return <StartScreen words={org ? "Opening " + org.name : "Opening Lemma"} />;
    }

    let page;
    if (orgsFailed || podsFailed) {
        /* Asks again for whichever read put this here: an org list that
           failed in the background with its data still cached is not one. */
        const retry = () => {
            if (orgsFailed) void orgs.refetch();
            if (podsFailed) void pods.refetch();
        };
        const failure = orgsFailed ? orgs.error : pods.error;
        page = (
            <div className="ox-page">
                <Calm
                    tone="warn"
                    title="Couldn’t read your workspace"
                    actions={(
                        <button type="button" className="b b--m b--primary" disabled={orgs.isFetching || pods.isFetching} onClick={retry}>
                            <Icon name="refresh" size={16} />Try again
                        </button>
                    )}
                >
                    <p>{failedLine(failure)}</p>
                </Calm>
            </div>
        );
    } else if (!org || pods.isPending) {
        page = <StartPage words={org ? "Opening " + org.name : "Opening Lemma"} />;
    } else if (nowhere) {
        page = (
            <div className="ox-page">
                <Calm
                    icon="search"
                    title="This link doesn’t open a Lemma you can reach"
                    actions={<button type="button" className="b b--m b--primary" onClick={() => go({ name: "today" })}>Home</button>}
                >
                    <p>It may have been renamed, or you haven’t been added to it. Ask whoever sent it to invite you.</p>
                </Calm>
            </div>
        );
    } else if (view.name === "today") {
        page = <Today org={org} pods={withPeople} activity={activity} channels={sideChannels} queue={queue} queueFailed={queueFailed} onRetryQueue={retryQueue} me={myName} go={go} onAsk={ask} onJoined={(invite) => setArriving({ kind: "joined", orgId: invite.orgId, orgName: invite.orgName, podId: invite.podId })} />;
    } else if (view.name === "apps") {
        page = <AppsPage pods={withPeople} go={go} />;
    } else if (view.name === "connectors") {
        page = <ConnectorsPage org={org} />;
    } else if (view.name === "settings") {
        page = (
            <SettingsPage
                orgs={orgs.data ?? []}
                activeOrgId={orgId}
                section={(view.section ?? "account") as SettingsSection}
                focus={settingsFocus}
                onSection={(section) => { setSettingsFocus(null); go({ name: "settings", section }, { replace: true }); }}
                onPickOrg={pickOrg}
                onNewOrg={() => setMakingOrg(true)}
                people={<People org={org} pods={withPeople} go={go} actions={actions} embedded />}
            />
        );
    } else if (view.name === "outcomes" || view.name === "hire") {
        page = <OutcomesList org={org} pods={withPeople} activity={activity} days={days} queue={queue} go={go} actions={actions} />;
    } else if (view.name === "needs") {
        page = <NeedsYou pods={withPeople} queue={queue} loading={queueLoading} failed={queueFailed} onRetry={retryQueue} retrying={!sampled && needs.isFetching} go={go} />;
    } else if (view.name === "people") {
        /* People is a section of Settings now; its old address lands there. */
        page = null;
    } else if (view.name === "history") {
        page = <History org={org} pods={withPeople} podId={view.podId} go={go} />;
    } else if (stranger) {
        /* Three answers for a Lemma the list does not have: not yours (403),
           not there (404), and the read failing (anything else). */
        page = (
            <div className="ox-page">
                <NotYours podId={stranger} onArrived={() => { void pods.refetch(); void linked.refetch(); }} onHome={() => go({ name: "today" })} />
            </div>
        );
    } else if (unlisted && linked.isError && isMissing(linked.error)) {
        page = (
            <div className="ox-page">
                <Calm
                    icon="search"
                    title="This Lemma isn’t here"
                    actions={<button type="button" className="b b--m b--primary" onClick={() => go({ name: "today" })}>Home</button>}
                >
                    <p>It was deleted, or the link came from somewhere else.</p>
                </Calm>
            </div>
        );
    } else if (unlisted && linked.isError) {
        page = (
            <div className="ox-page">
                <Calm
                    tone="warn"
                    title="Couldn’t open this Lemma"
                    actions={(
                        <button type="button" className="b b--m b--primary" disabled={linked.isFetching} onClick={() => void linked.refetch()}>
                            <Icon name="refresh" size={16} />Try again
                        </button>
                    )}
                >
                    <p>{failedLine(linked.error)}</p>
                </Calm>
            </div>
        );
    } else if (unlisted && (linked.isPending || elsewhere)) {
        page = <StartPage words="Opening this Lemma" />;
    } else if (view.name === "chats") {
        page = <ChatPane pods={withPeople} open={view.open} activity={activity} go={go} actions={actions} calls={calls} handoff={handoff} onHandoffSent={sentHandoff} fill={fill} onFilled={filled} />;
    } else if (!pod) {
        /* Read, but in no list of the reader's: asked again, lists and all. */
        page = (
            <div className="ox-page">
                <Calm
                    tone="warn"
                    title="Couldn’t open this Lemma"
                    actions={(
                        <button type="button" className="b b--m b--primary" disabled={pods.isFetching || linked.isFetching} onClick={() => { void pods.refetch(); void linked.refetch(); }}>
                            <Icon name="refresh" size={16} />Try again
                        </button>
                    )}
                >
                    <p>It isn’t in {org.name}’s list of Lemmas yet. If it was only just made, trying again usually finds it.</p>
                </Calm>
            </div>
        );
    } else if (view.name === "outcome" && view.tab === "settings") {
        page = <SettingsView key={pod.id} pod={pod} org={org} section={view.section ?? null} go={go} actions={actions} />;
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
                <main className={"ox-main" + (fills(view) && !nowhere ? " ox-main--fill" : "") + (calling ? " ox-main--calling" : "")}>
                    {/* A page that throws while drawing takes down itself and
                        nothing else; the next address draws again. Each kept
                        app has its own, so one breaking leaves the rest. */}
                    <PageBoundary at={path}>
                        {calling && <CallStage huddle={huddle} go={go} />}
                        {page}
                    </PageBoundary>
                    {kept.map((one) => {
                        const keptPod = withPeople.find((candidate) => candidate.id === one.podId) ?? one.pod;
                        if (!keptPod) return null;
                        /* A link that opens nothing keeps the last view but
                           shows only what it says. */
                        const shown = !nowhere && Boolean(pod) && view.name === "app" && view.podId === one.podId && view.app === one.app;
                        return (
                            <div key={one.podId + "/" + one.app} className="ox-keep" hidden={!shown}>
                                <PageBoundary at={shown ? path : "away"}>
                                    <AppView pod={keptPod} app={one.app} at={shown && view.name === "app" ? view.at ?? "/" : "/"} go={go} actions={actions} shown={shown} onMove={moveApp} />
                                </PageBoundary>
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
            {makingLemma && org && (
                <StartLemma
                    org={org}
                    pods={withPeople}
                    me={myName}
                    onClose={() => { setMakingLemma(false); if (view.name === "hire") go({ name: "outcomes" }); }}
                    onMade={(podId, missed) => {
                        setMakingLemma(false);
                        /* It opens on its page: its face, the box to ask it,
                           and the way to add apps. */
                        go(outcomeView(podId));
                        /* Someone could not be added: who has access opens,
                           to try again, rather than a line nobody reads. */
                        if (missed > 0) setAddingPeople(podId);
                    }}
                    apps={(made) => <FittingApps podId={made.id} purpose={made.purpose} name={made.name} />}
                />
            )}
            {makingOrg && (
                <NewOrgDialog
                    taken={(orgs.data ?? []).map((one) => one.name)}
                    onClose={() => setMakingOrg(false)}
                    onCreated={(made, personal) => {
                        setMakingOrg(false);
                        pickOrg(made.id);
                        /* A new organization opens in its own Lemma, as the
                           first one does, not on an empty list. */
                        void firstLemma(made.id, made.name, { personal }).then((podId) => {
                            if (!podId) return;
                            void queryClient.invalidateQueries({ queryKey: ["pods", made.id] });
                            go(outcomeView(podId));
                        });
                    }}
                />
            )}
            {reaching && withPeople.some((one) => one.id === reaching) && <ReachSheet pod={withPeople.find((one) => one.id === reaching)!} onClose={() => setReaching(null)} />}
            {shared && withPeople.some((one) => one.id === shared.podId) && <ShareSheet pod={withPeople.find((one) => one.id === shared.podId)!} orgId={orgId} subject={shared.subject} onClose={() => setShared(null)} onAddPeople={() => { const podId = shared.podId; setShared(null); setAddingPeople(podId); }} />}
            {/* Adding people to a Lemma: who, and what they can do in it. Who
                is in it already, and who asked to join, are on its People. */}
            {addingPeople && org && withPeople.some((one) => one.id === addingPeople) && (
                <AddPeopleDialog pod={withPeople.find((one) => one.id === addingPeople)!} org={org} onClose={() => setAddingPeople(null)} />
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
