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
    NEW_CHAT, PLAN_PATH, PLAN_SECTIONS, SECTION_TITLES,
    ageOf, chatItems, chatView, draftPlan, formatMeasure, inSidebar, outcomeView, sectionRows, standingOf, stampOf, workRows,
    type Ask, type Go, type OutcomeTab, type Plan, type PlanLine, type PlanSection, type QueueItem,
} from "./model";
import { mcpUrl, planRequest, PlanMovedOn, readPlan, savePlan, useMeasure } from "./data";
import { Avatar, Empty, Icon, to } from "./parts";
import type { FrameActions } from "./actions";
import { NeedRow } from "./answer";
import { AppTiles, AskBox, useApps } from "./ask";
import { AutomationsTab } from "./automations";
import { LemmaActions, SectionSwitch, closeMenu, sectionLabel, useLemmaSections, type LemmaSection } from "./lemma-bar";
import { kindOf, markOf } from "./sidebar";
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
    const bar = <LemmaActions pod={pod} go={go} actions={actions} />;
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
                        onNew={() => go(chatView({ kind: "chat", podId: pod.id, id: NEW_CHAT }))}
                        onConnect={() => actions.connect(pod.id)}
                        onReach={() => actions.connect(pod.id)}
                        onPages={() => go(outcomeView(pod.id, "made"))}
                        onAsk={(text) => actions.prompt(pod.id, text)}
                    />
                )}
                {tab === "apps" && <AppsTab pod={pod} go={go} />}
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
const RECENT_SHOWN = 8;
/** How many apps the summary shows before "All". */
const APPS_SHOWN = 4;

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
    const solo = useMemo(() => [pod], [pod]);
    const { apps } = useApps(solo);
    const rows = useMemo(() => {
        const { needs, rest } = chatItems({ pods: [pod], conversations: new Map([[pod.id, activity]]), channels: new Map([[pod.id, allChannels(channelList.data)]]), me: reader });
        const work = workRows({
            podId: pod.id,
            conversations: activity,
            tables: (tables.data?.items ?? []).filter((item) => item.kind === "table").map((item) => ({ name: item.name, title: readableName(item.name), updated: item.updated })),
            now,
        });
        return sectionRows([...needs, ...rest].filter(inSidebar), work).filter((row) => !row.needsYou);
    }, [pod, activity, channelList.data, tables.data, reader, now]);
    const tableCount = (tables.data?.items ?? []).filter((item) => item.kind === "table").length;

    return (
        <div className="ox-page ox-lsum">
            <header className="ox-lsum__head">
                <TeammateFace pod={pod} size={52} />
                <div className="ox-lsum__id">
                    <h1>{pod.name}</h1>
                    {pod.description && <p>{pod.description}</p>}
                </div>
            </header>
            {owns ? (
                <section className="ox-owns" aria-label={`What ${pod.name} owns`}>
                    <p className="ox-owns__t"><span>Owns</span>{owns}</p>
                    {plan && value !== null
                        ? <div className="ox-stand"><span className="ox-big ox-num">{formatMeasure(value, plan.unit)}</span>{plan.target && <span className="ox-vs">target {plan.target}</span>}{standing && <span className={"ox-pill" + (standing === "behind" ? " ox-pill--behind" : "")}>{standing === "behind" ? "Behind" : "On track"}</span>}</div>
                        : measure.isError ? <span className="ox-vs">The measure didn’t run</span> : null}
                </section>
            ) : plan !== undefined && (
                <section className="ox-ownit" aria-label={`What ${pod.name} owns`}>
                    <div>
                        <p className="ox-ownit__t">What does {pod.name} own?</p>
                        <p className="ox-vs">A bar it holds every week, or a finish line by a date, and the number that says how it is doing.</p>
                    </div>
                    <div className="ox-ownit__acts">
                        <button type="button" className="ox-btn ox-btn--primary" onClick={() => onAsk(pod.id, planRequest(pod.description ?? ""))}>Write it with {pod.name}</button>
                        <a className="ox-btn" {...to(outcomeView(pod.id, "plan"), go)}>Set it yourself</a>
                    </div>
                </section>
            )}
            <div className="ox-lsum__cols">
                <div className="ox-lsum__main">
                    <AskBox pods={[pod]} podId={pod.id} fixed onAsk={onAsk} placeholder={`Ask ${pod.name} anything, or say what to do`} />
                    {queue.length > 0 && (
                        <section aria-label="Needs you">
                            <div className="ox-sec"><h2>Needs you</h2></div>
                            <div className="ox-queue">
                                {queue.map((item) => <NeedRow key={item.key} item={item} pods={[pod]} now={now} go={go} showLemma={false} />)}
                            </div>
                        </section>
                    )}
                    <section aria-label="Recently">
                        <div className="ox-sec"><h2>Recently</h2></div>
                        {rows.length === 0 ? <p className="ox-vs">Nothing yet. Ask {pod.name} something, and it shows here.</p> : (
                            <div className="ox-recent">
                                {rows.slice(0, RECENT_SHOWN).map((row) => (
                                    <a key={row.key} className="ox-recent__row" {...to(row.view, go)}>
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
                    {apps.length > 0 && (
                        <section aria-label="Apps">
                            <div className="ox-sec">
                                <h2>Apps</h2>
                                {apps.length > APPS_SHOWN && <a className="ox-link" {...to(outcomeView(pod.id, "apps"), go)}>All {apps.length}</a>}
                            </div>
                            <AppTiles apps={apps.slice(0, APPS_SHOWN)} go={go} showWhose={false} />
                        </section>
                    )}
                </div>
                <aside className="ox-lsum__side">
                    <SectionRows pod={pod} plan={plan} queue={queue} apps={apps.length} tables={tables.isSuccess ? tableCount : null} go={go} />
                    <PeopleIn pod={pod} actions={actions} />
                    <ReachedOn pod={pod} actions={actions} />
                </aside>
            </div>
        </div>
    );
}

/** Its sections as rows, each saying what is in it, each opening its page. */
function SectionRows({ pod, plan, queue, apps, tables, go }: {
    pod: Pod;
    plan: Plan | null | undefined;
    queue: readonly QueueItem[];
    apps: number;
    /** Null until they are counted. */
    tables: number | null;
    go: Go;
}) {
    const sections = useLemmaSections(pod.id);
    const workflows = useWorkflowList(pod.id);
    const schedules = useQuery({ queryKey: ["outcome-schedules", pod.id], queryFn: () => source.listSchedules(pod.id), staleTime: 5 * 60_000 });
    const lines = plan ? PLAN_SECTIONS.reduce((sum, one) => sum + plan.sections[one].length, 0) : 0;
    const waiting = queue.filter((item) => item.contactId).length;
    const flows = workflows.data?.length ?? 0;
    const jobs = (schedules.data ?? []).filter((job) => !job.internal).length;
    const said: Record<LemmaSection, string> = {
        plan: plan === undefined ? "" : lines ? counted(lines, "line") : "Not written yet",
        chats: "Every chat, and the shared ones",
        apps: apps ? counted(apps, "app") : "None yet",
        made: tables ? `${counted(tables, "table")}, pages and files` : "Pages, files and tables",
        contacts: waiting ? `${waiting} waiting on a person` : "",
        automations: [flows ? counted(flows, "workflow") : "", jobs ? counted(jobs, "schedule") : ""].filter(Boolean).join(" · ") || (workflows.isPending ? "" : "None yet"),
        settings: "",
    };
    return (
        <nav className="ox-secrows" aria-label={`${pod.name}’s sections`}>
            {sections.map((one) => (
                <a key={one.tab} className="ox-secrow" {...to(outcomeView(pod.id, one.tab), go)}>
                    <Icon name={one.icon} size={16} />
                    <span className="ox-secrow__t">{one.label}</span>
                    <span className={"ox-secrow__d" + (one.tab === "contacts" && waiting ? " ox-secrow__d--you" : "")}>{said[one.tab]}</span>
                </a>
            ))}
        </nav>
    );
}

const PEOPLE_SHOWN = 5;
const PLACES_SHOWN = 4;

function PeopleIn({ pod, actions }: { pod: Pod; actions: FrameActions }) {
    return (
        <section className="ox-sideb" aria-label="People">
            <h2 className="ox-sideb__h">People</h2>
            <ul className="ox-sideb__list">
                {pod.members.slice(0, PEOPLE_SHOWN).map((member) => (
                    <li key={member.id}><Avatar name={member.name} size={22} /><span>{member.name}</span></li>
                ))}
            </ul>
            {pod.members.length > PEOPLE_SHOWN && <p className="ox-vs">and {pod.members.length - PEOPLE_SHOWN} more</p>}
            <button type="button" className="ox-link" onClick={() => actions.addPeople(pod.id)}>Who has access</button>
        </section>
    );
}

/** Where people reach it besides here: the numbers and addresses that
 *  answer as this Lemma. */
function ReachedOn({ pod, actions }: { pod: Pod; actions: FrameActions }) {
    const surfaces = useQuery({ queryKey: ["outcome-surfaces", pod.id], queryFn: () => source.listSurfaces(pod.id), staleTime: 5 * 60_000, retry: false });
    const on = (surfaces.data ?? []).filter((surface) => surface.active);
    const shown = on.slice(0, PLACES_SHOWN);
    return (
        <section className="ox-sideb" aria-label="Where people reach it">
            <h2 className="ox-sideb__h">Where people reach it</h2>
            {surfaces.isSuccess && on.length === 0 && <p className="ox-vs">Only here, so far.</p>}
            {shown.length > 0 && (
                <ul className="ox-sideb__list">
                    {shown.map((surface) => (
                        <li key={surface.id}><span>{platformName(surface.platform)}</span>{surface.handle && <span className="ox-vs">{surface.handle}</span>}</li>
                    ))}
                </ul>
            )}
            {on.length > shown.length && <p className="ox-vs">and {on.length - shown.length} more</p>}
            <button type="button" className="ox-link" onClick={() => actions.connect(pod.id)}>{on.length > 0 ? "All of them, or connect another" : "Connect a place"}</button>
        </section>
    );
}

/* ── plan ─────────────────────────────────────────────────────────────── */

/** The plan: what it owns and how, line by line, with who wrote each. With
 *  none yet, a draft read off what it already runs -- to save as it is, or
 *  to have the Lemma write properly in a chat. */
function PlanTab({ pod, me, go, onAsk }: { pod: Pod; me: string; go: Go; onAsk: Ask }) {
    const queryClient = useQueryClient();
    /* The frame's own read of the plan, shared by key, asked here for its
       failure: a plan that could not be read is not a pod with no plan. */
    const read = useQuery({ queryKey: ["outcome-plan", pod.id], queryFn: () => readPlan(pod.id), staleTime: 5 * 60_000 });
    const plan = read.data;
    const [busy, setBusy] = useState(false);
    const [note, setNote] = useState<string | null>(null);

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
                        : <PlanEditor key={pod.id} plan={plan} busy={busy} me={me} onChange={(next) => save(next, true)} file={to({ name: "file", podId: pod.id, path: PLAN_PATH }, go)} />}
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

function PlanEditor({ plan, busy, me, onChange, file }: { plan: Plan; busy: boolean; me: string; onChange: (plan: Plan) => void; file: ReturnType<typeof to> }) {
    const [editing, setEditing] = useState<{ section: PlanSection; index: number; text: string } | null>(null);
    const [adding, setAdding] = useState<Record<PlanSection, string>>({ must: "", how: "", who: "", never: "" });
    const [measureOpen, setMeasureOpen] = useState(false);
    const metaOf = (from: Plan) => ({ outcome: from.outcome, kind: from.kind ?? "week", target: from.target ?? "", unit: from.unit ?? "", due: from.due ?? "", measure: from.measure ?? "" });
    const [meta, setMeta] = useState<{ outcome: string; kind: string; target: string; unit: string; due: string; measure: string }>(() => metaOf(plan));
    /* Filled from the plan as it is now each time the form opens, so a save
       never writes back values a later read replaced. */
    const toggleMeasure = () => {
        if (!measureOpen) setMeta(metaOf(plan));
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
                    {plan.measure ? "The measure and target" : "Add a measure, so what it owns gets a number"}
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
                    <div className="ox-plan__acts"><button type="button" className="ox-btn ox-btn--primary" onClick={saveMeta} disabled={busy}>Save</button><button type="button" className="ox-btn" onClick={() => setMeasureOpen(false)}>Cancel</button></div>
                </div>
            )}
        </section>
    );
}

/* ── apps ─────────────────────────────────────────────────────────────── */

function AppsTab({ pod, go }: { pod: Pod; go: Go }) {
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
                    <a className="ox-btn ox-btn--lemma" {...to(chatView({ kind: "chat", podId: pod.id, id: NEW_CHAT }), go)}>Ask {pod.name} to build one</a>
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
