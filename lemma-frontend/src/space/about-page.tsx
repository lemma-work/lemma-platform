"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { capabilityList, grantedToolsets } from "@/stage/colleagues";
import { isForbidden } from "@/session/auth-state";
import { SkillsView } from "@/skills/skills-view";
import { StandingWork } from "@/schedule/standing-work";
import { Surfaces } from "@/shell/surfaces";
import { RunsOn } from "@/shell/runs-on";
import { WhoCanJoin } from "@/shell/who-can-join";
import { AtTheDoor } from "@/shell/at-the-door";
import { AddPeople } from "@/shell/add-people";
import { ChevronRightIcon, EditIcon, LockIcon, PlusIcon } from "@/ui/icons";
import { AgentMark } from "./agent-mark";
import { TeammateFace } from "./teammate-face";

/** Where a link into About lands. Each is a section of the one page. */
export type AboutSection = "people" | "channels" | "skills" | "schedules" | "agents" | "model";

const SECTIONS: readonly string[] = ["people", "channels", "skills", "schedules", "agents", "model"] satisfies AboutSection[];

/** Whether a word from an address is one of About's sections. */
export function isAboutSection(value: string | null | undefined): value is AboutSection {
    return typeof value === "string" && SECTIONS.includes(value);
}

/** The teammate itself, as a page: who it is and what it is for, then
 *  everything about how it works — who it works with, where it can be
 *  reached, what it has been taught, what it does without being asked, what
 *  it hands work to and what it thinks with.
 *
 *  These used to be settings of a place. They are facts about the teammate,
 *  and read that way: "what Kit has been taught", not "Skills". Each section
 *  is the same working component Settings stacked before — nothing here is a
 *  second copy of how a schedule or a channel works. What is left in
 *  Settings is about the space rather than the teammate. */
export function AboutPage({ pod, orgId, orgName, section, request = 0, onAsk, onOpenAgent, onAskFor, onOpenRun, onOpenConversation, onFile, onSettings }: {
    pod: Pod;
    orgId: string | null;
    orgName: string;
    /** The section a link asked for; the page scrolls it into view. */
    section: AboutSection | null;
    /** Bumped with every ask, so asking for the same section twice scrolls
     *  to it twice. */
    request?: number;
    /** Start a new conversation with this teammate. */
    onAsk: () => void;
    onOpenAgent: (name: string) => void;
    /** Put words in the composer for the teammate to act on. */
    onAskFor: (text: string) => void;
    onOpenRun: (runId: string, label: string) => void;
    onOpenConversation: (id: string) => void;
    onFile: (path: string) => void;
    onSettings: () => void;
}) {
    const page = useRef<HTMLDivElement>(null);
    useEffect(() => {
        if (!section) return;
        page.current?.querySelector(`[data-about="${section}"]`)?.scrollIntoView({ block: "start" });
    }, [section, request]);

    return (
        <div className="aboutpage" ref={page}>
            <div className="aboutpage__column">
                <Hero pod={pod} orgId={orgId} onAsk={onAsk} />

                <Section id="people" title="People" note={"Everyone here can open what is in " + pod.name + "’s space."}>
                    <AddPeople pod={pod} orgId={orgId} />
                    <WhoCanJoin podId={pod.id} orgName={orgName} />
                    <AtTheDoor podId={pod.id} teammate={pod.name} />
                </Section>

                <Section id="channels" title={"Where to reach " + pod.name} note="Channels outside the app it answers in.">
                    <Surfaces pod={pod} expanded />
                </Section>

                <Section id="skills" title={"What " + pod.name + " has been taught"} note="Instructions it follows when a task matches.">
                    <SkillsView
                        podId={pod.id}
                        teammate={pod.name}
                        onFile={onFile}
                        onCreate={() => onAskFor(
                            "Write me a new skill. Use the lemma-skill-creator skill: decide its triggers, "
                            + "write the instructions, and publish it under /skills. Ask me what it should do first."
                        )}
                    />
                </Section>

                <Section id="schedules" title="Standing work" note="What it does without being asked, on a time or an event.">
                    <StandingWork podId={pod.id} teammate={pod.name} members={pod.members} orgId={orgId} onOpenRun={onOpenRun} onOpenConversation={onOpenConversation} />
                </Section>

                <Section
                    id="agents"
                    title="Hands work to"
                    note={"Agents that take a piece of the job from " + pod.name + " or from a workflow."}
                    action={<button className="aboutpage__action" onClick={() => onAskFor(
                        "Set up a new agent I can hand work to. Ask me what it should be for, what it may use, and who should be able to talk to it."
                    )}><PlusIcon size={14} /> New agent</button>}
                >
                    <AgentList pod={pod} onOpen={onOpenAgent} />
                </Section>

                <Section id="model" title="Runs on" note={"What " + pod.name + " thinks with, unless an agent names its own."}>
                    <RunsOn podId={pod.id} orgId={pod.orgId} />
                </Section>

                <p className="aboutpage__foot">
                    AI tools, the models {orgName} can use, and usage are in{" "}
                    <button className="linkish" onClick={onSettings}>Settings</button>.
                </p>
            </div>
        </div>
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
                    <div className="aboutpage__acts">
                        <button className="pill-button" onClick={onAsk}>Ask {pod.name}</button>
                        <button className="ghost-pill" onClick={() => setEditing(true)}><EditIcon size={14} /> Edit</button>
                    </div>
                </div>
            )}
        </header>
    );
}

function Section({ id, title, note, action, children }: { id: string; title: string; note?: string; action?: ReactNode; children: ReactNode }) {
    return (
        <section className="aboutpage__section" data-about={id} aria-label={title}>
            <header>
                <div className="aboutpage__heading">
                    <h2>{title}</h2>
                    {action}
                </div>
                {note && <p>{note}</p>}
            </header>
            <div className="aboutpage__content">{children}</div>
        </section>
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
