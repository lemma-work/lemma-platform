import { isPodDefaultAgent } from "@web/data/agent-names";
import { capabilityFor, grantedToolsets } from "@web/stage/colleagues";
import { itemsOf } from "@web/search/sources";
import { readSchedules } from "@web/schedule/schedules";

/** What the voice is told about the Lemma it speaks for.
 *
 *  `web/pod-brief.ts` (the web's, unchanged) writes it from a `Profile`, the
 *  shape the web's `source.getProfile` reads for a Lemma's profile tab. The
 *  phone has no profile tab, so the same reads are made here and folded into
 *  that shape by `profileFrom`. The types are the web's
 *  (`lemma-frontend/src/data/types.ts`).
 *
 *  Left out, because `podBrief` never reads them: `permits` (the default
 *  agent's allowed actions), each standing job's last firing, `joined` and
 *  the icon. They stay empty here rather than read for nothing. */

export interface Skill {
    id: string;
    label: string;
    blurb: string;
}

export interface Commitment {
    id: string;
    title: string;
    detail: string;
    /** "Every weekday at 09:00", "When mail arrives": said in words. */
    cadence: string;
    since: string;
    active: boolean;
    last: string;
}

export interface Project {
    id: string;
    name: string;
    description: string;
    status: string;
    tabId: string;
}

export interface Profile {
    podId: string;
    name: string;
    iconUrl: string | null;
    headline: string;
    joined: string;
    about: string;
    skills: Skill[];
    permits: string[];
    commitments: Commitment[];
    projects: Project[];
    counts: { tables: number; functions: number; workflows: number };
    unavailable?: Array<"tools" | "schedules" | "apps" | "tables" | "functions" | "workflows">;
}

/** The reads the web's `getProfile` makes, as they came back; `null` for one
 *  that failed. */
export interface ProfileReads {
    podId: string;
    pod: { name?: string | null; description?: string | null } | null;
    agents: unknown;
    /** The default agent's own record, when there is one and it could be read. */
    defaultAgent: { instruction?: string | null; toolsets?: unknown } | null;
    schedules: unknown;
    apps: unknown;
    tables: unknown;
    functions: unknown;
    workflows: unknown;
}

/** The web's `humanizeName` from `data/live.ts`: a filename said as a title. */
export function humanizeName(raw: string): string {
    const words = raw.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/[-_]+/g, " ").replace(/\s+/g, " ").trim();
    return words ? words[0].toUpperCase() + words.slice(1) : raw;
}

/** The agent that answers in a Lemma, by name, from the agents list. Never
 *  `agents[0]`, which names a subagent as often as not. */
export function defaultAgentOf(agents: unknown): string | null {
    const found = itemsOf(agents)
        .map((raw) => raw as { name?: unknown; kind?: unknown })
        .find((agent) => typeof agent.name === "string" && agent.name && isPodDefaultAgent(agent.name, typeof agent.kind === "string" ? agent.kind : null));
    return found ? String(found.name) : null;
}

/** The profile `podBrief` reads, from the reads (the web's `getProfile`). */
export function profileFrom(reads: ProfileReads): Profile {
    const unavailable: NonNullable<Profile["unavailable"]> = [];
    if (reads.agents === null) unavailable.push("tools");
    if (reads.schedules === null) unavailable.push("schedules");
    if (reads.apps === null) unavailable.push("apps");
    if (reads.tables === null) unavailable.push("tables");
    if (reads.functions === null) unavailable.push("functions");
    if (reads.workflows === null) unavailable.push("workflows");

    const detail = reads.defaultAgent;
    const about = detail?.instruction?.trim() || reads.pod?.description || "";
    /* The default agent's toolsets come back empty on the wire, because they
       are assigned when it runs; `grantedToolsets` knows that empty means the
       fixed set for it. */
    const skills: Skill[] = detail
        ? grantedToolsets(detail.toolsets, true).map((code) => {
              const capability = capabilityFor(code);
              return { id: capability.code, label: capability.word, blurb: capability.says };
          }).filter((skill) => Boolean(skill.label))
        : [];
    if (!detail && !unavailable.includes("tools")) unavailable.push("tools");

    const commitments: Commitment[] = readSchedules(reads.schedules ?? []).map((job) => ({
        id: job.id,
        title: job.title,
        detail: job.instruction || job.filter,
        cadence: job.trigger,
        since: job.since,
        active: job.active,
        last: "",
    }));

    const projects: Project[] = itemsOf(reads.apps)
        .map((raw) => raw as { id?: string; name?: string; description?: string | null; status?: string })
        .filter((app) => Boolean(app.name))
        .map((app) => ({
            id: app.id ?? (app.name as string),
            name: humanizeName(app.name as string),
            description: app.description ?? "",
            status: (app.status ?? "").toLowerCase(),
            tabId: "app:" + app.name,
        }));

    return {
        podId: reads.podId,
        name: humanizeName(reads.pod?.name ?? reads.podId),
        iconUrl: null,
        headline: (reads.pod?.description ?? "").trim(),
        joined: "",
        about: about.trim(),
        skills,
        permits: [],
        commitments,
        projects,
        unavailable,
        counts: {
            tables: itemsOf(reads.tables).length,
            functions: itemsOf(reads.functions).length,
            workflows: itemsOf(reads.workflows).length,
        },
    };
}
