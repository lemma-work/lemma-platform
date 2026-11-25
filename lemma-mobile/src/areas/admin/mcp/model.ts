import type { Lemma } from "../../../data/types";
import { agoOf } from "../people/model";
import type { AboutLine } from "../types";
import { count, problemOf } from "../words";
import { accessLabel, verifiedHost, type ConnectedClient } from "@web/space/agent-access-model";

/** Using a Lemma from other AI apps over MCP: what the screen says, apart
 *  from drawing it. Pure, so `tests/` can hold it. The rules and the
 *  per-app steps are the web's own (`web/agent-access-model.ts`). */

/** What the web's model takes for a Lemma: its id and name. */
export type Pod = Pick<Lemma, "id" | "name">;

/** The apps connected to a Lemma: everyone's for whoever may see them all,
 *  else the signed-in person's own (`everyone` says which). */
export interface Grants {
    items: ConnectedClient[];
    everyone: boolean;
}

/** A failure in a person's words: the server's when it said some, with any
 *  status code the web's sentence carries left off. */
export function plainProblem(error: unknown, fallback: string): string {
    return problemOf(error, fallback).replace(/\s*\(\d{3}\)(?=\.?$)/, "");
}

/** Who connected an app: "you", somebody here, or "a former member". The
 *  viewer first, since a Lemma's people are who else is in it. */
export function whoConnected(userId: string, me: string | null, people: readonly { userId: string | null; name: string }[]): string {
    if (me && userId === me) return "you";
    return people.find((person) => person.userId === userId)?.name ?? "a former member";
}

/** The quiet line under a connected app: where it is from (the one checked
 *  fact about it), who connected it when that is not obvious, what it may
 *  do, and when it was last used. */
export function grantLine(item: ConnectedClient, who: string | null, now: number = Date.now()): string {
    const host = verifiedHost(item);
    const when = item.last_used_at ? `used ${agoOf(item.last_used_at, now)}` : `connected ${agoOf(item.connected_at, now)}`;
    return [host ? `from ${host}` : "unverified app", who ? `by ${who}` : "", accessLabel(item.scopes), when].filter(Boolean).join(" · ");
}

/** The Settings tab's line: how many apps are connected, once that is read,
 *  else which apps it is for. */
export function mcpLine(connected: number | null): AboutLine {
    if (connected) return { text: `${count(connected, "app", "apps")} connected` };
    return { text: "ChatGPT, Claude, Claude Code" };
}
