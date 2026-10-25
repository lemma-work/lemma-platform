"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type Org } from "@/data";
import { lemma } from "@/session/client";
import { key } from "@/session/storage";
import { readAddress } from "@/shell/address";
import { acknowledge, readComposeRequest } from "@/thread/compose-bridge";
import { gather } from "@/workflow/waiting-inbox";
import { useConversationAsks } from "@/thread/use-conversation-asks";
import { byAge } from "@/thread/waiting-on-you";
import { channelTitle } from "@/channels/model";
import { SettingsModal, type SettingsSection } from "@/settings/settings-modal";
import { NewOrgDialog } from "@/org/new-org-dialog";
import { ArrivalView } from "@/org/arrival-view";
import { HiringView } from "@/stage/hiring";
import { ReachSheet } from "@/shell/reach";
import { ShareSheet } from "@/space/share-sheet";
import { AddPeople } from "@/shell/add-people";
import { Modal } from "@/shell/modal";
import { ReconnectStrip } from "@/shell/reconnect-strip";
import { FirstProfileStep } from "@/session/first-profile-step";
import { useOpenSettingsEvent } from "@/desktop/open-settings";
import {
    NEW_CHAT,
    chatOpenOfView, chatView, outcomeView, podOfView, queueItems, readView, viewOfAddress, writeView,
    type ContactWaiting, type Fill, type Go, type Handoff, type View,
} from "./model";
import { usePlans, usePodActivity, usePodDetails } from "./data";
import { Bars, Icon, Empty } from "./parts";
import { Sidebar } from "./sidebar";
import { Today } from "./today";
import { OutcomesList } from "./list";
import { NeedsYou } from "./needs";
import { People } from "./people";
import { OutcomePage } from "./outcome";
import { ChatPane, useContactsWaiting } from "./chats";
import { AgentView, AppView, FilePage, RecordPage, RunView, SettingsView, TablePage, WorkflowView } from "./stage";
import type { FrameActions } from "./actions";
import { Palette } from "./palette";

/** The organization the workspace last opened, shared with `/t` so the two
 *  frames agree on where you are. */
const ORG_KEY = key("org");

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
        || view.name === "table" || view.name === "record" || view.name === "agent" || view.name === "hire"
        || (view.name === "outcome" && view.tab === "settings");
}

/** The outcome-first frame: a sidebar of places and of every outcome with
 *  its chats, and whatever is open beside it. The sidebar names where people
 *  go and talk, not the kinds of things inside a pod; those are one level
 *  down, in an outcome's X-ray. Everything opens in here. */
export function OutcomesShell() {
    const pathname = usePathname();
    /* Where the frame was last, for a workspace address to fall back on: an
       embedded thread or chat page writes its links for `/t`, and following
       one should stay here. */
    const last = useRef<View>({ name: "today" });
    const view = useMemo(() => {
        const path = pathname ?? "/o";
        if (path === "/t" || path.startsWith("/t/")) return viewOfAddress(readAddress(path)) ?? last.current;
        return readView(path);
    }, [pathname]);
    useEffect(() => { last.current = view; }, [view]);
    useEffect(() => {
        const path = pathname ?? "/o";
        if (path === "/o" || path.startsWith("/o/")) return;
        const here = path === "/t" || path.startsWith("/t/") ? viewOfAddress(readAddress(path)) : null;
        if (here) window.history.replaceState(null, "", writeView(here));
        else window.location.assign(path + window.location.search);
    }, [pathname]);

    /* Words on their way to a chat: what Home or ⌘K asked, or what an app put
       in the box. Both belong to the navigation that carried them, and any
       other step drops them, so they never land in a chat they were not for. */
    const [handoff, setHandoff] = useState<Handoff | null>(null);
    const [fill, setFill] = useState<Fill | null>(null);
    const [navOpen, setNavOpen] = useState(false);
    const go = useCallback<Go>((next, options = {}) => {
        const url = writeView(next);
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
    const filled = useCallback(() => setFill(null), []);
    const queryClient = useQueryClient();

    const orgs = useQuery({ queryKey: ["orgs"], queryFn: () => source.listOrgs(), staleTime: 10 * 60_000 });
    const [chosen, setChosen] = useState<string | null>(() => storedOrg());
    const orgId = orgs.data?.some((one) => one.id === chosen) ? chosen : orgs.data?.[0]?.id ?? null;
    const org: Org | null = orgs.data?.find((one) => one.id === orgId) ?? null;
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

    const pods = useQuery({
        queryKey: ["pods", orgId],
        queryFn: () => source.listPods(orgId as string),
        enabled: Boolean(orgId),
        staleTime: 5 * 60_000,
    });
    const list = useMemo(() => pods.data ?? [], [pods.data]);

    const waits = useQuery({
        queryKey: ["workflow-waiting", list.map((one) => one.id).join(",")],
        queryFn: () => gather(list, source.label === "sample", queryClient),
        enabled: list.length > 0,
        staleTime: 60_000,
    });
    const asked = useConversationAsks(list);
    /* People outside a Lemma who wrote in and wait on a person: answered
       from Needs you like any other question, not listed in the sidebar. */
    const [contactsWaiting, contactFeeds] = useContactsWaiting(list);
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
    const queue = useMemo(() => queueItems(byAge(asked.data ?? []).fresh, waits.data?.rows ?? [], contacts), [asked.data, waits.data, contacts]);
    const { byPod: activity } = usePodActivity(list);
    const plans = usePlans(list);

    /* Asking -- from Home, ⌘K or an outcome -- starts a chat in the outcome
       named and opens it. The pane that shows it sends the words, so the
       reply streams into the thread on screen; without a live session the
       words wait in the box instead. */
    const asks = useRef(0);
    const ask = useCallback((podId: string, text: string, replace = false, createWith?: Record<string, unknown>) => {
        asks.current += 1;
        const target = chatView({ kind: "chat", podId, id: NEW_CHAT });
        if (source.label === "live") go(target, { replace, handoff: { text, id: asks.current, podId, sent: false, createWith } });
        else go(target, { replace, fill: { text, id: asks.current, podId } });
    }, [go]);
    /* From a new chat's own box: the chat is already open, so the words
       start it in place rather than as another step back to go through. */
    const askHere = useCallback((podId: string, text: string) => ask(podId, text, true), [ask]);
    /* Words for a chat's box, without sending: what a table's "ask" and an
       app's button put there. */
    const prompt = useCallback((podId: string, text: string, fresh = true) => {
        asks.current += 1;
        const words: Fill = { text, id: asks.current, podId };
        const open = chatOpenOfView(view);
        if (!fresh && open.kind === "chat" && open.podId === podId) setFill(words);
        else go(chatView({ kind: "chat", podId, id: NEW_CHAT }), { fill: words });
    }, [go, view]);

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
    const [settings, setSettings] = useState<{ section: SettingsSection; focus: string | null; request: number } | null>(null);
    const [makingOrg, setMakingOrg] = useState(false);
    const [reaching, setReaching] = useState<string | null>(null);
    const [sharing, setSharing] = useState<string | null>(null);
    const [addingPeople, setAddingPeople] = useState<string | null>(null);
    const openSettings = useCallback((section: SettingsSection = "account", focus: string | null = null) => {
        setSettings((was) => ({ section, focus, request: (was?.request ?? 0) + 1 }));
    }, []);
    useOpenSettingsEvent(openSettings);
    const actions = useMemo<FrameActions>(() => ({
        openSettings: (section) => openSettings(section),
        connect: setReaching,
        share: setSharing,
        addPeople: setAddingPeople,
        prompt: (podId, text) => prompt(podId, text),
        ask: (podId, text, createWith) => ask(podId, text, false, createWith),
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

    const myName = nameOf(me.data);
    const scoped = podOfView(view);
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

    /* Somebody who belongs nowhere yet: the workspace's own way in, with
       their invitations, the organizations they could join, or a new one. */
    if (orgs.isSuccess && orgs.data.length === 0) {
        return (
            <ArrivalView
                email={me.data?.email ?? null}
                name={[me.data?.first_name, me.data?.last_name].filter(Boolean).join(" ") || null}
                onEntered={(podId) => { void queryClient.invalidateQueries({ queryKey: ["orgs"] }); if (podId) go(outcomeView(podId)); }}
            />
        );
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
        page = <Today org={org} pods={withPeople} activity={activity} queue={queue} me={myName} go={go} onAsk={ask} />;
    } else if (view.name === "outcomes") {
        page = <OutcomesList org={org} pods={withPeople} activity={activity} plans={plans} queue={queue} go={go} />;
    } else if (view.name === "needs") {
        page = <NeedsYou pods={withPeople} queue={queue} loading={asked.isPending || waits.isPending} go={go} />;
    } else if (view.name === "people") {
        page = <People pods={withPeople} go={go} />;
    } else if (view.name === "hire") {
        page = (
            <div className="settings-view ox-hire">
                <HiringView
                    orgId={org.id}
                    orgName={org.name}
                    onClose={() => go({ name: "outcomes" })}
                    onHired={(id) => {
                        void queryClient.invalidateQueries({ queryKey: ["pods", org.id] });
                        go(outcomeView(id));
                    }}
                />
            </div>
        );
    } else if (view.name === "chats") {
        page = <ChatPane pods={withPeople} open={view.open} activity={activity} go={go} onAsk={askHere} actions={actions} handoff={handoff} onHandoffSent={sentHandoff} fill={fill} onFilled={filled} />;
    } else if (!pod) {
        page = <div className="ox-page"><Empty title="This Lemma isn’t one you can open."><p>It may have been removed, or you were never added to it.</p></Empty></div>;
    } else if (view.name === "outcome" && view.tab === "settings") {
        page = <SettingsView key={pod.id} pod={pod} org={org} section={view.section ?? null} go={go} actions={actions} />;
    } else if (view.name === "outcome") {
        page = <OutcomePage pod={pod} tab={view.tab} plan={plans.get(pod.id)} activity={activity.get(pod.id) ?? []} queue={queue.filter((item) => item.podId === pod.id)} me={myName} go={go} onAsk={ask} actions={actions} />;
    } else if (view.name === "run") {
        page = <RunView key={view.runId} pod={pod} runId={view.runId} go={go} back={back} />;
    } else if (view.name === "workflow") {
        page = <WorkflowView key={view.workflow} pod={pod} workflow={view.workflow} go={go} />;
    } else if (view.name === "app") {
        page = <AppView key={view.app} pod={pod} app={view.app} go={go} />;
    } else if (view.name === "file") {
        page = <FilePage key={view.path} pod={pod} path={view.path} go={go} actions={actions} />;
    } else if (view.name === "table") {
        page = <TablePage key={view.table} pod={pod} table={view.table} go={go} actions={actions} />;
    } else if (view.name === "record") {
        page = <RecordPage key={view.table + view.recordId} pod={pod} table={view.table} recordId={view.recordId} go={go} />;
    } else if (view.name === "agent") {
        page = <AgentView key={view.agent} pod={pod} agent={view.agent} go={go} actions={actions} back={back} />;
    }

    return (
        <div className="ox" data-nav={navOpen || undefined}>
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
                me={myName}
                onFind={() => openPalette(podOfView(view))}
                onSettings={() => openSettings("account")}
                onNewOrg={() => setMakingOrg(true)}
                open={navOpen}
            />
            {navOpen && <button type="button" className="ox-navscrim" aria-label="Close the menu" onClick={() => setNavOpen(false)} />}
            <div className="ox-body">
                <header className="ox-topbar">
                    <button type="button" className="ox-topbar__menu" aria-label="Menu" aria-expanded={navOpen} onClick={() => setNavOpen((was) => !was)}><Icon name="menu" size={18} /></button>
                    <Bars size={18} />
                    <span className="ox-topbar__org">{org?.name ?? "Lemma"}</span>
                    <button type="button" className="ox-topbar__find" aria-label="Ask or find" onClick={() => openPalette(podOfView(view))}><Icon name="search" size={16} /></button>
                </header>
                <main className={"ox-main" + (fills(view) ? " ox-main--fill" : "")}>{page}</main>
            </div>
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
            {makingOrg && (
                <NewOrgDialog
                    taken={(orgs.data ?? []).map((one) => one.name)}
                    onClose={() => setMakingOrg(false)}
                    onCreated={(made) => { setMakingOrg(false); setSettings(null); pickOrg(made.id); }}
                />
            )}
            {reaching && withPeople.some((one) => one.id === reaching) && <ReachSheet pod={withPeople.find((one) => one.id === reaching)!} onClose={() => setReaching(null)} />}
            {sharing && withPeople.some((one) => one.id === sharing) && <ShareSheet pod={withPeople.find((one) => one.id === sharing)!} orgId={orgId} subject={{ kind: "space" }} onClose={() => setSharing(null)} />}
            {addingPeople && withPeople.some((one) => one.id === addingPeople) && (
                <Modal title={"People with access to " + withPeople.find((one) => one.id === addingPeople)!.name} onClose={() => setAddingPeople(null)}>
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
