"use client";

import type { ReactNode } from "react";
import type { Pod } from "@/data";
import { AgentAccess } from "./agent-access";
import { ModelsSection } from "@/org/models";
import { UsagePanel } from "@/usage/usage-panel";
import { OrgUsageSection } from "@/org/org-usage";

export type SettingsSection = "agents" | "model" | "usage";

const SECTIONS: { id: SettingsSection; label: string }[] = [
    { id: "agents", label: "AI tools" },
    { id: "model", label: "Models" },
    { id: "usage", label: "Usage" },
];

/** What is left to set once the teammate has its own page.
 *
 *  Who it works with, where it answers, what it has been taught, its standing
 *  work, its agents and its model are facts about the teammate, and live on
 *  About. These are about the space and the organization around it: reaching
 *  the space from an AI tool, the models every teammate here can use, and
 *  what has been spent. One section at a time, the way ChatGPT's settings are. */
export function SettingsPage({ pod, orgId, orgName, section, onSection, onAbout }: {
    pod: Pod;
    orgId: string | null;
    orgName: string;
    section: SettingsSection;
    onSection: (section: SettingsSection) => void;
    /** The teammate's own page, where everything else about it is. */
    onAbout: () => void;
}) {
    return (
        <div className="settings">
            <nav className="settings__nav" aria-label="Settings">
                <h1>Settings</h1>
                {SECTIONS.map(item => (
                    <button key={item.id} aria-current={section === item.id ? "page" : undefined} onClick={() => onSection(item.id)}>
                        {item.label}
                    </button>
                ))}
                <p className="settings__elsewhere">
                    How {pod.name} works — people, channels, skills, schedules, agents, model — is on{" "}
                    <button className="linkish" onClick={onAbout}>About {pod.name}</button>.
                </p>
            </nav>
            <div className="settings__body">
                {section === "agents" && (
                    <Section title="AI tools" note={"Use " + pod.name + " from Claude, ChatGPT, Claude Code and other AI tools."}>
                        <AgentAccess pod={pod} />
                    </Section>
                )}
                {section === "model" && (
                    orgId ? (
                        /* The catalog is the organization's: a provider key is
                           bought and billed once, for every teammate. What this
                           one runs on is on its About page. */
                        <Section title={"Available in " + orgName} note="Providers, keys and computers every teammate here can use.">
                            <ModelsSection orgId={orgId} />
                        </Section>
                    ) : <Section title="Models"><p className="settings__quiet">No organization to read models from.</p></Section>
                )}
                {section === "usage" && (
                    <>
                        <Section title="Your usage" note="What your conversations and runs have used.">
                            <UsagePanel orgId={orgId} />
                        </Section>
                        {orgId && (
                            <Section title={orgName + " usage"} note="Across every teammate and person in the organization.">
                                <OrgUsageSection orgId={orgId} />
                            </Section>
                        )}
                    </>
                )}
            </div>
        </div>
    );
}

function Section({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
    return (
        <section className="settings__section">
            <header>
                <div className="settings__heading">
                    <h2>{title}</h2>
                </div>
                {note && <p>{note}</p>}
            </header>
            <div className="settings__content">{children}</div>
        </section>
    );
}
