import type { LibraryItem } from "../../../thread/library-item";
import type { AboutLine } from "../types";
import { isForbidden, statusOf } from "../words";

export type { LibraryItem };

/** Which other Lemmas a Lemma works with, and what each may read: the
 *  shapes and words, apart from drawing them. Pure, so `tests/` can hold it.
 *  The shapes are the web's (`data/types.ts`), which `web/pod-links.ts`
 *  takes. */

/** Another Lemma this one can ask: as you, because you are in both, or
 *  because that one allowed it. */
export interface AskablePod {
    id: string;
    name: string;
    iconUrl: string | null;
    description?: string;
    throughYou: boolean;
    connected: boolean;
}

/** What a connection lets the other Lemma read, past what is Public. */
export interface LinkShare {
    kind: "table" | "folder";
    name: string;
}

/** Another Lemma allowed to ask this one with nobody present, reading what
 *  was shared with it. */
export interface PodLink {
    podId: string;
    name: string;
    iconUrl: string | null;
    description?: string;
    /** Who connected it, and looks after it. */
    stewardName?: string;
    shared: LinkShare[];
}

export interface ResourcePage<T> {
    items: T[];
    next?: string | null;
}

function itemsOf(value: unknown): unknown[] {
    if (Array.isArray(value)) return value;
    return (value as { items?: unknown[] } | null)?.items ?? [];
}

/** `GET /pods/{id}/askable-pods`, read as the web's `askablePods` reads it. */
export function askableFrom(listed: unknown): AskablePod[] {
    return itemsOf(listed)
        .map((raw) => (raw ?? {}) as { pod_id?: string; name?: string; icon_url?: string | null; description?: string | null; through_you?: boolean; connected?: boolean })
        .filter((pod) => Boolean(pod.pod_id && pod.name))
        .map((pod) => ({
            id: String(pod.pod_id),
            name: String(pod.name),
            iconUrl: pod.icon_url ?? null,
            ...(pod.description ? { description: pod.description } : {}),
            throughYou: pod.through_you === true,
            connected: pod.connected === true,
        }));
}

/** `GET /pods/{id}/pod-links`, read as the web's `podLinks` reads it. */
export function linksFrom(listed: unknown): PodLink[] {
    return itemsOf(listed)
        .map((raw) => (raw ?? {}) as {
            pod_id?: string; name?: string; icon_url?: string | null; description?: string | null;
            steward_name?: string | null;
            shared?: { resource_type?: string; resource_name?: string }[];
        })
        .filter((link) => Boolean(link.pod_id && link.name))
        .map((link) => ({
            podId: String(link.pod_id),
            name: String(link.name),
            iconUrl: link.icon_url ?? null,
            ...(link.description ? { description: link.description } : {}),
            ...(link.steward_name ? { stewardName: link.steward_name } : {}),
            shared: (link.shared ?? [])
                .filter((one) => typeof one.resource_name === "string")
                .map((one) => ({ kind: one.resource_type === "datastore_table" ? "table" as const : "folder" as const, name: one.resource_name as string })),
        }));
}

/** What `PUT /pods/{id}/pod-links/{asker}` is sent: every share, read only. */
export function connectBody(shares: readonly LinkShare[]): { shares: { resource_type: string; resource_name: string; permission_ids: string[] }[] } {
    return {
        shares: shares.map((share) => share.kind === "table"
            ? { resource_type: "datastore_table", resource_name: share.name, permission_ids: ["datastore.table.read", "datastore.record.read"] }
            : { resource_type: "folder", resource_name: share.name, permission_ids: ["folder.read"] }),
    };
}

/** A table, as the share list takes it. */
export function tableItem(raw: { id?: string; name?: string; updated_at?: string | null; visibility?: string | null }): LibraryItem {
    const name = String(raw.name ?? "");
    return { id: String(raw.id ?? name), name, kind: "table", path: name, updated: raw.updated_at ?? "", detail: "Table", ...(raw.visibility ? { visibility: raw.visibility } : {}) };
}

/** A file or folder, as the share list takes it. */
export function fileItem(raw: { id?: string; name?: string; kind?: string; path?: string; updated_at?: string | null; visibility?: string | null; description?: string | null }): LibraryItem {
    const path = String(raw.path ?? raw.name ?? "");
    return {
        id: String(raw.id ?? path),
        name: String(raw.name ?? path.split("/").pop() ?? path),
        kind: /folder|directory/i.test(String(raw.kind ?? "")) ? "folder" : "file",
        path,
        updated: raw.updated_at ?? "",
        detail: raw.description ?? "",
        ...(raw.visibility ? { visibility: raw.visibility } : {}),
    };
}

/** The Settings tab's line: "Asks 2 · asked by 1", or "Nobody yet". */
export function worksWithLine(asks: number | null, askedBy: number): AboutLine {
    if (asks === null) return { text: null };
    return { text: [asks ? `Asks ${asks}` : "", askedBy ? `asked by ${askedBy}` : ""].filter(Boolean).join(" · ") || "Nobody yet" };
}

/** How it can ask another: "As you", "Allowed by Renewals", or both. */
export function howItAsks(other: Pick<AskablePod, "throughYou" | "connected" | "name">): string {
    return [other.throughYou ? "As you" : "", other.connected ? `Allowed by ${other.name}` : ""].filter(Boolean).join(" · ");
}

/** Why connecting failed, as the web says it. */
export function connectProblem(error: unknown, lemma: string): string {
    const status = statusOf(error);
    if (status === 403 || isForbidden(error)) return `Only an admin of ${lemma} can let another Lemma ask it.`;
    if (status === 409) return "You can only connect a Lemma you’re in.";
    if (status === 400) return "Something ticked can’t be shared. Untick it and try again.";
    return "That couldn’t be saved. Try again.";
}

/** What stops when a connection is ended: not "are you sure", but the asks
 *  it runs on its own and what it could read. */
export function disconnectSaid(link: Pick<PodLink, "name" | "shared">, lemma: string): string {
    const loses = link.shared.length > 0 ? ` It loses what was shared: ${link.shared.map((one) => one.name).join(", ")}.` : "";
    return `${link.name} will no longer be able to ask ${lemma}, and an answer it is waiting for won’t come back.${loses}`;
}
