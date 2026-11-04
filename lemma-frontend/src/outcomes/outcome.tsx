"use client";

import { useMemo, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type ConversationRef, type Pod } from "@/data";
import { ContactsPage } from "@/space/contacts-page";
import { Library } from "@/library/library";
import { AppCover } from "@/stage/app-cover";
import { platformName } from "@/thread/conversation-origin";
import { readableName } from "@/library/reading";
import { useWorkflowList } from "@/workflow/use-run";
import { useMaySeeContacts } from "@/space/contact-queries";
import { TeammateFace } from "@/space/teammate-face";
import { allChannels, useChannelList } from "@/channels/queries";
import { sinceShort } from "@/channels/model";
import { useMe } from "@/session/use-me";
import {
    NEW_CHAT, NOTHING_OWNED, OUTCOME_TABS, PLAN_PATH, PLAN_SECTIONS, SECTION_TITLES,
    ageOf, chatItems, chatView, draftPlan, formatMeasure, inSidebar, outcomeView, sectionRows, standingOf, stampOf, viewOfItem, workRows,
    type Go, type OutcomeTab, type Plan, type PlanLine, type PlanSection, type QueueItem,
} from "./model";
import { classic, mcpUrl, planRequest, PlanMovedOn, readPlan, savePlan, useMeasure } from "./data";
import { Avatar, Empty, Icon, to, type IconName } from "./parts";
import type { FrameActions } from "./actions";
import { AskBox } from "./ask";
import { WhatItRuns } from "./runs";
import { kindOf, markOf } from "./sidebar";

const TAB_LABELS: Record<OutcomeTab, string> = { home: "Home", plan: "Plan", apps: "Apps", made: "Files", contacts: "Contacts", xray: "X-ray", settings: "Settings" };

/** Close the menu a click inside it came from. */
function closeMenu(event: { currentTarget: Element }) {
    event.currentTarget.closest("details")?.removeAttribute("open");
}

async function copy(text: string): Promise<boolean> {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch {
        return false;
    }
}

/** One Lemma: who it is -- its face and name -- and what it owns, with its
 *  number, on top. Its home is where you talk to it, what it needs from
 *  you, what it has been doing and what it runs; then the plan, its apps,
 *  what it has made, the people outside who wrote in, and X-ray. */
export function OutcomePage({ pod, tab, plan, activity, queue, me, go, onAsk, actions }: {
    pod: Pod;
    tab: OutcomeTab;
    plan: Plan | null | undefined;
    activity: readonly ConversationRef[];
    queue: readonly QueueItem[];
    me: string;
    go: Go;
    /** Start a chat with this Lemma with these words, and open it. */
    onAsk: (podId: string, text: string) => void;
    actions: FrameActions;
}) {
    const measure = useMeasure(pod.id, plan);
    const value = measure.data ?? null;
    const standing = standingOf(plan, value);
    const owns = plan?.outcome || "";
    const newChat = to(chatView({ kind: "chat", podId: pod.id, id: NEW_CHAT }), go);
    const contacts = useMaySeeContacts(pod.id);
    const tabs = OUTCOME_TABS.filter((one) => one !== "contacts" || contacts);
    return (
        <div className="ox-page ox-page--lemma">
            <p className="ox-crumb">
                <a {...to({ name: "outcomes" }, go)}>Lemmas</a>
                <span aria-hidden="true">/</span>
                <span>{pod.name}</span>
            </p>
            <section className="ox-ohead">
                <div className="ox-ohead__l">
                    <div className="ox-ohead__who">
                        <TeammateFace pod={pod} size={40} />
                        <h1 className="ox-ohead__name">{pod.name}</h1>
                    </div>
                    {owns
                        ? <p className="ox-ohead__owns">{pod.name} owns: {owns}</p>
                        : (
                            <>
                                {pod.description && <p className="ox-ohead__owns">{pod.description}</p>}
                                {tab !== "home" && <p className="ox-ohead__nudge">{tab === "plan" ? NOTHING_OWNED : <a {...to(outcomeView(pod.id, "plan"), go)}>{NOTHING_OWNED}</a>}</p>}
                            </>
                        )}
                    <p className="ox-ohead__people">
                        <span className="ox-faces">{pod.members.slice(0, 6).map((member) => <span key={member.id} title={member.name}><Avatar name={member.name} size={22} /></span>)}</span>
                        <span>{pod.members.length} {pod.members.length === 1 ? "person" : "people"}</span>
                    </p>
                </div>
                <div className="ox-ohead__r">
                    {plan && value !== null
                        ? <div className="ox-stand"><span className="ox-big ox-num">{formatMeasure(value, plan.unit)}</span>{plan.target && <span className="ox-vs">target {plan.target}</span>}{standing && <span className={"ox-pill" + (standing === "behind" ? " ox-pill--behind" : "")}>{standing === "behind" ? "Behind" : "On track"}</span>}</div>
                        : measure.isError ? <span className="ox-vs">The measure didn’t run</span> : null}
                    <div className="ox-ohead__acts">
                        <details className="ox-more">
                            <summary aria-label={`More for ${pod.name}`} title="More"><Icon name="more" size={16} /></summary>
                            <div className="ox-more__menu" role="menu">
                                <button type="button" role="menuitem" onClick={(event) => { closeMenu(event); actions.addPeople(pod.id); }}>Who has access</button>
                                <button type="button" role="menuitem" onClick={(event) => { closeMenu(event); actions.connect(pod.id); }}>Where people reach it</button>
                                <button type="button" role="menuitem" onClick={(event) => { closeMenu(event); actions.share(pod.id); }}>Share or export</button>
                                <a role="menuitem" {...to(outcomeView(pod.id, "settings"), go)} onClick={(event) => { closeMenu(event); to(outcomeView(pod.id, "settings"), go).onClick(event); }}>Settings</a>
                                <a role="menuitem" href={classic.pod(pod.id)}>Open in the workspace</a>
                            </div>
                        </details>
                        {tab !== "home" && <a className="ox-btn ox-btn--primary" {...newChat}>New chat</a>}
                    </div>
                </div>
            </section>
            <nav className="ox-otabs" aria-label={pod.name}>
                {tabs.map((one) => (
                    <a key={one} className="ox-otab" aria-current={one === tab ? "page" : undefined} {...to(outcomeView(pod.id, one), go)}>{TAB_LABELS[one]}</a>
                ))}
            </nav>
            {tab === "home" && <HomeTab pod={pod} plan={plan} activity={activity} queue={queue} go={go} onAsk={onAsk} />}
            {tab === "plan" && <PlanTab pod={pod} me={me} go={go} onAsk={onAsk} />}
            {tab === "apps" && <AppsTab pod={pod} go={go} />}
            {tab === "made" && (
                <div className="ox-embed">
                    <Library podId={pod.id} onFile={(path) => go({ name: "file", podId: pod.id, path })} onTable={(name) => go({ name: "table", podId: pod.id, table: name })} />
                </div>
            )}
            {tab === "contacts" && (contacts
                ? <div className="ox-embed"><ContactsPage pod={pod} onConnect={() => actions.connect(pod.id)} /></div>
                : <Empty title="Contacts are for whoever looks after them."><p>Ask an admin of {pod.name} if you should be one.</p></Empty>)}
            {tab === "xray" && <XrayTab pod={pod} go={go} actions={actions} />}

        </div>
    );
}

/* ── home ─────────────────────────────────────────────────────────────── */

/** How many recent things a Lemma's home lists. */
const RECENT_SHOWN = 8;

/** A Lemma's home: what it owns, if nothing yet; a box to talk to it; what
 *  it needs from you; what it has been doing -- its chats, the automations
 *  that ran, the tables that changed -- and what it runs, with a way to ask
 *  for more. */
function HomeTab({ pod, plan, activity, queue, go, onAsk }: {
    pod: Pod;
    plan: Plan | null | undefined;
    activity: readonly ConversationRef[];
    queue: readonly QueueItem[];
    go: Go;
    onAsk: (podId: string, text: string) => void;
}) {
    const reader = useMe();
    const [now] = useState(() => Date.now());
    const [seed, setSeed] = useState<{ text: string; id: number } | null>(null);
    const seedWith = (text: string) => setSeed((was) => ({ text, id: (was?.id ?? 0) + 1 }));
    const channelList = useChannelList(pod.id, { archived: false });
    const tables = useQuery({ queryKey: ["outcome-tables", pod.id], queryFn: () => source.listLibrary(pod.id, "tables", "/"), staleTime: 5 * 60_000, retry: false });
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
    return (
        <div className="ox-lhome">
            {plan !== undefined && !plan?.outcome && (
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
            <AskBox pods={[pod]} podId={pod.id} fixed onAsk={onAsk} seed={seed} placeholder={`Ask ${pod.name} anything, or say what to do`} />
            {queue.length > 0 && (
                <section aria-label="Needs you">
                    <div className="ox-sec"><h2>Needs you</h2></div>
                    <div className="ox-queue">
                        {queue.map((item) => (
                            <a key={item.key} className="ox-item" {...to(viewOfItem(item), go)}>
                                <span className="ox-item__t">
                                    <span className="ox-item__w">{item.title}</span>
                                    {item.detail && <span className="ox-item__o">{item.detail}</span>}
                                </span>
                                <span className="ox-item__age">{ageOf(item.sinceMs, now)}</span>
                                <span className="ox-btn ox-btn--small">{item.kind === "decide" ? "Decide" : item.kind === "answer" ? "Answer" : "Open"}</span>
                            </a>
                        ))}
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
            <WhatItRuns pod={pod} go={go} onNew={seedWith} />
        </div>
    );
}

/* ── plan ─────────────────────────────────────────────────────────────── */

/** The plan: what it owns and how, line by line, with who wrote each. With
 *  none yet, a draft read off what it already runs -- to save as it is, or
 *  to have the Lemma write properly in a chat. */
function PlanTab({ pod, me, go, onAsk }: { pod: Pod; me: string; go: Go; onAsk: (podId: string, text: string) => void }) {
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

/* ── x-ray ────────────────────────────────────────────────────────────── */

function lastRan(at: string): string {
    const ms = at ? Date.parse(at) : Number.NaN;
    return Number.isNaN(ms) ? "" : ` · last ran ${ageOf(ms, Date.now())} ago`;
}

/** One thing a Lemma runs on. Opens in the frame when the frame shows that
 *  kind of thing, in the workspace when it does not. */
function Piece({ icon, name, line, link, tone }: { icon: IconName; name: string; line?: string; link: { href: string; onClick?: ReturnType<typeof to>["onClick"] }; tone?: "lemma" | "off" }) {
    return (
        <a className={"ox-piece" + (tone === "off" ? " ox-piece--off" : "")} {...link}>
            <span className={"ox-piece__i" + (tone === "lemma" ? " ox-piece__i--lemma" : "")}><Icon name={icon} /></span>
            <span className="ox-piece__t">{name}</span>
            {line && <span className="ox-piece__d">{line}</span>}
        </a>
    );
}

function XrayTab({ pod, go, actions }: { pod: Pod; go: Go; actions: FrameActions }) {
    const agents = useQuery({ queryKey: ["outcome-agents", pod.id], queryFn: () => source.listAgents(pod.id), staleTime: 5 * 60_000 });
    const schedules = useQuery({ queryKey: ["outcome-schedules", pod.id], queryFn: () => source.listSchedules(pod.id), staleTime: 5 * 60_000 });
    const tables = useQuery({ queryKey: ["outcome-tables", pod.id], queryFn: () => source.listLibrary(pod.id, "tables", "/"), staleTime: 5 * 60_000 });
    const tabs = useQuery({ queryKey: ["outcome-apps", pod.id], queryFn: () => source.listTabs(pod.id), staleTime: 5 * 60_000 });
    const skills = useQuery({ queryKey: ["outcome-skills", pod.id], queryFn: () => source.listLibrary(pod.id, "files", "/skills"), staleTime: 5 * 60_000, retry: false });
    const surfaces = useQuery({ queryKey: ["outcome-surfaces", pod.id], queryFn: () => source.listSurfaces(pod.id), staleTime: 5 * 60_000, retry: false });
    const workflows = useWorkflowList(pod.id);

    const apps = (tabs.data ?? []).flatMap((tab) => (tab.kind === "app" ? [tab] : []));
    const jobs = (schedules.data ?? []).filter((job) => !job.internal);
    const groups: { title: string; pending: boolean; failed: boolean; empty: string; pieces: ReactNode[] }[] = [
        {
            title: "Runs on its own",
            pending: schedules.isPending,
            failed: schedules.isError,
            empty: "Nothing scheduled.",
            pieces: jobs.map((job) => <Piece key={job.id} icon="clock" name={job.title} line={`${job.trigger}${job.active ? "" : " · paused"}${lastRan(job.lastFiredAt)}`} link={to(outcomeView(pod.id, "settings", "schedules"), go)} tone={job.active ? "lemma" : "off"} />),
        },
        {
            title: "Workflows",
            pending: workflows.isPending,
            failed: workflows.isError,
            empty: "No workflows yet.",
            pieces: (workflows.data ?? []).map((flow) => <Piece key={flow.id} icon="flow" name={readableName(flow.name)} line={[flow.description, `${flow.steps} ${flow.steps === 1 ? "step" : "steps"}`, flow.active ? "" : "off"].filter(Boolean).join(" · ")} link={to({ name: "workflow", podId: pod.id, workflow: flow.name }, go)} tone={flow.active ? "lemma" : "off"} />),
        },
        {
            title: "Agents",
            pending: agents.isPending,
            failed: agents.isError,
            empty: "Only its own agent.",
            pieces: (agents.data ?? []).map((agent) => <Piece key={agent.id} icon="agent" name={agent.front ? `${agent.label} (answers here)` : agent.label} line={agent.blurb} link={to({ name: "agent", podId: pod.id, agent: agent.name }, go)} tone="lemma" />),
        },
        {
            title: "Tables",
            pending: tables.isPending,
            failed: tables.isError,
            empty: "No tables yet.",
            pieces: (tables.data?.items ?? []).map((table) => <Piece key={table.id} icon="table" name={table.name} line={table.detail} link={to({ name: "table", podId: pod.id, table: table.name }, go)} />),
        },
        {
            title: "Apps",
            pending: tabs.isPending,
            failed: tabs.isError,
            empty: "No apps yet.",
            pieces: apps.map((app) => <Piece key={app.id} icon="app" name={app.label} line={app.visibility ? app.visibility.toLowerCase() : undefined} link={to({ name: "app", podId: pod.id, app: app.id.replace(/^app:/, "") }, go)} />),
        },
        {
            title: "Skills it follows",
            pending: skills.isPending,
            failed: skills.isError,
            empty: "No skills written here yet.",
            pieces: (skills.data?.items ?? []).map((item) => <Piece key={item.id} icon="skill" name={item.name.replace(/\.md$/i, "")} line={item.description} link={to({ name: "file", podId: pod.id, path: item.kind === "file" ? item.path : item.path.replace(/\/+$/, "") + "/SKILL.md" }, go)} />),
        },
        {
            title: "Where people reach it",
            pending: surfaces.isPending,
            failed: surfaces.isError,
            empty: "Only here.",
            pieces: (surfaces.data ?? []).map((surface) => <Piece key={surface.id} icon="plug" name={platformName(surface.platform)} line={[surface.handle, surface.active ? "" : "off"].filter(Boolean).join(" · ")} link={{ href: "#", onClick: (event) => { event.preventDefault(); actions.connect(pod.id); } }} tone={surface.active ? undefined : "off"} />),
        },
    ];
    return (
        <div>
            <p className="ox-xray__lead">Everything {pod.name} runs on. Each piece opens here.</p>
            <div className="ox-xray">
                {groups.map((group) => (
                    <section key={group.title} className="ox-xray__g">
                        <h2>{group.title}{!group.pending && !group.failed && group.pieces.length > 0 && <span className="ox-vs"> · {group.pieces.length}</span>}</h2>
                        {group.pending ? <p className="ox-loading">Loading…</p> : group.failed ? <p className="ox-vs">Couldn’t read this.</p> : group.pieces.length === 0 ? <p className="ox-vs">{group.empty}</p> : <div className="ox-pieces">{group.pieces}</div>}
                    </section>
                ))}
            </div>
        </div>
    );
}

