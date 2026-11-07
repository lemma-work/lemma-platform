"use client";

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type ConversationRef, type Pod } from "@/data";
import { ContactsPage } from "@/space/contacts-page";
import { ChatsPage } from "@/space/chats-page";
import { PAGE_TEMPLATES, makePage } from "@/docpages/templates";
import { Library } from "@/library/library";
import { AppCover } from "@/stage/app-cover";
import { readableName } from "@/library/reading";
import { useMaySeeContacts } from "@/space/contact-queries";
import { TeammateFace } from "@/space/teammate-face";
import { allChannels, useChannelList } from "@/channels/queries";
import { sinceShort } from "@/channels/model";
import { platformName } from "@/thread/conversation-origin";
import { useMe } from "@/session/use-me";
import { useWorkflowList } from "@/workflow/use-run";
import {
    NOTHING_OWNED, PLAN_PATH, PLAN_SECTIONS, SECTION_TITLES,
    ageOf, chatItems, chatView, draftPlan, formatMeasure, inSidebar, outcomeView, sectionRows, standingOf, stampOf, withOwned, workRows,
    type Ask, type Go, type OutcomeTab, type Owned, type Plan, type PlanLine, type PlanSection, type QueueItem, type View,
} from "./model";
import { mcpUrl, planRequest, PlanMovedOn, readPlan, saveOwned, savePlan, standingKey, useMeasure, useOwned, useWeeks } from "./data";
import { Empty, Icon, to, type IconName } from "./parts";
import type { FrameActions } from "./actions";
import { NeedRow } from "./answer";
import { AppTiles, AskBox, useApps, type PodApp } from "./ask";
import { StartWith } from "./chats";
import { AutomationsTab } from "./automations";
import { LemmaActions, SectionSwitch, closeMenu, sectionLabel, useLemmaSections } from "./lemma-bar";
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

/** "1 table", "3 tables". */
function counted(n: number, one: string): string {
    return `${n} ${one}${n === 1 ? "" : "s"}`;
}

/** One Lemma. At its own address, a summary: who it is and what it owns,
 *  a box to ask it, what it needs from you, what it has been doing and its
 *  apps, with its sections listed beside them. Each section -- the plan,
 *  apps, files, contacts, automations -- is a page of its own under the bar
 *  every page of a Lemma shares, and names itself once. (Settings is drawn
 *  by the shell, full height, under the same bar.) */
export function OutcomePage({ pod, pods, tab, section = null, plan, activity, queue, me, go, onAsk, actions }: {
    pod: Pod;
    /** Every Lemma in the organization, for the queues that read across them. */
    pods: readonly Pod[];
    tab: OutcomeTab;
    /** One view of the tab, where the tab has views (Automations). */
    section?: string | null;
    plan: Plan | null | undefined;
    activity: readonly ConversationRef[];
    queue: readonly QueueItem[];
    me: string;
    go: Go;
    /** Start a chat with this Lemma with these words, and open it. */
    onAsk: Ask;
    actions: FrameActions;
}) {
    const contacts = useMaySeeContacts(pod.id);
    /* Its own page is where a chat with it starts, so only its other
       pages offer the way there. */
    const bar = <LemmaActions pod={pod} go={go} actions={actions} newChat={tab !== "home"} />;
    if (tab === "home") {
        return (
            <Stage pod={pod} go={go} actions={bar}>
                <LemmaSummary pod={pod} plan={plan} activity={activity} queue={queue} go={go} onAsk={onAsk} actions={actions} />
            </Stage>
        );
    }
    return (
        <Stage pod={pod} here={<SectionSwitch pod={pod} here={tab} go={go} />} go={go} actions={bar}>
            <div className="ox-page ox-section" data-section={tab}>
                <header className="ox-section__head">
                    <h1 className="ox-section__t">{sectionLabel(tab)}</h1>
                    {tab === "made" && <MakeMenu pod={pod} go={go} actions={actions} />}
                </header>
                {tab === "plan" && <PlanTab pod={pod} me={me} go={go} onAsk={onAsk} />}
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
                        onPages={() => go(outcomeView(pod.id, "made"))}
                        onAsk={(text) => actions.prompt(pod.id, text)}
                    />
                )}
                {tab === "apps" && <AppsTab pod={pod} go={go} actions={actions} />}
                {tab === "made" && <Library podId={pod.id} onFile={(path) => go({ name: "file", podId: pod.id, path })} onTable={(name) => go({ name: "table", podId: pod.id, table: name })} />}
                {tab === "contacts" && (contacts
                    ? <ContactsPage pod={pod} onConnect={() => actions.connect(pod.id)} />
                    : <Empty title="Contacts are for whoever looks after them."><p>Ask an admin of {pod.name} if you should be one.</p></Empty>)}
                {tab === "automations" && <AutomationsTab pod={pod} pods={pods} section={section} queue={queue} go={go} actions={actions} />}
            </div>
        </Stage>
    );
}

/** New, over a Lemma's files, as the workspace offers it: a blank page or
 *  one from a template, made and opened with the caret in it; a table or an
 *  app, which the Lemma makes, so they start as words in a chat's box. */
function MakeMenu({ pod, go, actions }: { pod: Pod; go: Go; actions: FrameActions }) {
    const cache = useQueryClient();
    const [making, setMaking] = useState<string | null>(null);
    const [failed, setFailed] = useState(false);
    const make = async (event: { currentTarget: Element }, id: string, base: string, text: string) => {
        closeMenu(event);
        setMaking(id);
        setFailed(false);
        try {
            /* Named like a person would, Untitled then Untitled 2, against
               what the pages folder already holds; with no listing, names are
               found by trying. Create-only, so it never writes over a page. */
            const taken = await source.listLibrary(pod.id, "files", "/pages")
                .then((listed) => new Set(listed.items.map((item) => item.name.toLowerCase())))
                .catch(() => new Set<string>());
            const path = await makePage((at, body) => source.createFile(pod.id, at, body), base, text, taken);
            void cache.invalidateQueries({ queryKey: ["library", pod.id] });
            go({ name: "file", podId: pod.id, path });
        } catch {
            setFailed(true);
        } finally {
            setMaking(null);
        }
    };
    const mate = pod.teammate?.name || pod.name;
    return (
        <div className="ox-make">
            {failed && <span className="ox-make__note" role="alert">That page couldn’t be made. Try again.</span>}
            <details className="ox-more">
                <summary className="ox-btn ox-btn--small" aria-label="New">{making ? "Making…" : "New"}<Icon name="chevron" size={14} /></summary>
                <div className="ox-more__menu" role="menu">
                    <button type="button" role="menuitem" disabled={Boolean(making)} onClick={(event) => void make(event, "page", "Untitled", "# Untitled\n\n")}>Page</button>
                    {PAGE_TEMPLATES.map((template) => (
                        <button key={template.id} type="button" role="menuitem" disabled={Boolean(making)} onClick={(event) => void make(event, template.id, template.file, template.body(mate, new Date()))}>
                            {template.title}<span className="ox-make__blurb">{template.blurb}</span>
                        </button>
                    ))}
                    <button type="button" role="menuitem" onClick={(event) => { closeMenu(event); actions.prompt(pod.id, "Set up a table to track "); }}>Table</button>
                    <button type="button" role="menuitem" onClick={(event) => { closeMenu(event); actions.prompt(pod.id, "Build an app that "); }}>App</button>
                </div>
            </details>
        </div>
    );
}

/* ── the summary ──────────────────────────────────────────────────────── */

/** How many recent things the summary lists. */
const RECENT_SHOWN = 6;
/** How many names a row of the map lists before it trails off. */
const NAMES_SHOWN = 6;
/** Apps on a Lemma's page before "All": one row of the column. */
const APPS_SHOWN = 5;

/** The first few names, and "…" when there are more. */
function names(all: readonly string[]): string {
    return all.slice(0, NAMES_SHOWN).join(", ") + (all.length > NAMES_SHOWN ? ", …" : "");
}

/** A Lemma's own page, and the new chat with it, one column read top to
 *  bottom: who it is and what it owns; what it needs from the reader; its
 *  apps, as cards, since they are what people use it through; what it has
 *  been doing; what it is made of -- and at the foot, pinned like a chat's
 *  composer, the box to ask it something and the ways to start a chat with
 *  people instead. One edge, and nothing beside the column pulling the eye
 *  away. */
function LemmaSummary({ pod, plan, activity, queue, go, onAsk, actions }: {
    pod: Pod;
    plan: Plan | null | undefined;
    activity: readonly ConversationRef[];
    queue: readonly QueueItem[];
    go: Go;
    onAsk: Ask;
    actions: FrameActions;
}) {
    const reader = useMe();
    const [now] = useState(() => Date.now());
    const measure = useMeasure(pod.id, plan);
    const value = measure.data ?? null;
    const standing = standingOf(plan, value);
    const owns = plan?.outcome || "";
    const channelList = useChannelList(pod.id, { archived: false });
    const tables = useQuery({ queryKey: ["outcome-tables", pod.id], queryFn: () => source.listLibrary(pod.id, "tables", "/"), staleTime: 5 * 60_000, retry: false });
    const tableItems = useMemo(() => (tables.data?.items ?? []).filter((item) => item.kind === "table"), [tables.data]);
    const solo = useMemo(() => [pod], [pod]);
    const { apps } = useApps(solo);
    const { chats, rows } = useMemo(() => {
        const { needs, rest } = chatItems({ pods: [pod], conversations: new Map([[pod.id, activity]]), channels: new Map([[pod.id, allChannels(channelList.data)]]), me: reader });
        const listed = [...needs, ...rest].filter(inSidebar);
        const work = workRows({
            podId: pod.id,
            conversations: activity,
            tables: tableItems.map((item) => ({ name: item.name, title: readableName(item.name), updated: item.updated })),
            now,
        });
        return { chats: listed, rows: sectionRows(listed, work).filter((row) => !row.needsYou) };
    }, [pod, activity, channelList.data, tableItems, reader, now]);

    return (
        <div className="ox-page ox-lsum">
            <header className="ox-lsum__head">
                <TeammateFace pod={pod} size={48} />
                <div className="ox-lsum__id">
                    <h1>{pod.name}</h1>
                    {pod.description && <p>{pod.description}</p>}
                    {owns ? (
                        <p className="ox-lsum__owns">
                            <span className="ox-vs">Owns</span>
                            <span>{owns}</span>
                            {plan && value !== null && <span className="ox-num">{formatMeasure(value, plan.unit)}{plan.target ? ` of ${plan.target}` : ""}</span>}
                            {plan?.owned && <Weeks podId={pod.id} owned={plan.owned} />}
                            {standing && <span className={"ox-pill" + (standing === "behind" ? " ox-pill--behind" : "")}>{standing === "behind" ? "Behind" : "On track"}</span>}
                            {measure.isError && <span className="ox-vs">The measure didn’t run</span>}
                        </p>
                    ) : plan !== undefined && (
                        <p className="ox-lsum__owns">
                            <span className="ox-vs">{NOTHING_OWNED}</span>
                            <button type="button" className="ox-textbtn" onClick={() => onAsk(pod.id, planRequest(pod.description ?? ""))}>Write it with {pod.name}</button>
                            <span className="ox-vs" aria-hidden="true">·</span>
                            <a className="ox-textbtn" {...to(outcomeView(pod.id, "plan"), go)}>Set it yourself</a>
                        </p>
                    )}
                </div>
            </header>
            {queue.length > 0 && (
                <section aria-label="Needs you">
                    <div className="ox-sec"><h2>Needs you</h2></div>
                    <div className="ox-queue">
                        {queue.map((item) => <NeedRow key={item.key} item={item} pods={[pod]} now={now} go={go} showLemma={false} />)}
                    </div>
                </section>
            )}
            {apps.length > 0 && (
                <section aria-label="Apps">
                    <div className="ox-sec">
                        <h2>Apps</h2>
                        {apps.length > APPS_SHOWN && <a className="ox-link" {...to(outcomeView(pod.id, "apps"), go)}>All {apps.length}</a>}
                    </div>
                    <AppTiles apps={apps.slice(0, APPS_SHOWN)} go={go} showWhose={false} />
                </section>
            )}
            <section aria-label="Recently">
                <div className="ox-sec"><h2>Recently</h2></div>
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
            <Inside pod={pod} plan={plan} queue={queue} apps={apps} chats={chats.map((chat) => chat.title)} tables={tables.isSuccess ? tableItems.map((item) => readableName(item.name)) : null} go={go} actions={actions} />
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

interface MapRow {
    key: string;
    icon: IconName;
    label: string;
    /** How many, where that is known and worth saying. */
    count: number | null;
    /** The first few by name, or what is there in a few words. */
    said: string;
    /** Something here waits on the reader. */
    you?: boolean;
    open: { kind: "view"; view: View } | { kind: "act"; run: () => void };
}

/** What it is made of, a row per kind -- how many, and the first few by
 *  name -- each opening where they all are: the answer to "what is in
 *  here?" read as a list, the way the Lemma would say it, not a set of
 *  panels each asking to be looked at. */
function Inside({ pod, plan, queue, apps, chats, tables, go, actions }: {
    pod: Pod;
    plan: Plan | null | undefined;
    queue: readonly QueueItem[];
    /** Its apps, drawn as cards above; the map names them only when there
     *  are none, to say so. */
    apps: readonly PodApp[];
    /** Its chats lately, by name. */
    chats: readonly string[];
    /** Its tables by name; null until they are read. */
    tables: readonly string[] | null;
    go: Go;
    actions: FrameActions;
}) {
    const sections = useLemmaSections(pod.id);
    const workflows = useWorkflowList(pod.id);
    const schedules = useQuery({ queryKey: ["outcome-schedules", pod.id], queryFn: () => source.listSchedules(pod.id), staleTime: 5 * 60_000 });
    const files = useQuery({ queryKey: ["outcome-files", pod.id], queryFn: () => source.listLibrary(pod.id, "files", "/"), staleTime: 5 * 60_000, retry: false });
    const surfaces = useQuery({ queryKey: ["outcome-surfaces", pod.id], queryFn: () => source.listSurfaces(pod.id), staleTime: 5 * 60_000, retry: false });
    /* Under the keys its Connections page reads them by. */
    const connectors = useQuery({ queryKey: ["pod-connectors", pod.id], queryFn: () => source.listPodConnectors(pod.id), staleTime: 30_000, retry: false });
    const catalog = useQuery({ queryKey: ["connectors"], queryFn: () => source.listConnectors(), staleTime: 10 * 60_000 });

    const automations = [
        ...(workflows.data ?? []).map((flow) => readableName(flow.name)),
        ...(schedules.data ?? []).filter((job) => !job.internal).map((job) => job.title),
    ];
    const automationsKnown = workflows.isSuccess && schedules.isSuccess;
    /* Its folders first, the way it would name where things live, and none
       of the dot-named ones the system keeps for itself. */
    const top = (files.data?.items ?? [])
        .filter((item) => !item.name.startsWith("."))
        .sort((a, b) => Number(b.kind === "folder") - Number(a.kind === "folder"))
        .map((item) => (item.kind === "folder" ? "/" + item.name : item.name));
    const lines = plan ? PLAN_SECTIONS.reduce((sum, one) => sum + plan.sections[one].length, 0) : 0;
    const waiting = queue.filter((item) => item.contactId).length;
    const on = (surfaces.data ?? []).filter((surface) => surface.active);
    const places = [...on.reduce((all, surface) => all.set(platformName(surface.platform), (all.get(platformName(surface.platform)) ?? 0) + 1), new Map<string, number>())]
        .map(([name, n]) => (n > 1 ? `${name} ×${n}` : name));
    const contacts = sections.some((one) => one.tab === "contacts");
    const titles = new Map((catalog.data ?? []).map((one) => [one.id, one.title]));
    const through = (connectors.data ?? []).map((one) => titles.get(one.connectorId) ?? one.installationName ?? one.slug);

    const rows: MapRow[] = [
        ...(apps.length === 0 ? [{ key: "apps", icon: "app", label: "Apps", count: null, said: "None yet", open: { kind: "view", view: outcomeView(pod.id, "apps") } } satisfies MapRow] : []),
        { key: "automations", icon: "flow", label: "Automations", count: automations.length || null, said: automations.length ? names(automations) : automationsKnown ? "None yet" : "", open: { kind: "view", view: outcomeView(pod.id, "automations") } },
        { key: "tables", icon: "table", label: "Tables", count: tables?.length || null, said: tables === null ? "" : tables.length ? names(tables) : "None yet", open: { kind: "view", view: outcomeView(pod.id, "made") } },
        { key: "files", icon: "file", label: "Files", count: null, said: top.length ? names(top) : files.isSuccess ? "None yet" : "", open: { kind: "view", view: outcomeView(pod.id, "made") } },
        { key: "chats", icon: "chat", label: "Chats", count: null, said: chats.length ? names(chats) : "None yet", open: { kind: "view", view: outcomeView(pod.id, "chats") } },
        ...(contacts ? [{ key: "contacts", icon: "people", label: "Contacts", count: waiting || null, said: waiting ? `${waiting} waiting on a person` : "People outside who wrote in", you: waiting > 0, open: { kind: "view", view: outcomeView(pod.id, "contacts") } } satisfies MapRow] : []),
        { key: "plan", icon: "skill", label: "Plan", count: lines || null, said: plan === undefined ? "" : lines ? `${counted(lines, "line")} in plan.md` : "Not written yet", open: { kind: "view", view: outcomeView(pod.id, "plan") } },
        { key: "people", icon: "agent", label: "People", count: pod.members.length || null, said: names(pod.members.map((member) => member.name)), open: { kind: "act", run: () => actions.addPeople(pod.id) } },
        { key: "connections", icon: "plug", label: "Connections", count: through.length || null, said: connectors.isSuccess ? (through.length ? names(through) : "None yet") : "", open: { kind: "view", view: outcomeView(pod.id, "settings", "connections") } },
        { key: "reach", icon: "link", label: "Where it answers", count: on.length || null, said: surfaces.isSuccess ? (places.length ? names(places) : "Only here, so far") : "", open: { kind: "act", run: () => actions.connect(pod.id) } },
        { key: "settings", icon: "gear", label: "Settings", count: null, said: "", open: { kind: "view", view: outcomeView(pod.id, "settings") } },
    ];

    return (
        <section aria-label={`What ${pod.name} is made of`}>
            <div className="ox-sec"><h2>Inside</h2></div>
            <div className="ox-map">
                {rows.map((row) => {
                    const body = (
                        <>
                            <Icon name={row.icon} size={16} />
                            <span className="ox-map__t">{row.label}</span>
                            <span className="ox-map__n ox-num">{row.count ?? ""}</span>
                            <span className={"ox-map__d" + (row.you ? " ox-map__d--you" : "")}>{row.said}</span>
                        </>
                    );
                    return row.open.kind === "view"
                        ? <a key={row.key} className="ox-map__row" {...to(row.open.view, go)}>{body}</a>
                        : <button key={row.key} type="button" className="ox-map__row" onClick={row.open.run}>{body}</button>;
                })}
            </div>
        </section>
    );
}

/* ── plan ─────────────────────────────────────────────────────────────── */

/** The plan: what it owns and how, line by line, with who wrote each. With
 *  none yet, a draft read off what it already runs -- to save as it is, or
 *  to have the Lemma write properly in a chat. */
/** The Lemma's last weeks against what it owns, once there are two to
 *  compare: a Lemma started today has one week, and one bar says nothing. */
function Weeks({ podId, owned }: { podId: string; owned: Owned }) {
    const weeks = useWeeks(podId, true);
    const list = weeks.data ?? [];
    const read = list.flatMap((week) => (typeof week.value === "number" ? [week.value] : []));
    if (read.length < 2) return null;
    const top = Math.max(owned.target ?? 0, ...read, 1);
    const said = list.map((week) => (typeof week.value === "number" ? formatMeasure(week.value, owned.unit) : "not read")).join(", ");
    return (
        <span className="ox-weeks" role="img" aria-label={`The last ${list.length} weeks: ${said}`}>
            {list.map((week, index) => {
                const value = typeof week.value === "number" ? week.value : null;
                const tone = value === null ? " ox-weeks__b--none" : index === list.length - 1 ? " ox-weeks__b--now" : "";
                return <span key={week.starts_on} className={"ox-weeks__b" + tone} style={{ height: `${value === null ? 8 : Math.max(8, Math.round((value / top) * 100))}%` }} />;
            })}
        </span>
    );
}

function PlanTab({ pod, me, go, onAsk }: { pod: Pod; me: string; go: Go; onAsk: Ask }) {
    const queryClient = useQueryClient();
    const solo = useMemo(() => [pod], [pod]);
    const owned = useOwned(solo).get(pod.id) ?? null;
    /* The frame's own read of the plan, shared by key, asked here for its
       failure: a plan that could not be read is not a pod with no plan. */
    const read = useQuery({ queryKey: ["outcome-plan", pod.id], queryFn: () => readPlan(pod.id), staleTime: 5 * 60_000 });
    const plan = read.data;
    const [busy, setBusy] = useState(false);
    const [note, setNote] = useState<string | null>(null);

    /* What it owns is the Lemma's, kept on the server, so a change to it goes
       there and the number is read again; the plan's lines stay in the file.
       The sample source has no server to keep it, so its plan file does. */
    const own = source.label === "live"
        ? async (next: Owned) => {
            setBusy(true);
            setNote(null);
            try {
                await saveOwned(pod.id, next);
                await queryClient.invalidateQueries({ queryKey: standingKey(pod.id) });
                setNote("Saved");
            } catch (error) {
                setNote(error instanceof Error ? `Couldn’t save: ${error.message}` : "Couldn’t save what it owns");
                throw error;
            } finally {
                setBusy(false);
            }
        }
        : null;

    const save = async (next: Plan, exists: boolean) => {
        setBusy(true);
        setNote(null);
        try {
            queryClient.setQueryData(["outcome-plan", pod.id], await savePlan(pod.id, next, exists));
            setNote("Saved to plan.md");
        } catch (error) {
            if (error instanceof PlanMovedOn) void queryClient.invalidateQueries({ queryKey: ["outcome-plan", pod.id] });
            setNote(error instanceof Error ? `Couldn’t save: ${error.message}` : "Couldn’t save the plan");
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="ox-plantab">
            {plan === undefined && read.isError
                ? (
                    <Empty title="The plan couldn’t be read.">
                        <button type="button" className="ox-btn" onClick={() => void read.refetch()} disabled={read.isFetching}>{read.isFetching ? "Reading…" : "Try again"}</button>
                    </Empty>
                )
                : plan === undefined
                    ? <p className="ox-loading">Reading the plan…</p>
                    : plan === null
                        ? <PlanDraft key={pod.id} pod={pod} busy={busy} onSave={(draft) => save(draft, false)} onAsk={() => onAsk(pod.id, planRequest(pod.description ?? ""))} me={me} />
                        : <PlanEditor key={pod.id} plan={plan} owned={owned} busy={busy} me={me} onChange={(next) => save(next, true)} onOwn={own} file={to({ name: "file", podId: pod.id, path: PLAN_PATH }, go)} />}
            {note && <p className="ox-note" role="status">{note}</p>}
        </div>
    );
}

function PlanDraft({ pod, busy, onSave, onAsk, me }: { pod: Pod; busy: boolean; onSave: (plan: Plan) => void; onAsk: () => void; me: string }) {
    const schedules = useQuery({ queryKey: ["outcome-schedules", pod.id], queryFn: () => source.listSchedules(pod.id), staleTime: 5 * 60_000 });
    const agents = useQuery({ queryKey: ["outcome-agents", pod.id], queryFn: () => source.listAgents(pod.id), staleTime: 5 * 60_000 });
    const skills = useQuery({ queryKey: ["outcome-skills", pod.id], queryFn: () => source.listLibrary(pod.id, "files", "/skills"), staleTime: 5 * 60_000, retry: false });
    const ready = !schedules.isPending && !agents.isPending && !skills.isPending;
    const draft = draftPlan({
        description: pod.description,
        schedules: schedules.data ?? [],
        agents: agents.data ?? [],
        skills: (skills.data?.items ?? []).map((item) => item.name.replace(/\.md$/i, "")),
    });
    const stamped: Plan = {
        ...draft,
        sections: Object.fromEntries(PLAN_SECTIONS.map((section) => [section, draft.sections[section].map((line) => ({ ...line, by: me, on: stampOf(new Date()) }))])) as Record<PlanSection, PlanLine[]>,
    };
    return (
        <section className="ox-plan ox-plan--draft">
            <div className="ox-plan__intro">
                <p className="ox-plan__lead">No plan yet. Here is a draft from what {pod.name} already runs.</p>
                <div className="ox-plan__acts">
                    <button type="button" className="ox-btn ox-btn--primary" disabled={busy || !ready} onClick={() => onSave(stamped)}>Save as the plan</button>
                    <button type="button" className="ox-btn ox-btn--lemma" disabled={busy} onClick={onAsk}>Ask {pod.name} to write it</button>
                </div>
            </div>
            {!ready ? <p className="ox-loading">Reading what it runs…</p> : PLAN_SECTIONS.map((section) => (
                <DraftSection key={section} title={SECTION_TITLES[section]} lines={draft.sections[section]} />
            ))}
        </section>
    );
}

/** How many lines of a drafted section show before the rest fold away: a
 *  draft read off thirteen schedules is a list of machinery, not a plan. */
const DRAFT_SHOWN = 3;

function DraftSection({ title, lines }: { title: string; lines: readonly PlanLine[] }) {
    const [all, setAll] = useState(false);
    return (
        <div className="ox-plan__sec">
            <p className="ox-plan__h">{title}</p>
            {lines.length === 0
                ? <p className="ox-plan__none">Nothing yet</p>
                : <ul>{(all ? lines : lines.slice(0, DRAFT_SHOWN)).map((line, index) => <li key={index} className="ox-line"><span className="ox-line__dot" aria-hidden="true" /><span className="ox-line__t">{line.text}</span></li>)}</ul>}
            {lines.length > DRAFT_SHOWN && (
                <button type="button" className="ox-plan__more" onClick={() => setAll((was) => !was)}>{all ? "Show fewer" : `${lines.length - DRAFT_SHOWN} more`}</button>
            )}
        </div>
    );
}

function PlanEditor({ plan: lines, owned, busy, me, onChange, onOwn, file }: {
    plan: Plan;
    owned: Owned | null;
    busy: boolean;
    me: string;
    onChange: (plan: Plan) => void;
    /** Where what it owns is saved, when the Lemma keeps it rather than its file. */
    onOwn: ((next: Owned) => Promise<void>) | null;
    file: ReturnType<typeof to>;
}) {
    const plan = lines;
    const shown = withOwned(lines, owned) ?? lines;
    const [editing, setEditing] = useState<{ section: PlanSection; index: number; text: string } | null>(null);
    const [adding, setAdding] = useState<Record<PlanSection, string>>({ must: "", how: "", who: "", never: "" });
    const [measureOpen, setMeasureOpen] = useState(false);
    const metaOf = (from: Plan) => ({ outcome: from.outcome, kind: from.kind ?? "week", target: from.target ?? "", unit: from.unit ?? "", due: from.due ?? "", measure: from.measure ?? "" });
    const [meta, setMeta] = useState<{ outcome: string; kind: string; target: string; unit: string; due: string; measure: string }>(() => metaOf(shown));
    /* Filled from the plan as it is now each time the form opens, so a save
       never writes back values a later read replaced. */
    const [metaProblem, setMetaProblem] = useState<string | null>(null);
    const toggleMeasure = () => {
        if (!measureOpen) setMeta(metaOf(shown));
        setMetaProblem(null);
        setMeasureOpen(!measureOpen);
    };

    const withSection = (section: PlanSection, lines: PlanLine[]): Plan => ({ ...plan, sections: { ...plan.sections, [section]: lines } });
    const add = (section: PlanSection) => {
        const text = adding[section].trim();
        if (!text) return;
        onChange(withSection(section, [...plan.sections[section], { text, by: me, on: stampOf(new Date()) }]));
        setAdding((now) => ({ ...now, [section]: "" }));
    };
    const commit = () => {
        if (!editing) return;
        const text = editing.text.trim();
        const lines = [...plan.sections[editing.section]];
        /* A line clicked and left as it was: nothing to write. */
        if (text === lines[editing.index]?.text) {
            setEditing(null);
            return;
        }
        if (text) lines[editing.index] = { ...lines[editing.index], text };
        else lines.splice(editing.index, 1);
        onChange(withSection(editing.section, lines));
        setEditing(null);
    };
    const remove = (section: PlanSection, index: number) => {
        const lines = [...plan.sections[section]];
        lines.splice(index, 1);
        onChange(withSection(section, lines));
    };
    const saveMeta = () => {
        if (onOwn) {
            const said = meta.target.trim();
            const target = said ? Number.parseFloat(said) : null;
            const unit = meta.unit.trim() || null;
            /* Said back rather than dropped: a target the server would keep as
               nothing, or a % against a unit picked as a count, is a mistake
               the person can fix here, not one to save over the last target. */
            if (target !== null && !Number.isFinite(target)) {
                setMetaProblem("The target needs a number, like 90 or 90%.");
                return;
            }
            if (/%\s*$/.test(said) && unit !== "%") {
                setMetaProblem("The target says % but the unit is a count. Pick “A share (%)”, or drop the %.");
                return;
            }
            setMetaProblem(null);
            void onOwn({
                sentence: meta.outcome.trim(),
                kind: meta.kind === "date" ? "date" : "week",
                target,
                unit,
                due: meta.kind === "date" ? meta.due.trim() || null : null,
                measure: meta.measure.trim() || null,
                value: null,
                problem: null,
            }).then(() => setMeasureOpen(false), () => undefined);
            return;
        }
        onChange({
            ...plan,
            outcome: meta.outcome.trim(),
            kind: meta.kind === "date" ? "date" : "week",
            target: meta.target.trim() || null,
            unit: meta.unit.trim() || null,
            due: meta.kind === "date" ? meta.due.trim() || null : null,
            measure: meta.measure.trim() || null,
        });
        setMeasureOpen(false);
    };

    return (
        <section className="ox-plan">
            {PLAN_SECTIONS.map((section) => (
                <div key={section} className="ox-plan__sec">
                    <p className="ox-plan__h">{SECTION_TITLES[section]}</p>
                    <ul>
                        {plan.sections[section].map((line, index) => (
                            <li key={index} className="ox-line">
                                <span className="ox-line__dot" aria-hidden="true" />
                                {editing && editing.section === section && editing.index === index ? (
                                    <input
                                        className="ox-line__input"
                                        aria-label="Edit the line"
                                        value={editing.text}
                                        autoFocus
                                        onChange={(event) => setEditing({ ...editing, text: event.target.value })}
                                        onKeyDown={(event) => { if (event.key === "Enter") commit(); if (event.key === "Escape") setEditing(null); }}
                                        onBlur={commit}
                                    />
                                ) : (
                                    <button type="button" className="ox-line__t" onClick={() => setEditing({ section, index, text: line.text })} disabled={busy}>{line.text}</button>
                                )}
                                <span className="ox-line__by">{line.by ? `${line.by}${line.on ? " · " + line.on : ""}` : ""}</span>
                                <button type="button" className="ox-line__x" aria-label={`Remove “${line.text}”`} onClick={() => remove(section, index)} disabled={busy}>×</button>
                            </li>
                        ))}
                    </ul>
                    <form className="ox-add" onSubmit={(event) => { event.preventDefault(); add(section); }}>
                        <Icon name="plus" size={14} />
                        <label className="ox-sr" htmlFor={`add-${section}`}>Add to {SECTION_TITLES[section]}</label>
                        <input id={`add-${section}`} value={adding[section]} placeholder="Add a line" onChange={(event) => setAdding((now) => ({ ...now, [section]: event.target.value }))} disabled={busy} />
                    </form>
                </div>
            ))}
            <div className="ox-measure">
                <button type="button" className="ox-link" onClick={toggleMeasure} aria-expanded={measureOpen}>
                    {shown.measure ? "The measure and target" : "Add a measure, so what it owns gets a number"}
                </button>
                <a className="ox-link" {...file}>Open plan.md</a>
            </div>
            {measureOpen && (
                <div className="ox-measure__form">
                    <label>What it owns<input value={meta.outcome} onChange={(event) => setMeta({ ...meta, outcome: event.target.value })} placeholder="Every customer hears back within a day" /></label>
                    <div className="ox-measure__row">
                        <label>Kind<select value={meta.kind} onChange={(event) => setMeta({ ...meta, kind: event.target.value })}><option value="week">A bar every week</option><option value="date">A finish line by a date</option></select></label>
                        <label>Target<input value={meta.target} onChange={(event) => setMeta({ ...meta, target: event.target.value })} placeholder="90%" /></label>
                        <label>Unit<select value={meta.unit} onChange={(event) => setMeta({ ...meta, unit: event.target.value })}><option value="">A count</option><option value="%">A share (%)</option></select></label>
                        {meta.kind === "date" && <label>Due<input type="date" value={meta.due} onChange={(event) => setMeta({ ...meta, due: event.target.value })} /></label>}
                    </div>
                    <label>Measure, one read-only SQL query that answers a single number<textarea rows={4} value={meta.measure} onChange={(event) => setMeta({ ...meta, measure: event.target.value })} placeholder="SELECT round(100.0 * avg(CASE WHEN replied_within_hours <= 24 THEN 1 ELSE 0 END)) FROM conversations WHERE created_at > now() - interval '7 days'" /></label>
                    {metaProblem && <p className="ox-note" role="alert">{metaProblem}</p>}
                    <div className="ox-plan__acts"><button type="button" className="ox-btn ox-btn--primary" onClick={saveMeta} disabled={busy}>Save</button><button type="button" className="ox-btn" onClick={() => setMeasureOpen(false)}>Cancel</button></div>
                </div>
            )}
        </section>
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
