"use client";

import { useEffect, useRef, useState, type ComponentType, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { capabilityList, grantedToolsets } from "@/stage/colleagues";
import { isForbidden } from "@/session/auth-state";
import { SkillsView } from "@/skills/skills-view";
import { StandingWork } from "@/schedule/standing-work";
import { ModelChoice } from "@/shell/runs-on";
import { ReachPanel } from "@/shell/reach";
import { PodConnections } from "@/connect/pod-connections";
import { useSearchParams } from "next/navigation";
import { WhoCanJoin } from "@/shell/who-can-join";
import { AtTheDoor } from "@/shell/at-the-door";
import { AddPeople } from "@/shell/add-people";
import type { SettingsSection } from "@/settings/settings-modal";
import {
    AgentIcon, AskIcon, ChevronLeftIcon, ChevronRightIcon, ClockIcon, ConnectorIcon, EditIcon, LockIcon, MemoryIcon, ModelIcon,
    PeopleIcon, PlugIcon, PlusIcon, ReachIcon, TaughtIcon,
} from "@/ui/icons";
import { AgentAccess } from "./agent-access";
import { AgentMark } from "./agent-mark";
import { WhatItRemembers } from "./what-it-remembers";
import { sayHired } from "./teammates";
import { TeammateFace } from "./teammate-face";
import { AskedBy, ConnectTeammate, useMayConnect } from "./connect-teammate";
import { DeleteTeammate } from "./delete-teammate";
import { useAboutLines } from "./about-lines";
import type { AboutSection } from "./about-sections";

export { isAboutSection, type AboutSection } from "./about-sections";

/** The parts of About, in the order the overview lays them out: five rows
 *  of two — what it thinks with, what it knows and hands on, what it can
 *  reach and does unasked, where it is reached, and who it works with.
 *  Named with plain nouns, the words people already use for
 *  these things elsewhere: "Agents", "Default model", "Skills". `label` is
 *  the tile's word, the breadcrumb's, and the part's own heading unless
 *  `title` says more. `blurb` stands in for the tile's live line when that
 *  line cannot be read. */
interface Part {
    id: AboutSection;
    label: string;
    icon: ComponentType<{ size?: number }>;
    title?: (name: string) => string;
    note: (name: string) => string;
    blurb: string;
}

const PARTS: readonly Part[] = [
    { id: "model", label: "Default model", icon: ModelIcon,
        note: (name) => "What " + name + " thinks with, unless an agent names its own.", blurb: "The model it thinks with" },
    { id: "skills", label: "Skills", icon: TaughtIcon,
        note: (name) => "Instructions " + name + " follows when a task matches. People write these.", blurb: "Instructions people wrote" },
    { id: "memory", label: "Memory", icon: MemoryIcon,
        note: (name) => "Notes " + name + " writes as it works. Open one to read or fix it.", blurb: "Notes it wrote itself" },
    { id: "agents", label: "Agents", icon: AgentIcon,
        note: (name) => "Agents that take a piece of the job from " + name + " or from a workflow.", blurb: "Agents it hands work to" },
    { id: "connections", label: "Connections", icon: ConnectorIcon,
        note: (name) => "The apps " + name + " works through — Gmail, Slack, GitHub and the rest — and whose account each one uses: each person’s own, or one somebody shared here.",
        blurb: "What it works through" },
    { id: "schedules", label: "Standing work", icon: ClockIcon,
        note: () => "What it does without being asked, on a time or an event.", blurb: "What it does unasked" },
    { id: "channels", label: "Channels", icon: ReachIcon, title: (name) => "Where to reach " + name,
        note: (name) => "Pick a channel and " + name + " answers there, under its own name.", blurb: "Where it answers" },
    { id: "ai-tools", label: "MCP", icon: PlugIcon, title: () => "Connect ChatGPT, Claude and others",
        note: (name) => "Add " + name + " to any AI app that speaks MCP, and use it from there.", blurb: "Use it from other AI apps" },
    { id: "people", label: "People", icon: PeopleIcon,
        note: (name) => "Everyone here can open what is in " + name + "’s space.", blurb: "Who can open its space" },
    { id: "works-with", label: "Teammates", icon: AskIcon, title: (name) => "Teammates " + name + " works with",
        note: (name) => "As you: " + name + " asks with your access there, and checks with you first. Allowed: that teammate lets " + name + " ask it with nobody present, reading only what it shared.",
        blurb: "Other teammates it can ask" },
];

/** A part's heading on its own page. */
function titleOf(part: Part, name: string): string {
    return part.title ? part.title(name) : part.label;
}

/** A part's short name, for the breadcrumb above its page. */
export function aboutLabel(section: AboutSection): string {
    return PARTS.find((part) => part.id === section)?.label ?? "About";
}

/** The teammate itself, as a page: who it is and what it is for, then a
 *  tile for each part of how it works, each saying what is true there now.
 *
 *  This was one page that stacked every part — thirteen of them, eight
 *  screens tall, with a row of jumps that ran off its own edge — and four of
 *  those were the organization's (connectors, models, two kinds of usage),
 *  copies of what Settings already holds. Now the overview fits on a screen
 *  and a part opens as a page of its own at `/t/{pod}/about/{part}`, so Back
 *  steps out of it and a link can name it. Each part is still the same
 *  working component it always was; nothing here is a second copy of how a
 *  schedule or a channel works. */
export function AboutPage({ pod, orgId, orgName, section, onSection, onOpenSettings, onAsk, onOpenAgent, onOpenTeammate, onAskFor, onOpenRun, onOpenConversation, onFile, onDeleted }: {
    pod: Pod;
    orgId: string | null;
    orgName: string;
    /** The part that is open, or null for the overview. */
    section: AboutSection | null;
    /** Open a part, or go back to the overview. */
    onSection: (section: AboutSection | null) => void;
    /** The organization's settings, where connectors, models and usage live. */
    onOpenSettings: (section: SettingsSection) => void;
    /** Start a new conversation with this teammate. */
    onAsk: () => void;
    onOpenAgent: (name: string) => void;
    /** Go to another teammate. */
    onOpenTeammate: (podId: string) => void;
    /** Put words in the composer for the teammate to act on. */
    onAskFor: (text: string) => void;
    onOpenRun: (runId: string, label: string) => void;
    onOpenConversation: (id: string) => void;
    onFile: (path: string) => void;
    /** The teammate has just been deleted: leave its space. */
    onDeleted: () => void;
}) {
    const page = useRef<HTMLDivElement>(null);
    /* A part opens at its top, and the overview where it starts. */
    useEffect(() => { page.current?.scrollTo({ top: 0 }); }, [section]);
    const part = section ? PARTS.find((one) => one.id === section) : undefined;

    return (
        <div className="aboutpage" ref={page}>
            <div className="aboutpage__column">
                {part ? (
                    <PartPage
                        pod={pod}
                        part={part}
                        orgId={orgId}
                        orgName={orgName}
                        onBack={() => onSection(null)}
                        onOpenAgent={onOpenAgent}
                        onOpenTeammate={onOpenTeammate}
                        onAskFor={onAskFor}
                        onOpenRun={onOpenRun}
                        onOpenConversation={onOpenConversation}
                        onFile={onFile}
                    />
                ) : <>
                    <Hero pod={pod} orgId={orgId} onAsk={onAsk} />
                    <Parts pod={pod} onOpen={onSection} />
                    {/* What the organization shares is in Settings, once, for
                        every teammate. Said here because it used to be here,
                        and somebody will come looking. */}
                    <p className="aboutpage__elsewhere">
                        <span>Shared by everyone in {orgName}, in Settings:</span>
                        <button className="linkish" onClick={() => onOpenSettings("models")}>Models</button>
                        <button className="linkish" onClick={() => onOpenSettings("usage")}>Usage</button>
                    </p>
                    {/* Last, and after everything that says how it works: the
                        one act on this page that cannot be taken back. Not a
                        tile — nobody should arrive here by a shortcut. */}
                    <DeleteTeammate pod={pod} orgId={orgId} onDeleted={onDeleted} />
                </>}
            </div>
        </div>
    );
}

/** The overview's grid: a tile a part, its mark, its name and one live line. */
function Parts({ pod, onOpen }: { pod: Pod; onOpen: (section: AboutSection) => void }) {
    const lines = useAboutLines(pod);
    return (
        <nav className="aboutparts" aria-label={"About " + pod.name}>
            {PARTS.map((part) => {
                const line = lines[part.id];
                return (
                    <button key={part.id} className="aboutparts__tile" onClick={() => onOpen(part.id)}>
                        <span className="aboutparts__mark"><part.icon size={20} /></span>
                        <span className="aboutparts__label">{part.label}</span>
                        <span className="aboutparts__line">
                            {line.needs && <span className="aboutparts__dot" role="img" aria-label="Needs you" />}
                            <span>{line.text ?? part.blurb}</span>
                        </span>
                        <ChevronRightIcon size={16} className="aboutparts__go" aria-hidden="true" />
                    </button>
                );
            })}
        </nav>
    );
}

/** One part on its own page: the way back, its heading and note, and the
 *  same component the part has always been drawn with. */
function PartPage({ pod, part, orgId, orgName, onBack, onOpenAgent, onOpenTeammate, onAskFor, onOpenRun, onOpenConversation, onFile }: {
    pod: Pod;
    part: Part;
    orgId: string | null;
    orgName: string;
    onBack: () => void;
    onOpenAgent: (name: string) => void;
    onOpenTeammate: (podId: string) => void;
    onAskFor: (text: string) => void;
    onOpenRun: (runId: string, label: string) => void;
    onOpenConversation: (id: string) => void;
    onFile: (path: string) => void;
}) {
    /* Open on nobody for a new connection; on one teammate to change what it
       reads. */
    const [connecting, setConnecting] = useState<{ asker?: string } | null>(null);
    const manageLinks = useMayConnect(pod.id);
    /* The connector a link named — every typed refusal's `connect_url`. */
    const focus = useSearchParams().get("connector");

    let action: ReactNode = null;
    let body: ReactNode;
    switch (part.id) {
        case "people":
            body = <>
                <AddPeople pod={pod} orgId={orgId} />
                <WhoCanJoin podId={pod.id} orgName={orgName} />
                <AtTheDoor podId={pod.id} teammate={pod.name} />
            </>;
            break;
        case "channels":
            body = <ReachPanel pod={pod} inline />;
            break;
        case "connections":
            body = <PodConnections pod={pod} focus={focus} />;
            break;
        case "works-with":
            action = manageLinks ? (
                <button className="aboutpage__action aboutpage__action--long" onClick={() => setConnecting({})}>
                    <PlusIcon size={14} /> Let another teammate ask {pod.name}
                </button>
            ) : null;
            body = <>
                <WorksWith pod={pod} onOpen={onOpenTeammate} />
                <AskedBy pod={pod} onOpen={onOpenTeammate} onEdit={(asker) => setConnecting({ asker })} />
                {connecting && <ConnectTeammate pod={pod} asker={connecting.asker} onClose={() => setConnecting(null)} />}
            </>;
            break;
        case "skills":
            body = (
                <SkillsView
                    podId={pod.id}
                    teammate={pod.name}
                    layout="grid"
                    onFile={onFile}
                    onCreate={() => onAskFor(
                        "Write me a new skill. Use the lemma-skill-creator skill: decide its triggers, "
                        + "write the instructions, and publish it under /skills. Ask me what it should do first."
                    )}
                />
            );
            break;
        case "memory":
            body = <WhatItRemembers pod={pod} onFile={onFile} />;
            break;
        case "schedules":
            body = <StandingWork podId={pod.id} teammate={pod.name} members={pod.members} onOpenRun={onOpenRun} onOpenConversation={onOpenConversation} />;
            break;
        case "agents":
            action = (
                <button className="aboutpage__action" onClick={() => onAskFor(
                    "Set up a new agent I can hand work to. Ask me what it should be for, what it may use, and who should be able to talk to it."
                )}><PlusIcon size={14} /> New agent</button>
            );
            body = <AgentList pod={pod} onOpen={onOpenAgent} />;
            break;
        case "model":
            body = <ModelChoice podId={pod.id} orgId={pod.orgId} orgName={orgName} />;
            break;
        case "ai-tools":
            body = <AgentAccess pod={pod} />;
            break;
    }

    return (
        <section className="aboutpart" aria-label={titleOf(part, pod.name)}>
            <button className="aboutpart__back" onClick={onBack}>
                <ChevronLeftIcon size={15} />
                <TeammateFace pod={pod} size={20} />
                <span>{pod.name}</span>
            </button>
            <header className="aboutpart__head">
                <div className="aboutpage__heading">
                    <h1>{titleOf(part, pod.name)}</h1>
                    {action}
                </div>
                <p>{part.note(pod.name)}</p>
            </header>
            <div className="aboutpage__content">{body}</div>
        </section>
    );
}

/** The face, the name and the job — and the one place to change the last two. */
function Hero({ pod, orgId, onAsk }: { pod: Pod; orgId: string | null; onAsk: () => void }) {
    const cache = useQueryClient();
    const [editing, setEditing] = useState(false);
    const [name, setName] = useState(pod.name);
    const [job, setJob] = useState(pod.description ?? "");
    useEffect(() => { if (!editing) { setName(pod.name); setJob(pod.description ?? ""); } }, [editing, pod.name, pod.description]);

    const save = useMutation({
        mutationFn: async () => {
            const nextName = name.trim();
            if (nextName && nextName !== pod.name) await source.renamePod(pod.id, nextName);
            if (job.trim() !== (pod.description ?? "").trim()) await source.describePod(pod.id, job);
        },
        onSuccess: () => {
            void cache.invalidateQueries({ queryKey: ["pods", orgId] });
            void cache.invalidateQueries({ queryKey: ["pod-access", pod.id] });
            void cache.invalidateQueries({ queryKey: ["pod-detail", pod.id] });
            setEditing(false);
        },
    });

    const hired = sayHired(pod);

    return (
        <header className="aboutpage__hero">
            <TeammateFace pod={pod} size={132} live className="aboutpage__face" />
            {editing ? (
                <form className="aboutpage__edit" onSubmit={(event) => { event.preventDefault(); if (name.trim()) save.mutate(); }}>
                    <label>
                        <span>Name</span>
                        <input value={name} onChange={(event) => setName(event.target.value)} autoFocus maxLength={255} />
                    </label>
                    <label>
                        <span>What it is for</span>
                        <textarea value={job} onChange={(event) => setJob(event.target.value)} rows={3} placeholder="Watches the inbox and answers what it can" />
                    </label>
                    {save.isError && (
                        <p className="aboutpage__error" role="alert">
                            {isForbidden(save.error) ? "You may not change " + pod.name + "." : "That could not be saved. Try again."}
                        </p>
                    )}
                    <div className="aboutpage__acts">
                        <button className="pill-button" type="submit" disabled={!name.trim() || save.isPending}>{save.isPending ? "Saving…" : "Save"}</button>
                        <button className="ghost-pill" type="button" onClick={() => { setEditing(false); save.reset(); }}>Cancel</button>
                    </div>
                </form>
            ) : (
                <div className="aboutpage__who">
                    <h1>{pod.name}</h1>
                    {pod.description
                        ? <p className="aboutpage__job">{pod.description}</p>
                        : <p className="aboutpage__job aboutpage__job--none">No job written down yet.</p>}
                    {hired && <p className="aboutpage__hired">{hired}</p>}
                    <div className="aboutpage__acts">
                        <button className="pill-button" onClick={onAsk}>Ask {pod.name}</button>
                        <button className="ghost-pill" onClick={() => setEditing(true)}><EditIcon size={14} /> Edit</button>
                    </div>
                </div>
            )}
        </header>
    );
}

/** The agents this teammate hands work to. Not the teammate's own agent,
 *  which is the teammate and has this whole page. A row opens the agent's
 *  own page; nothing about an agent is configured inside this list. */
function AgentList({ pod, onOpen }: { pod: Pod; onOpen: (name: string) => void }) {
    const agents = useQuery({
        queryKey: ["agents", pod.id],
        queryFn: () => source.listAgents(pod.id),
        staleTime: 60_000,
    });
    if (agents.isPending) return <p className="aboutpage__quiet">Loading…</p>;
    if (agents.isError) {
        return (
            <p className="aboutpage__quiet" role="alert">
                {isForbidden(agents.error) ? "You may not list the agents here." : "Couldn’t load the agents."}{" "}
                <button className="linkish" onClick={() => void agents.refetch()}>Try again</button>
            </p>
        );
    }
    const rows = agents.data.filter((row) => !row.front);
    if (rows.length === 0) return <p className="aboutpage__quiet">Nobody yet. {pod.name} does the whole job itself.</p>;
    return (
        <ul className="agentlist">
            {rows.map((row, at) => {
                const can = capabilityList(grantedToolsets(row.toolsets, row.front)).map((one) => one.word);
                return (
                    <li key={row.name || "unnamed-" + at}>
                        <button className="agentlist__row" disabled={row.broken} onClick={() => onOpen(row.name)}>
                            <AgentMark pod={pod} agent={row} size={32} />
                            <span className="agentlist__body">
                                <span className="agentlist__title">
                                    <b>{row.label}</b>
                                    {row.visibility === "RESTRICTED" && <em><LockIcon size={11} /> Restricted</em>}
                                    {row.takesInput && <em>Workflow step</em>}
                                </span>
                                <small>{row.blurb || "No description written."}</small>
                            </span>
                            <span className="agentlist__can">{can.slice(0, 3).join(" · ")}{can.length > 3 && " +" + (can.length - 3)}</span>
                            <ChevronRightIcon size={16} />
                        </button>
                    </li>
                );
            })}
        </ul>
    );
}

/** The other teammates this one can ask: through you, or connected.
 *
 *  Partly yours: through you means you are in both, so someone else opening
 *  this page sees their own. The same list the teammate reads before it asks,
 *  so nothing here is something it would be refused. */
function WorksWith({ pod, onOpen }: { pod: Pod; onOpen: (podId: string) => void }) {
    const askable = useQuery({
        queryKey: ["askable-pods", pod.id],
        queryFn: () => source.askablePods(pod.id),
        staleTime: 60_000,
    });
    if (askable.isPending) return <p className="aboutpage__quiet">Loading…</p>;
    if (askable.isError) {
        return (
            <p className="aboutpage__quiet" role="alert">
                Couldn’t load who {pod.name} can ask.{" "}
                <button className="linkish" onClick={() => void askable.refetch()}>Try again</button>
            </p>
        );
    }
    if (askable.data.length === 0) {
        return <p className="aboutpage__quiet">Nobody yet. {pod.name} can ask any other teammate you’re in, or one that lets it.</p>;
    }
    return (
        <ul className="agentlist">
            {askable.data.map((other) => (
                <li key={other.id}>
                    <button className="agentlist__row" onClick={() => onOpen(other.id)}>
                        <TeammateFace pod={other} size={32} />
                        <span className="agentlist__body">
                            <span className="agentlist__title"><b>{other.name}</b></span>
                            <small>{other.description || "No job written down yet."}</small>
                        </span>
                        <span className="agentlist__can">{[other.throughYou && "As you", other.connected && "Allowed by " + other.name].filter(Boolean).join(" · ")}</span>
                        <ChevronRightIcon size={16} />
                    </button>
                </li>
            ))}
        </ul>
    );
}
