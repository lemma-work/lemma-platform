import { NEW_CONVERSATION } from "@/data/types";
import type { Profile } from "@/data/types";

/** Reuse the open conversation. Only an empty/new pane needs creation. */
export async function openCallConversation<T extends { id: string }>(
    client: { conversations: {
        get: (id: string, options: { pod_id: string }) => Promise<T>;
        create: (payload: { pod_id: string }) => Promise<T>;
    } },
    podId: string,
    conversationId: string | null,
): Promise<T> {
    if (conversationId && conversationId !== NEW_CONVERSATION) {
        return client.conversations.get(conversationId, { pod_id: podId });
    }
    return client.conversations.create({ pod_id: podId });
}

/* Roughly a third of what the voice session will accept, which leaves the
   conversation history and the standing rules space to breathe. The gateway
   rejects a system instruction over 16,000 characters outright. */
const BUDGET = 2600;
const MAX_SKILLS = 10;
const MAX_COMMITMENTS = 4;
const MAX_PROJECTS = 5;

function clip(text: string, limit: number): string {
    const clean = text.replace(/\s+/g, " ").trim();
    return clean.length > limit ? clean.slice(0, limit) + "…" : clean;
}

/** Where the voice model is, in terms it can use out loud.
 *
 *  Built from `source.getProfile`, which reads the pod itself — its default
 *  agent's instruction, that agent's toolsets and allowed actions, its
 *  schedules and its apps. None of it is written here, which is the point: a
 *  hand-written blurb about "your capabilities" goes stale the first time
 *  someone adds a toolset, and a voice confidently claiming a skill it no
 *  longer has is worse than one that says it does not know.
 *
 *  Every section is dropped when it is empty rather than filled with a
 *  placeholder — the same rule the profile page follows, for the same reason:
 *  a pod with no standing work should not be described as having some. */
export function podBrief(profile: Profile, teammate: string): string {
    const lines: string[] = [];

    lines.push(
        "Where you are. Lemma is a workspace where people work alongside AI teammates. A pod is one teammate's own workspace — its instructions, its data tables, its files, its scheduled work and the apps it has built. It is a place, not a chat thread, and the person on this call already has one open.",
    );
    lines.push(
        `This pod is "${clip(profile.name, 80)}" and you are the voice of ${clip(teammate, 60)}, the teammate whose pod it is. When the person says "you", they mean this teammate — the one that holds the data and does the work — not you specifically. Speak as it.`,
    );

    const headline = clip(profile.headline, 200);
    if (headline) lines.push(`What it is for: ${headline}`);

    /* The default agent's own instruction, which is the only prose a pod
       writes about itself. Clipped hard: it can run to thousands of words, and
       a voice model needs the gist, not the contract. */
    const about = clip(profile.about, 700);
    if (about) lines.push(`How it describes itself: ${about}`);

    const skills = profile.skills.slice(0, MAX_SKILLS).map((skill) => skill.label).filter(Boolean);
    if (skills.length) lines.push(`What it can actually do: ${skills.join(", ")}.`);

    /* Counted, never estimated. "Some tables" is the kind of thing that gets
       said out loud and then turns out to be nine hundred. */
    const { tables, functions, workflows } = profile.counts;
    const holdings = [
        tables ? `${tables} data table${tables === 1 ? "" : "s"}` : "",
        functions ? `${functions} function${functions === 1 ? "" : "s"}` : "",
        workflows ? `${workflows} workflow${workflows === 1 ? "" : "s"}` : "",
    ].filter(Boolean);
    if (holdings.length) lines.push(`It holds ${holdings.join(", ")}.`);

    const standing = profile.commitments
        .filter((commitment) => commitment.active)
        .slice(0, MAX_COMMITMENTS)
        .map((commitment) => `${clip(commitment.title, 60)} (${clip(commitment.cadence, 40)})`);
    if (standing.length) lines.push(`Standing work that runs without being asked: ${standing.join("; ")}.`);

    const projects = profile.projects.slice(0, MAX_PROJECTS).map((project) => clip(project.name, 50));
    if (projects.length) lines.push(`Apps it has built and runs: ${projects.join(", ")}.`);

    /* Trimmed from the bottom, unlike the conversation history, which trims
       from the top. The order here is deliberate — what a pod IS matters more
       to a spoken answer than how many workflows it happens to own — so the
       last section is the most expendable. */
    while (lines.length > 2 && lines.join("\n\n").length > BUDGET) {
        lines.pop();
    }

    return lines.join("\n\n");
}
