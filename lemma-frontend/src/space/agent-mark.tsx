"use client";

import type { Pod } from "@/data";
import { Mark } from "@/shell/mark";
import { characterFromUrl } from "@/shell/character";
import { parseResourceIcon } from "@/shell/resource-icon";
import { TeammateFace } from "./teammate-face";

/** An agent's mark.
 *
 *  The cast is for teammates — the ones somebody hired and talks to. An agent
 *  is what a teammate hands a piece of the job to, and an agent wearing a
 *  character is how a workflow step came to look like somebody you could ask.
 *  So an agent is its initial on a plain tile, unless somebody gave it a
 *  picture or an emoji of its own. And the space's own agent is not one of
 *  them at all: it is the teammate, so it wears the teammate's face. */
export function AgentMark({ pod, agent, size }: {
    pod: Pick<Pod, "id" | "name" | "iconUrl">;
    agent: { name: string; label: string; front?: boolean; iconUrl?: string | null };
    size: number;
}) {
    if (agent.front) return <TeammateFace pod={pod} size={size} />;
    const icon = parseResourceIcon(agent.iconUrl);
    const chosen = icon?.kind === "glyph" || (icon?.kind === "url" && !characterFromUrl(icon.url));
    if (chosen) return <Mark seed={pod.id + ":" + agent.name} name={agent.label} icon={agent.iconUrl} size={size} still />;
    return (
        <span className="amark" style={{ width: size, height: size, fontSize: Math.round(size * 0.42) }} aria-hidden="true">
            {(agent.label.trim()[0] ?? "?").toUpperCase()}
        </span>
    );
}
