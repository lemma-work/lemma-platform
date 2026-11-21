"use client";

import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { source, type ResourcePage } from "@/data";
import { readingProblem } from "@/library/file-status";
import { pathIn } from "@/library/library-cache";
import { createOrReplace } from "@/library/replace-on-conflict";
import { isForbidden } from "@/session/auth-state";
import { lemma } from "@/session/client";
import { describeSize, tooLarge } from "@/thread/attachments";
import { readStateOf, type FileItem } from "./file-kinds";

/* ── a folder's listing ──────────────────────────────────────────────── */

export type FolderPages = InfiniteData<ResourcePage<FileItem>, string | undefined>;

/** One folder, as the library lists it, plus each file's size and stored
 *  type. The source's listing maps those two away, and the Files page says
 *  "PDF · 1.2 MB"; so live, the same listing is read here with them kept.
 *  The rows are otherwise the library's own, under the library's own key,
 *  so its writes (`useLibraryWrites`) patch this list in place. The sample
 *  has no sizes to give and is read through the source as it always was. */
export async function listFolder(podId: string, directory: string, page?: string): Promise<ResourcePage<FileItem>> {
    if (source.label !== "live") return source.listLibrary(podId, "files", directory, page);
    const listed = await lemma(podId).files.list({ directoryPath: directory, limit: 50, pageToken: page });
    return {
        next: listed.next_page_token,
        items: listed.items.map((file) => ({
            id: file.id,
            name: file.name,
            kind: /folder|directory/i.test(file.kind) ? ("folder" as const) : ("file" as const),
            path: file.path,
            updated: file.updated_at,
            detail: file.description || file.mime_type || file.kind,
            ...(file.description ? { description: file.description } : {}),
            status: file.status,
            visibility: file.visibility,
            owner: file.owner_user_id ?? null,
            ...(typeof file.size_bytes === "number" ? { size: file.size_bytes } : {}),
            ...(file.mime_type ? { mime: file.mime_type } : {}),
        })),
    };
}

export const folderKey = (podId: string, directory: string) => ["library", podId, "files", directory];

/** A folder's listing, read again every ten seconds while the datastore is
 *  reading something in it, so "Reading…" turns into what happened without
 *  anyone asking, and not at all otherwise. */
export function useFolder(podId: string, directory: string, options: { poll?: boolean; enabled?: boolean } = {}) {
    return useInfiniteQuery({
        queryKey: folderKey(podId, directory),
        initialPageParam: undefined as string | undefined,
        queryFn: ({ pageParam }) => listFolder(podId, directory, pageParam),
        getNextPageParam: (page) => page.next || undefined,
        staleTime: 60_000,
        retry: 1,
        enabled: options.enabled !== false,
        refetchInterval: (query) => {
            if (options.poll === false) return false;
            const pages = query.state.data?.pages ?? [];
            return pages.some((page) => page.items.some((item) => readStateOf(item.status) === "reading")) ? 10_000 : false;
        },
    });
}

/* ── files handed over on the page ───────────────────────────────────── */

/** Where a file handed over on this page is: waiting its turn, going up,
 *  being read by the server, or settled one way or the other. "Reading" is
 *  only ever the datastore's own PENDING or PROCESSING; a file it does not
 *  read is "added", never "read". */
export type Arriving = "queued" | "up" | "reading" | "read" | "added" | "slow" | "upfailed" | "readfailed";

export interface Arrival {
    id: number;
    file: File;
    folder: string;
    path: string | null;
    state: Arriving;
    problem?: string;
    /** Whether the failure is one trying again could fix. */
    again?: boolean;
    /** Landed, and said so long enough: its row in the list takes over. */
    settled?: boolean;
}

/** How long a file is watched while it is read before the row stops
 *  asking and says it is taking a while. */
const WATCH_FOR = 10 * 60_000;
/** Uploads at once: enough that a pile of files does not crawl, few enough
 *  that the browser is not holding thirty requests open. */
const AT_ONCE = 3;
/** How long "Read" stays in a row of its own before the list's row for the
 *  file takes its place, and how long after that the row is let go. */
const SAID_FOR = 3000;
const KEPT_FOR = 9000;
let arrivalCount = 0;

function stateOf(status: string | null | undefined): Arriving {
    switch (readStateOf(status)) {
        case "reading":
            return "reading";
        case "read":
            return "read";
        case "failed":
            return "readfailed";
        default:
            return "added";
    }
}

/** Files handed over on the Files page, from the drop to the server's last
 *  word on them. Uploaded a few at a time into the folder they were dropped
 *  on; then, while the datastore is reading one, asked about it on a
 *  lengthening interval until it settles. The listing is refreshed as each
 *  one lands, so its row is there to take over once the row here has said
 *  how it went. A failure stays, with its reason and a way to try again. */
export function useArrivals(podId: string) {
    const cache = useQueryClient();
    const [rows, setRows] = useState<readonly Arrival[]>([]);
    const alive = useRef(true);
    const timers = useRef(new Set<number>());

    useEffect(() => {
        alive.current = true;
        const held = timers.current;
        return () => {
            alive.current = false;
            held.forEach((timer) => window.clearTimeout(timer));
            held.clear();
        };
    }, []);

    const later = (wait: number, then: () => void) => {
        if (!alive.current) return;
        const timer = window.setTimeout(() => {
            timers.current.delete(timer);
            if (alive.current) then();
        }, wait);
        timers.current.add(timer);
    };
    const drop = (id: number) => setRows((was) => was.filter((row) => row.id !== id));
    const patch = (id: number, change: Partial<Arrival>) => {
        setRows((was) => was.map((row) => (row.id === id ? { ...row, ...change } : row)));
        if (change.state === "read" || change.state === "added") {
            later(SAID_FOR, () => setRows((was) => was.map((row) => (row.id === id && (row.state === "read" || row.state === "added") ? { ...row, settled: true } : row))));
            later(KEPT_FOR, () => setRows((was) => was.filter((row) => !(row.id === id && row.settled))));
        }
    };
    const refresh = () => void cache.invalidateQueries({ queryKey: ["library", podId, "files"] });

    function watch(id: number, path: string, since = Date.now(), wait = 2000) {
        later(wait, async () => {
            if (Date.now() - since > WATCH_FOR) {
                patch(id, { state: "slow" });
                return;
            }
            try {
                const read = await lemma(podId).files.get(path);
                const state = stateOf(read.status);
                if (state === "reading") {
                    watch(id, path, since, Math.min(wait * 1.5, 15_000));
                    return;
                }
                patch(id, { state, problem: state === "readfailed" ? readingProblem(read.last_processing_error) : undefined, again: state === "readfailed" });
                refresh();
            } catch {
                /* A blip is not an answer about the file: ask again later. */
                watch(id, path, since, Math.min(wait * 1.5, 15_000));
            }
        });
    }

    async function send(row: Arrival) {
        patch(row.id, { state: "up", problem: undefined, again: undefined });
        const files = lemma(podId).files;
        try {
            const written = await createOrReplace(
                () => files.upload(row.file, { name: row.file.name, directoryPath: row.folder, searchEnabled: true }),
                () => files.update(pathIn(row.folder, row.file.name), { file: row.file }),
            );
            const state = stateOf(written.status);
            patch(row.id, { state, path: written.path, problem: state === "readfailed" ? readingProblem(written.last_processing_error) : undefined, again: state === "readfailed" });
            if (state === "reading") watch(row.id, written.path);
        } catch (problem) {
            patch(row.id, {
                state: "upfailed",
                problem: isForbidden(problem) ? "You can’t add files here." : problem instanceof Error && problem.message ? problem.message : "That file didn’t upload.",
                again: !isForbidden(problem),
            });
        }
    }

    /** Take files, each with the folder it goes in. Only live: the sample
     *  has nowhere to put them, and its page says so. */
    async function take(list: readonly { file: File; directory: string }[]) {
        if (source.label !== "live" || list.length === 0) return;
        const fresh: Arrival[] = list.map(({ file, directory }) => {
            arrivalCount += 1;
            return tooLarge(file)
                ? { id: arrivalCount, file, folder: directory, path: null, state: "upfailed", problem: "Too large to upload (" + describeSize(file.size) + ").", again: false }
                : { id: arrivalCount, file, folder: directory, path: null, state: "queued" };
        });
        setRows((was) => [...fresh, ...was]);
        const queue = fresh.filter((row) => row.state === "queued");
        await Promise.all(Array.from({ length: Math.min(AT_ONCE, queue.length) }, async () => {
            for (let next = queue.shift(); next; next = queue.shift()) {
                await send(next);
                refresh();
            }
        }));
    }

    /** Read a failed file again: the server queues it and says so. */
    async function reread(row: Arrival) {
        if (!row.path) return;
        patch(row.id, { state: "reading", problem: undefined, again: undefined });
        try {
            const read = await lemma(podId).files.retryProcessing(row.path);
            const state = stateOf(read.status);
            patch(row.id, { state, problem: state === "readfailed" ? readingProblem(read.last_processing_error) : undefined, again: state === "readfailed" });
            if (state === "reading") watch(row.id, row.path);
        } catch {
            patch(row.id, { state: "readfailed", problem: "I couldn’t start reading it again.", again: true });
        }
    }

    function retry(row: Arrival) {
        if (row.state === "upfailed") void send(row).then(refresh);
        else if (row.state === "readfailed") void reread(row);
        else if (row.state === "slow" && row.path) {
            patch(row.id, { state: "reading" });
            watch(row.id, row.path, Date.now(), 0);
        }
    }

    return { rows, take, retry, dismiss: drop };
}
