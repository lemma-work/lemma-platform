"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { useSchedules } from "@/schedule/queries";
import { useSurfaces } from "@/shell/surfaces";
import { channelName } from "@/shell/channels";
import { useRunsOn } from "@/shell/runs-on";
import { readSkills, skillsKey, SKILLS_FRESH } from "@/skills/read-skills";
import { changedLately } from "@/thread/memory-notes";
import { useMemoryNotes } from "./what-it-remembers";
import type { AboutSection } from "./about-sections";
import { missingForMe } from "@/data/pod-connectors";
import { asMine, usePodMay } from "@/connect/pod-connect";
import { CONNECTOR_USE } from "@/data/agents";
import { useMe } from "@/session/use-me";

/** One line under a part of About on the overview: what is true there now.
 *
 *  `text` is null while it is being read, and on a failure — the tile then
 *  says what the part is for instead, so a refused read never looks like an
 *  empty one. `needs` marks something waiting on a person: somebody asking to
 *  join, standing work the breaker stopped, a model that is not there. */
export interface AboutLine {
    text: string | null;
    needs?: boolean;
}

function count(n: number, one: string, many: string): string {
    return n + " " + (n === 1 ? one : many);
}

/** "Researcher, Writer +2". */
function firstFew(names: string[], shown = 2): string {
    const head = names.slice(0, shown).join(", ");
    return names.length > shown ? head + " +" + (names.length - shown) : head;
}

/** Every line at once. Each reads through the query its own section uses,
 *  under the same key, so opening a part after the overview draws it from
 *  what the overview already fetched. */
export function useAboutLines(pod: Pod): Record<AboutSection, AboutLine> {
    const cache = useQueryClient();

    /* Who may knock is an admin's question; anyone else is refused, which is
       the ordinary case, so a refusal just says nothing about it. */
    const knocking = useQuery({
        queryKey: ["pod-knocking", pod.id],
        queryFn: () => source.listJoinRequests(pod.id),
        retry: false,
    });
    const surfaces = useSurfaces(pod.id);
    const askable = useQuery({ queryKey: ["askable-pods", pod.id], queryFn: () => source.askablePods(pod.id), staleTime: 60_000 });
    const links = useQuery({ queryKey: ["pod-links", pod.id], queryFn: () => source.podLinks(pod.id), staleTime: 60_000 });
    const skills = useQuery({ queryKey: skillsKey(pod.id), queryFn: () => readSkills(pod.id, cache), staleTime: SKILLS_FRESH });
    const notes = useMemoryNotes(pod);
    const schedules = useSchedules(pod.id);
    const agents = useQuery({ queryKey: ["agents", pod.id], queryFn: () => source.listAgents(pod.id), staleTime: 60_000 });
    const runsOn = useRunsOn(pod.id, pod.orgId);
    const podConnectors = useQuery({ queryKey: ["pod-connectors", pod.id], queryFn: () => source.listPodConnectors(pod.id), staleTime: 30_000 });
    const accounts = useQuery({ queryKey: ["accounts", pod.orgId], queryFn: () => source.listAccounts(pod.orgId), staleTime: 30_000 });
    const me = useMe() ?? "";
    /* Asked only of a server that does not say `can_use` on each connector. */
    const podUse = usePodMay(pod.id, CONNECTOR_USE, (podConnectors.data ?? []).some((one) => one.canUse === null));

    const asking = knocking.data?.length ?? 0;
    const people: AboutLine = {
        text: count(pod.members.length, "person", "people") + (asking ? " · " + asking + " asking to join" : ""),
        needs: asking > 0,
    };

    const reachedOn = [...new Set((surfaces.data ?? []).filter((one) => one.active).map((one) => channelName(one.platform)))];
    const channels: AboutLine = {
        text: surfaces.isSuccess ? (reachedOn.length ? firstFew(reachedOn, 3) : "Only here, in Lemma") : null,
    };

    const asks = askable.data?.length ?? 0;
    const askedBy = links.data?.length ?? 0;
    const worksWith: AboutLine = {
        text: askable.isSuccess
            ? [asks && "Asks " + asks, askedBy && "asked by " + askedBy].filter(Boolean).join(" · ") || "Nobody yet"
            : null,
    };

    const taught: AboutLine = {
        text: skills.isSuccess ? (skills.data.length ? count(skills.data.length, "skill", "skills") : "Nothing yet") : null,
    };

    const lately = (notes.data ?? []).filter((note) => changedLately(note.updated)).length;
    const memory: AboutLine = {
        text: notes.isSuccess
            ? notes.data.length
                ? count(notes.data.length, "note", "notes") + (lately ? " · " + lately + " this week" : "")
                : "Nothing written down yet"
            : null,
    };

    const jobs = schedules.data ?? [];
    const stopped = jobs.filter((job) => job.pausedByFailures).length;
    const standing: AboutLine = {
        text: schedules.isSuccess
            ? jobs.length
                ? count(jobs.length, "schedule", "schedules") + (stopped ? " · " + stopped + " stopped" : "")
                : "Nothing yet"
            : null,
        needs: stopped > 0,
    };

    const helpers = (agents.data ?? []).filter((row) => !row.front).map((row) => row.label);
    const handsTo: AboutLine = {
        text: agents.isSuccess ? (helpers.length ? firstFew(helpers) : "Does the whole job itself") : null,
    };

    const model: AboutLine = {
        text: runsOn.current.isSuccess && runsOn.runtimes.isSuccess ? runsOn.said : null,
        needs: Boolean(runsOn.setup),
    };

    /* The pod's own connectors, by the slug it calls them, and whether any
       waits on somebody: one with no account shared, or one this pod asks
       everyone to connect that I have not. */
    const declared = podConnectors.data ?? [];
    const stuck = declared.filter((one) => one.status !== "READY").length
        + (accounts.isSuccess ? missingForMe(declared, asMine(accounts.data), me, podUse.answered ? podUse.allowed : null).length : 0);
    const connected: AboutLine = {
        text: podConnectors.isSuccess
            ? declared.length
                ? stuck ? count(stuck, "connection needs", "connections need") + " attention" : firstFew(declared.map((one) => one.slug), 3)
                : "Nothing connected yet"
            : null,
        needs: stuck > 0,
    };

    return {
        people,
        connections: connected,
        channels,
        "works-with": worksWith,
        skills: taught,
        memory,
        schedules: standing,
        agents: handsTo,
        model,
        /* Which apps have been connected is a read of the API's own OAuth
           grants, which the part itself makes; here the line says which apps
           it is for. */
        "ai-tools": { text: "ChatGPT, Claude, Claude Code" },
    };
}
