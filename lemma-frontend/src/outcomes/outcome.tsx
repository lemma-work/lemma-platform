"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { source, type ConversationRef, type Pod } from "@/data";
import { ContactsPage } from "@/space/contacts-page";
import { ChatsPage } from "@/space/chats-page";
import { AppCover } from "@/stage/app-cover";
import { readableName } from "@/library/reading";
import { useMaySeeContacts } from "@/space/contact-queries";
import { allChannels, useChannelList } from "@/channels/queries";
import { sinceShort } from "@/channels/model";
import { useMe } from "@/session/use-me";
import { key } from "@/session/storage";
import { TeammateFace } from "@/space/teammate-face";
import {
    ageOf, chatItems, chatView, holdsNothing, inSidebar, isChat, isNewcomer, isRunning, lemmaSays, outcomeView, partOfAbout, sectionRows, welcomeSaid, workRows,
    type Ask, type Go, type OutcomeTab, type QueueItem,
} from "./model";
import { mcpUrl, useLemmaOpenness } from "./data";
import { Empty, Icon, to } from "./parts";
import type { FrameActions } from "./actions";
import { NeedRow } from "./answer";
import { AppTiles, AskBox, useApps } from "./ask";
import { StartWith } from "./chats";
import { AboutTab } from "./about";
import { AutomationsTab } from "./automations";
import { sectionLabel } from "./lemma-bar";
import { LemmaHead, useLemmaTables } from "./lemma-head";
import { LemmaStart } from "./lemma-start";
import { FilesTab, PagesTab, PeopleTab, TablesTab } from "./lemma-tabs";
import { kindOf, markOf, readsAhead } from "./sidebar";
import { Stage } from "./stage";

async function copy(text: string): Promise<boolean> {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch {
        return false;
    }
}

/** One Lemma. Every page of it wears one header: its character, its name and
 *  what it says about itself right now, the people in it, and its tabs --
 *  Home, what is in it, and About. At its own address, its home: an empty
 *  Lemma's ways in, or, once something is in it, its apps, what needs you,
 *  what it has been doing, and the box to ask it. About says what it is;
 *  its people, what it runs and who writes in are parts of About,
 *  each a page that names itself and leads back. (Settings is drawn by the
 *  shell, full height, under the bar the things it holds wear.) */
export function OutcomePage({ pod, pods, orgName, tab, section = null, activity, queue, me, go, onAsk, actions }: {
    pod: Pod;
    /** Every Lemma in the organization, for the queues that read across them. */
    pods: readonly Pod[];
    orgName: string;
    tab: OutcomeTab;
    /** One view of the tab, where the tab has views (Automations). */
    section?: string | null;
    activity: readonly ConversationRef[];
    queue: readonly QueueItem[];
    me: string;
    go: Go;
    /** Start a chat with this Lemma with these words, and open it. */
    onAsk: Ask;
    actions: FrameActions;
}) {
    const contacts = useMaySeeContacts(pod.id);
    const empty = useLemmaEmpty(pod);
    /* What it is for, until what it holds has been read: a guess at how it
       is doing would be said in its voice and then taken back. */
    const says = empty === null
        ? pod.description ?? ""
        : lemmaSays({ empty, running: activity.filter(isRunning).length, needs: queue.length });
    const head = <LemmaHead pod={pod} says={says} tab={tab} go={go} actions={actions} />;
    if (tab === "home") {
        return (
            <Stage pod={pod} go={go} head={head}>
                <LemmaHome pod={pod} activity={activity} queue={queue} me={me} go={go} onAsk={onAsk} actions={actions} />
            </Stage>
        );
    }
    /* A tab the row shows names itself there; a part of About says what it
       is, and where it came from. */
    const titled = tab !== "settings" && partOfAbout(tab);
    return (
        <Stage pod={pod} go={go} head={head}>
            <div className="ox-page ox-section" data-section={tab}>
                {titled && (
                    <header className="ox-section__head ox-section__head--part">
                        <a className="ox-link" {...to(outcomeView(pod.id, "about"), go)}>About {pod.name}</a>
                        <h1 className="ox-section__t">{sectionLabel(tab)}</h1>
                    </header>
                )}
                {tab === "about" && <AboutTab pod={pod} orgName={orgName} go={go} actions={actions} />}
                {tab === "chats" && (
                    <ChatsPage
                        pod={pod}
                        openId={null}
                        initialFilter={section === "channels" ? "channels" : "all"}
                        onOpen={(id) => go(chatView({ kind: "chat", podId: pod.id, id }))}
                        onOpenChannel={(id) => go(chatView({ kind: "shared", podId: pod.id, id }))}
                        onOpenRun={(runId) => go({ name: "run", podId: pod.id, runId })}
                        onNew={() => go(outcomeView(pod.id))}
                        onConnect={() => actions.connect(pod.id)}
                        onReach={() => actions.connect(pod.id)}
                        onPages={() => go(outcomeView(pod.id, "pages"))}
                        onAsk={(text) => actions.prompt(pod.id, text)}
                    />
                )}
                {tab === "pages" && <PagesTab pod={pod} go={go} actions={actions} />}
                {tab === "tables" && <TablesTab pod={pod} go={go} actions={actions} />}
                {tab === "files" && <FilesTab pod={pod} go={go} />}
                {tab === "apps" && <AppsTab pod={pod} go={go} actions={actions} />}
                {tab === "people" && <PeopleTab pod={pod} orgName={orgName} />}
                {tab === "contacts" && (contacts
                    ? <ContactsPage pod={pod} onConnect={() => actions.connect(pod.id)} />
                    : <Empty title="Contacts are for whoever looks after them."><p>Ask an admin of {pod.name} if you should be one.</p></Empty>)}
                {tab === "automations" && <AutomationsTab pod={pod} pods={pods} section={section} queue={queue} go={go} actions={actions} />}
            </div>
        </Stage>
    );
}

/* ── home ─────────────────────────────────────────────────────────────── */

/** The Lemmas that have said hello to the reader, on this browser. */
const MET_KEY = key("outcomes-met");

function metAlready(): string[] {
    try {
        const kept: unknown = JSON.parse(localStorage.getItem(MET_KEY) ?? "[]");
        return Array.isArray(kept) ? kept.filter((one): one is string => typeof one === "string") : [];
    } catch {
        return [];
    }
}

function hasMet(podId: string): boolean {
    return metAlready().includes(podId);
}

function rememberMet(podId: string) {
    try {
        localStorage.setItem(MET_KEY, JSON.stringify([...new Set([...metAlready(), podId])]));
    } catch {
        /* Not kept: it says hello again on the next visit, which is harmless. */
    }
}

/** Whether a Lemma holds nothing yet -- no chat, table, app or file a person
 *  would call theirs -- or null while that is still being read. A read that
 *  fails says "not empty": an empty page shown over a Lemma full of work
 *  would be the worse mistake. */
export function useLemmaEmpty(pod: Pod): boolean | null {
    const chats = useQuery({
        queryKey: ["conversations", pod.id, "outcome-activity"],
        queryFn: async () => (await source.listConversationsPage(pod.id, null, undefined, true)).items,
        staleTime: 30_000,
    });
    const tables = useLemmaTables(pod.id);
    const files = useQuery({ queryKey: ["outcome-files", pod.id], queryFn: () => source.listLibrary(pod.id, "files", "/"), staleTime: 5 * 60_000, retry: false });
    const solo = useMemo(() => [pod], [pod]);
    const apps = useApps(solo);
    if (chats.isError || tables.isError || files.isError) return false;
    if ((chats.data?.length ?? 0) > 0 || (tables.data?.length ?? 0) > 0 || apps.apps.length > 0) return false;
    if (chats.isPending || tables.isPending || files.isPending || apps.pending) return null;
    return holdsNothing((files.data?.items ?? []).map((item) => item.name));
}

/** A Lemma's home: an empty one opens on its ways in -- the organization's
 *  own on bringing the team's stuff in -- and any other on its summary. */
function LemmaHome({ pod, activity, queue, me, go, onAsk, actions }: {
    pod: Pod;
    activity: readonly ConversationRef[];
    queue: readonly QueueItem[];
    me: string;
    go: Go;
    onAsk: Ask;
    actions: FrameActions;
}) {
    const empty = useLemmaEmpty(pod);
    const { openness, settled } = useLemmaOpenness(pod.id);
    if (empty === null || (empty && !settled)) return <p className="ox-loading">Opening {pod.name}…</p>;
    if (empty) return <LemmaStart pod={pod} welcome={Boolean(openness?.everyone)} go={go} onAsk={onAsk} actions={actions} />;
    return <LemmaSummary pod={pod} activity={activity} queue={queue} me={me} go={go} onAsk={onAsk} actions={actions} />;
}

/** How many recent things the summary lists. */
const RECENT_SHOWN = 6;
/** Apps on a Lemma's page before "All": one row of large tiles. */
const APPS_SHOWN = 3;

/** A Lemma's home once something is in it, one column read top to bottom:
 *  its apps, large, since they are
 *  what people use it through; what it needs from the reader; what it has
 *  been doing -- and at the foot, pinned like a chat's composer, the box to
 *  ask it something and the ways to start a chat with people instead. Who it
 *  is and how it is doing are the header's, so nothing here says them again. */
function LemmaSummary({ pod, activity, queue, me, go, onAsk, actions }: {
    pod: Pod;
    activity: readonly ConversationRef[];
    queue: readonly QueueItem[];
    /** The reader's name, for how the Lemma greets someone new to it. */
    me: string;
    go: Go;
    onAsk: Ask;
    actions: FrameActions;
}) {
    const reader = useMe();
    const [now] = useState(() => Date.now());
    const channelList = useChannelList(pod.id, { archived: false });
    const tables = useLemmaTables(pod.id);
    const tableItems = useMemo(() => tables.data ?? [], [tables.data]);
    const solo = useMemo(() => [pod], [pod]);
    const { apps } = useApps(solo);
    const rows = useMemo(() => {
        const { needs, rest } = chatItems({ pods: [pod], conversations: new Map([[pod.id, activity]]), channels: new Map([[pod.id, allChannels(channelList.data)]]), me: reader });
        const listed = [...needs, ...rest].filter(inSidebar);
        const work = workRows({
            podId: pod.id,
            conversations: activity,
            tables: tableItems.map((item) => ({ name: item.name, title: readableName(item.name), updated: item.updated })),
            now,
        });
        return sectionRows(listed, work).filter((row) => !row.needsYou);
    }, [pod, activity, channelList.data, tableItems, reader, now]);

    /* Somebody new to a Lemma they did not make is met by it, once: who it
       is, what it is for, and where their part is. Kept on this browser. */
    const [met, setMet] = useState(() => hasMet(pod.id));
    const newcomer = !met && isNewcomer({ madeBy: pod.hiredBy ?? null, me: reader, chats: activity.filter(isChat).length });
    const meet = () => { rememberMet(pod.id); setMet(true); };

    return (
        <div className="ox-page ox-lsum">
            {newcomer && (
                <section className="ox-meet" aria-label={`${pod.name} says hello`}>
                    <TeammateFace pod={pod} size={40} />
                    <div className="ox-meet__t">
                        <p>{welcomeSaid(pod, me, pod.members.filter((member) => member.kind === "person").length)}</p>
                        <p className="ox-meet__acts">
                            <button type="button" className="ox-btn ox-btn--small ox-btn--primary" onClick={meet}>Got it</button>
                            <a className="ox-link" {...to(outcomeView(pod.id, "about"), go)}>More about {pod.name}</a>
                        </p>
                    </div>
                </section>
            )}
            {apps.length > 0 && (
                <section aria-label="Apps">
                    <div className="ox-sec">
                        <h2>Apps</h2>
                        {apps.length > APPS_SHOWN && <a className="ox-link" {...to(outcomeView(pod.id, "apps"), go)}>All {apps.length}</a>}
                    </div>
                    <AppTiles apps={apps.slice(0, APPS_SHOWN)} go={go} showWhose={false} large />
                </section>
            )}
            {queue.length > 0 && (
                <section aria-label="Needs you">
                    <div className="ox-sec"><h2>Needs you</h2></div>
                    <div className="ox-queue">
                        {queue.map((item) => <NeedRow key={item.key} item={item} pods={[pod]} now={now} go={go} showLemma={false} />)}
                    </div>
                </section>
            )}
            <section aria-label="Recently">
                <div className="ox-sec">
                    <h2>Recently</h2>
                    <a className="ox-link" {...to({ name: "history", podId: pod.id }, go)}>Everything it has been doing</a>
                </div>
                {rows.length === 0 ? <p className="ox-vs">Nothing yet. Ask {pod.name} something, and it shows here.</p> : (
                    <div className="ox-recent">
                        {rows.slice(0, RECENT_SHOWN).map((row) => (
                            <a key={row.key} className="ox-recent__row" {...to(row.view, go)} {...readsAhead(row.view)}>
                                <span className="ox-glyph">{markOf(row)}</span>
                                <span className="ox-recent__t">{row.title}</span>
                                <span className="ox-recent__k">{kindOf(row)}</span>
                                {row.running
                                    ? <i className="ox-sign ox-sign--live" role="img" aria-label="Running now" />
                                    : <span className="ox-recent__when">{row.at !== null ? sinceShort(new Date(row.at).toISOString()) : ""}</span>}
                            </a>
                        ))}
                    </div>
                )}
            </section>
            {/* A chat with it starts here: this page is the new chat, so its
                box sits where a chat keeps one -- at the foot, in reach while
                everything above it scrolls. */}
            <div className="ox-lsum__ask">
                <StartWith pod={pod} go={go} onConnect={() => actions.connect(pod.id)} />
                <AskBox pods={[pod]} podId={pod.id} onAsk={onAsk} autoFocus placeholder={`Ask ${pod.name} anything, or say what to do`} />
            </div>
        </div>
    );
}

/* ── apps ─────────────────────────────────────────────────────────────── */

function AppsTab({ pod, go, actions }: { pod: Pod; go: Go; actions: FrameActions }) {
    const tabs = useQuery({ queryKey: ["outcome-apps", pod.id], queryFn: () => source.listTabs(pod.id), staleTime: 5 * 60_000 });
    const apps = (tabs.data ?? []).flatMap((tab) => (tab.kind === "app" ? [tab] : []));
    const [copied, setCopied] = useState<string | null>(null);
    const url = mcpUrl(pod.id);
    return (
        <div>
            <section className="ox-reach">
                <div>
                    <p className="ox-reach__t">Use {pod.name} from Claude or ChatGPT</p>
                    <p className="ox-vs">One address gives an assistant {pod.name}’s tables, files and apps, with the access of whoever connects it.</p>
                </div>
                {url && <button type="button" className="ox-btn" onClick={async () => setCopied((await copy(url)) ? "mcp" : null)}>{copied === "mcp" ? "Address copied" : "Copy the address"}</button>}
            </section>
            {tabs.isPending ? <p className="ox-loading">Loading apps…</p> : tabs.isError ? (
                <Empty title="Couldn’t read its apps.">
                    <button type="button" className="ox-btn" onClick={() => void tabs.refetch()}>Try again</button>
                </Empty>
            ) : apps.length === 0 ? (
                <Empty title="No apps yet.">
                    <p>Apps are how people work with {pod.name}, and how people outside it use what it makes.</p>
                    <button type="button" className="ox-btn ox-btn--lemma" onClick={() => actions.prompt(pod.id, "Build an app that ")}>Ask {pod.name} to build one</button>
                </Empty>
            ) : (
                <div className="ox-apps">
                    {apps.map((app) => {
                        const inFrame = to({ name: "app", podId: pod.id, app: app.id.replace(/^app:/, "") }, go);
                        return (
                            <article key={app.id} className="ox-app" aria-label={app.label}>
                                <a className="ox-app__cover" {...inFrame} aria-label={"Open " + app.label}>
                                    <AppCover url={app.url} fallback={<span className="ox-app__blank"><Icon name="app" size={22} /></span>} />
                                </a>
                                <p className="ox-app__name">{app.label}</p>
                                <p className="ox-app__meta">{[app.visibility && app.visibility.toLowerCase(), app.updated && `updated ${ageOf(Date.parse(app.updated), Date.now())} ago`].filter(Boolean).join(" · ")}</p>
                                <div className="ox-app__acts">
                                    <a className="ox-btn ox-btn--primary" {...inFrame} aria-label={"Open " + app.label}>Open</a>
                                    <button type="button" className="ox-btn" aria-label={copied === app.id ? undefined : "Copy link to " + app.label} onClick={async () => setCopied((await copy(app.url)) ? app.id : null)}>{copied === app.id ? "Link copied" : "Copy link"}</button>
                                    <a className="ox-btn ox-btn--icon" href={app.url} target="_blank" rel="noreferrer" aria-label={`Open ${app.label} in a new tab`} title="Open in a new tab"><Icon name="out" size={14} /></a>
                                </div>
                            </article>
                        );
                    })}
                </div>
            )}
        </div>
    );
}
