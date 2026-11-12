"use client";

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
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
    PLAN_PATH, PLAN_SECTIONS, SECTION_TITLES,
    ageOf, chatItems, chatView, draftPlan, formatMeasure, holdsNothing, inSidebar, isChat, isNewcomer, isRunning, lemmaSays, outcomeView, partOfAbout, sectionRows, welcomeSaid, standingOf, stampOf, withOwned, workRows,
    type Ask, type Go, type OutcomeTab, type Owned, type Plan, type PlanLine, type PlanSection, type QueueItem,
} from "./model";
import { mcpUrl, planRequest, PlanMovedOn, readPlan, saveOwned, savePlan, standingKey, useLemmaOpenness, useMeasure, useOwned, useWeeks } from "./data";
import { Empty, Icon, to } from "./parts";
import type { FrameActions } from "./actions";
import { NeedRow } from "./answer";
import { AppTiles, AskBox, useApps } from "./ask";
import { StartWith } from "./chats";
import { AboutTab } from "./about";
import { CanDo } from "./can-do";
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
 *  its plan, its people, what it runs and who writes in are parts of About,
 *  each a page that names itself and leads back. (Settings is drawn by the
 *  shell, full height, under the bar the things it holds wear.) */
export function OutcomePage({ pod, pods, orgName, tab, section = null, plan, activity, queue, me, go, onAsk, actions }: {
    pod: Pod;
    /** Every Lemma in the organization, for the queues that read across them. */
    pods: readonly Pod[];
    orgName: string;
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
    const empty = useLemmaEmpty(pod);
    const measure = useMeasure(pod.id, plan);
    /* What it is for, until what it holds has been read: a guess at how it
       is doing would be said in its voice and then taken back. */
    const says = empty === null
        ? pod.description ?? ""
        : lemmaSays({ empty, running: activity.filter(isRunning).length, needs: queue.length, standing: standingOf(plan, measure.data ?? null) });
    const head = <LemmaHead pod={pod} says={says} tab={tab} go={go} actions={actions} />;
    if (tab === "home") {
        return (
            <Stage pod={pod} go={go} head={head}>
                <LemmaHome pod={pod} plan={plan} activity={activity} queue={queue} me={me} go={go} onAsk={onAsk} actions={actions} />
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
                {tab === "about" && <AboutTab pod={pod} orgName={orgName} plan={plan} activity={activity} go={go} actions={actions} />}
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
function LemmaHome({ pod, plan, activity, queue, me, go, onAsk, actions }: {
    pod: Pod;
    plan: Plan | null | undefined;
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
    return <LemmaSummary pod={pod} plan={plan} activity={activity} queue={queue} me={me} go={go} onAsk={onAsk} actions={actions} />;
}

/** How many recent things the summary lists. */
const RECENT_SHOWN = 6;
/** Apps on a Lemma's page before "All": one row of large tiles. */
const APPS_SHOWN = 3;

/** A Lemma's home once something is in it, one column read top to bottom:
 *  what it owns, when it owns something; its apps, large, since they are
 *  what people use it through; what it needs from the reader; what it has
 *  been doing -- and at the foot, pinned like a chat's composer, the box to
 *  ask it something and the ways to start a chat with people instead. Who it
 *  is and how it is doing are the header's, so nothing here says them again. */
function LemmaSummary({ pod, plan, activity, queue, me, go, onAsk, actions }: {
    pod: Pod;
    plan: Plan | null | undefined;
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
    const measure = useMeasure(pod.id, plan);
    const value = measure.data ?? null;
    const standing = standingOf(plan, value);
    const owns = plan?.outcome || "";
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
            {/* Owning a result is something a Lemma takes on, not what it is:
                the line shows once it owns one, and says nothing before. */}
            {owns && (
                <p className="ox-lsum__owns">
                    <span className="ox-vs">Owns</span>
                    <span>{owns}</span>
                    {plan && value !== null && <span className="ox-num">{formatMeasure(value, plan.unit)}{plan.target ? ` of ${plan.target}` : ""}</span>}
                    {plan?.owned && <Weeks podId={pod.id} owned={plan.owned} />}
                    {standing && <span className={"ox-pill" + (standing === "behind" ? " ox-pill--behind" : "")}>{standing === "behind" ? "Behind" : "On track"}</span>}
                    {measure.isError && <span className="ox-vs">The measure didn’t run</span>}
                </p>
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
            <CanDo pod={pod} plan={plan} activity={activity} go={go} actions={actions} />
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
                setMetaProblem("The target says % but the unit is a number. Pick “A percentage”, or drop the %.");
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
                        <label>Kind<select value={meta.kind} onChange={(event) => setMeta({ ...meta, kind: event.target.value })}><option value="week">A number to hit every week</option><option value="date">Something to finish by a date</option></select></label>
                        <label>Target<input value={meta.target} onChange={(event) => setMeta({ ...meta, target: event.target.value })} placeholder="90%" /></label>
                        <label>Unit<select value={meta.unit} onChange={(event) => setMeta({ ...meta, unit: event.target.value })}><option value="">A number</option><option value="%">A percentage</option></select></label>
                        {meta.kind === "date" && <label>Due<input type="date" value={meta.due} onChange={(event) => setMeta({ ...meta, due: event.target.value })} /></label>}
                    </div>
                    <label>How to count it: one read-only SQL query that gives a single number<textarea rows={4} value={meta.measure} onChange={(event) => setMeta({ ...meta, measure: event.target.value })} placeholder="SELECT round(100.0 * avg(CASE WHEN replied_within_hours <= 24 THEN 1 ELSE 0 END)) FROM conversations WHERE created_at > now() - interval '7 days'" /></label>
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
