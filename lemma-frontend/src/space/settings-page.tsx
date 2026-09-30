"use client";

import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { useQuery } from "@tanstack/react-query";
import { Mark } from "@/shell/mark";
import { capabilityList, grantedToolsets } from "@/stage/colleagues";
import { isForbidden } from "@/session/auth-state";
import { ChevronRightIcon, LockIcon, PlusIcon } from "@/ui/icons";
import { SkillsView } from "@/skills/skills-view";
import { StandingWork } from "@/schedule/standing-work";
import { Surfaces } from "@/shell/surfaces";
import { RunsOn } from "@/shell/runs-on";
import { WhoCanJoin } from "@/shell/who-can-join";
import { AtTheDoor } from "@/shell/at-the-door";
import { AddPeople } from "@/shell/add-people";
import { SpaceTile } from "./space-switcher";
import { AgentAccess } from "./agent-access";
import { ModelsSection } from "@/org/models";
import { UsagePanel } from "@/usage/usage-panel";
import { OrgUsageSection } from "@/org/org-usage";

export type SettingsSection = "general" | "people" | "bots" | "channels" | "agents" | "schedules" | "skills" | "model" | "usage";

const SECTIONS: { id: SettingsSection; label: string }[] = [
    { id: "general", label: "General" },
    { id: "people", label: "People" },
    { id: "bots", label: "Bots" },
    { id: "channels", label: "Channels" },
    { id: "agents", label: "Coding agents" },
    { id: "schedules", label: "Schedules" },
    { id: "skills", label: "Skills" },
    { id: "model", label: "Models" },
    { id: "usage", label: "Usage" },
];

/** A space's settings, one section at a time, the way ChatGPT's settings
 *  are: a short list on the left, the section on the right. Each section is
 *  the same working component the old profile page stacked into one long
 *  scroll — nothing here is a second copy of how a bot or a schedule works. */
export function SettingsPage({ pod, orgId, orgName, section, onSection, onOpenBot, onFile, onAskFor, onOpenRun, onOpenConversation }: {
    pod: Pod;
    orgId: string | null;
    orgName: string;
    section: SettingsSection;
    onSection: (section: SettingsSection) => void;
    onOpenBot: (name: string) => void;
    onFile?: (path: string) => void;
    onAskFor?: (text: string) => void;
    onOpenRun?: (runId: string, label: string) => void;
    onOpenConversation?: (id: string) => void;
}) {
    const who = pod.teammate?.name ?? pod.name;
    return (
        <div className="settings">
            <nav className="settings__nav" aria-label="Settings">
                <h1>Settings</h1>
                {SECTIONS.map(item => (
                    <button key={item.id} aria-current={section === item.id ? "page" : undefined} onClick={() => onSection(item.id)}>
                        {item.label}
                    </button>
                ))}
            </nav>
            <div className="settings__body">
                {section === "general" && <General pod={pod} />}
                {section === "people" && (
                    <Section title="People" note={"Everyone here can open what is in " + pod.name + "."}>
                        <AddPeople pod={pod} orgId={orgId} />
                        <WhoCanJoin podId={pod.id} orgName={orgName} />
                        <AtTheDoor podId={pod.id} teammate={who} />
                    </Section>
                )}
                {section === "bots" && (
                    <Section title="Bots" note="The main bot answers most things. The others are specialists it — or a workflow — hands work to."
                        action={onAskFor && (
                            <button className="settings__action" onClick={() => onAskFor(
                                "Set up a new bot in this space. Ask me what it should be for, what it may use, and who should be able to talk to it."
                            )}><PlusIcon size={14} /> New bot</button>
                        )}>
                        <BotList pod={pod} onOpen={onOpenBot} />
                    </Section>
                )}
                {section === "channels" && (
                    <Section title="Channels" note="Where people can reach this space outside the app.">
                        <Surfaces pod={pod} expanded />
                    </Section>
                )}
                {section === "agents" && (
                    <Section title="Coding agents" note={"Use " + pod.name + " from Claude Code, Codex, Cursor or OpenCode — its pages, tables, workflows and bots — or connect Claude and ChatGPT by URL."}>
                        <AgentAccess pod={pod} />
                    </Section>
                )}
                {section === "schedules" && (
                    <Section title="Schedules" note="Work that starts on its own, on a time or an event.">
                        <StandingWork podId={pod.id} teammate={who} members={pod.members} orgId={orgId} onOpenRun={onOpenRun} onOpenConversation={onOpenConversation} />
                    </Section>
                )}
                {section === "skills" && (
                    <Section title="Skills" note="Instructions the bots here follow when a task matches.">
                        <SkillsView
                            podId={pod.id}
                            teammate={who}
                            onFile={onFile}
                            onCreate={onAskFor && (() => onAskFor(
                                "Write me a new skill. Use the lemma-skill-creator skill: decide its triggers, "
                                + "write the instructions, and publish it under /skills. Ask me what it should do first."
                            ))}
                        />
                    </Section>
                )}
                {section === "model" && (
                    <>
                        <Section title="Models" note={"What " + pod.name + " thinks with, unless a bot names its own."}>
                            <RunsOn podId={pod.id} orgId={pod.orgId} />
                        </Section>
                        {/* The catalog is the organization's: a provider key is
                            bought and billed once, for every space. Shown here
                            because this is where somebody goes looking for it. */}
                        {orgId && (
                            <Section title={"Available in " + orgName} note="Providers, keys and computers every space here can use.">
                                <ModelsSection orgId={orgId} />
                            </Section>
                        )}
                    </>
                )}
                {section === "usage" && (
                    <>
                        <Section title="Your usage" note="What your conversations and runs have used.">
                            <UsagePanel orgId={orgId} />
                        </Section>
                        {orgId && (
                            <Section title={orgName + " usage"} note="Across every space and person in the organization.">
                                <OrgUsageSection orgId={orgId} />
                            </Section>
                        )}
                    </>
                )}
            </div>
        </div>
    );
}

function Section({ title, note, action, children }: { title: string; note?: string; action?: React.ReactNode; children: React.ReactNode }) {
    return (
        <section className="settings__section">
            <header>
                <div className="settings__heading">
                    <h2>{title}</h2>
                    {action}
                </div>
                {note && <p>{note}</p>}
            </header>
            <div className="settings__content">{children}</div>
        </section>
    );
}

/** Every bot in the space, the main one first. A row opens the bot's own
 *  page; nothing about a bot is configured inside this list. */
function BotList({ pod, onOpen }: { pod: Pod; onOpen: (name: string) => void }) {
    const bots = useQuery({
        queryKey: ["agents", pod.id],
        queryFn: () => source.listAgents(pod.id),
        staleTime: 60_000,
    });
    const rows = [...(bots.data ?? [])].sort((a, b) => Number(b.front) - Number(a.front));
    if (bots.isPending) return <p className="settings__quiet">Loading bots…</p>;
    if (bots.isError) {
        return (
            <p className="settings__quiet" role="alert">
                {isForbidden(bots.error) ? "You may not list the bots here." : "Couldn’t load bots."}{" "}
                <button className="linkish" onClick={() => void bots.refetch()}>Try again</button>
            </p>
        );
    }
    return (
        <ul className="botlist">
            {rows.map((row, at) => {
                const can = capabilityList(grantedToolsets(row.toolsets, row.front)).map((one) => one.word);
                return (
                    <li key={row.name || "unnamed-" + at}>
                        <button className="botlist__row" disabled={row.broken} onClick={() => onOpen(row.name)}>
                            <Mark seed={pod.id + ":" + row.name} name={row.label} icon={row.iconUrl} size={36} />
                            <span className="botlist__body">
                                <span className="botlist__title">
                                    <b>{row.label}</b>
                                    {row.front && <em>Main</em>}
                                    {row.visibility === "RESTRICTED" && <em><LockIcon size={11} /> Restricted</em>}
                                    {row.takesInput && <em>Workflow step</em>}
                                </span>
                                <small>{row.front ? "Answers in " + pod.name + " and everywhere it is reached." : row.blurb || "No description written."}</small>
                            </span>
                            <span className="botlist__can">{can.slice(0, 3).join(" · ")}{can.length > 3 && " +" + (can.length - 3)}</span>
                            <ChevronRightIcon size={16} />
                        </button>
                    </li>
                );
            })}
        </ul>
    );
}

function General({ pod }: { pod: Pod }) {
    const cache = useQueryClient();
    const [name, setName] = useState(pod.name);
    useEffect(() => setName(pod.name), [pod.name]);
    const rename = useMutation({
        mutationFn: (next: string) => source.renamePod(pod.id, next),
        onSuccess: () => { void cache.invalidateQueries({ queryKey: ["pods", pod.orgId] }); },
    });
    const changed = name.trim() !== "" && name.trim() !== pod.name;
    return (
        <Section title="General">
            <div className="settings__row">
                <SpaceTile space={pod} size={44} />
                <label className="settings__field">
                    <span>Name</span>
                    <input value={name} onChange={event => setName(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && changed) rename.mutate(name.trim()); }} />
                </label>
                <button className="settings__save" disabled={!changed || rename.isPending} onClick={() => rename.mutate(name.trim())}>
                    {rename.isPending ? "Saving…" : "Save"}
                </button>
            </div>
            {rename.isError && <p className="settings__error" role="alert">That name could not be saved.</p>}
        </Section>
    );
}
