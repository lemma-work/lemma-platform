"use client";

import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import { lemma } from "@/session/client";
import { isForbidden } from "@/session/auth-state";
import { COMMENTS_TABLE, readComment, type CommentRow } from "./model";

/** Whether comments can be used in this space yet.
 *
 *  `missing`: the table has not been made. Making it takes an editor, which
 *  is why this is a state the page says out loud rather than an error. */
export type CommentsStatus = "ready" | "missing" | "forbidden";

/** The table's shape. Row-level security off: a comment on a shared page is
 *  for everyone who can open the page, not only its author. */
const COLUMNS = [
    { name: "file_path", type: "TEXT", required: true, description: "The page this comment is on." },
    { name: "quote", type: "TEXT", description: "The passage it is about; empty for the page as a whole." },
    { name: "quote_prefix", type: "TEXT", description: "A little of the text just before the passage." },
    { name: "quote_suffix", type: "TEXT", description: "A little of the text just after the passage." },
    { name: "body", type: "TEXT", required: true, description: "What was said." },
    { name: "parent_id", type: "TEXT", description: "The comment this replies to; empty for a new thread." },
    { name: "resolved", type: "BOOLEAN", default: false, description: "The thread is done." },
    /* Written by the app, not `auto`: an auto USER column is filled with a
       random id (`gen_random_uuid()`), not with whoever wrote the row. */
    { name: "written_by", type: "USER", description: "Who wrote it." },
    { name: "author_name", type: "TEXT", description: "Their name as shown, for readers who cannot look the person up." },
    { name: "author_agent", type: "TEXT", description: "The bot that wrote it, when a bot replied." },
    { name: "mentioned_agent", type: "TEXT", description: "The bot this comment asks for, by name. A schedule wakes that bot." },
];

const status404 = (error: unknown) => (error as { statusCode?: number } | null)?.statusCode === 404;

/* ── the sample: an in-memory table ───────────────────────────────── */

const SAMPLE: CommentRow[] = [];
const sampleListeners = new Set<() => void>();
let sampleEnabled = true;
function sampleChanged() {
    for (const listen of sampleListeners) listen();
}

/* ── reads and writes ─────────────────────────────────────────────── */

/** Columns added after the first version of the table, brought in when an
 *  older table is opened. Quietly skipped for somebody not allowed to change
 *  the table; the write below copes with their absence. */
const LATER = ["written_by", "author_name"];
const upgraded = new Set<string>();

export async function commentsStatus(podId: string): Promise<CommentsStatus> {
    if (source.label === "sample") return sampleEnabled ? "ready" : "missing";
    try {
        const table = (await lemma(podId).tables.get(COMMENTS_TABLE)) as { columns?: { name?: string }[] };
        const have = new Set((table.columns ?? []).map((one) => one.name));
        if (!upgraded.has(podId)) {
            upgraded.add(podId);
            for (const name of LATER.filter((one) => !have.has(one))) {
                const column = COLUMNS.find((one) => one.name === name);
                if (column) await lemma(podId).tables.columns.add(COMMENTS_TABLE, column as never).catch(() => undefined);
            }
        }
        return "ready";
    } catch (error) {
        if (status404(error)) return "missing";
        if (isForbidden(error)) return "forbidden";
        throw error;
    }
}

export async function enableComments(podId: string): Promise<void> {
    if (source.label === "sample") { sampleEnabled = true; return; }
    await lemma(podId).tables.create({
        name: COMMENTS_TABLE,
        columns: COLUMNS,
        enable_rls: false,
        config: { description: "Comments on pages. One row per comment; a reply names its thread in parent_id." },
    } as never);
}

export async function listComments(podId: string, filePath: string): Promise<CommentRow[]> {
    if (source.label === "sample") return SAMPLE.filter((row) => row.filePath === filePath);
    const listed = await lemma(podId).records.list(COMMENTS_TABLE, {
        filters: [{ field: "file_path", op: "eq", value: filePath }],
        sort: [{ field: "created_at", direction: "asc" }],
        limit: 500,
    });
    const items = (listed as { items?: unknown[] }).items ?? [];
    return items.map(readComment).filter((row): row is CommentRow => row !== null);
}

export async function addComment(podId: string, data: Record<string, unknown>, author: { id: string | null; name: string }): Promise<CommentRow> {
    if (source.label === "sample") {
        const row = readComment({ ...data, id: "c" + Date.now().toString(36), written_by: author.id, author_name: author.name, created_at: new Date().toISOString() })!;
        SAMPLE.push(row);
        sampleChanged();
        return row;
    }
    const signed = { ...data, ...(author.id ? { written_by: author.id } : {}), author_name: author.name };
    let made: unknown;
    try {
        made = await lemma(podId).records.create(COMMENTS_TABLE, signed);
    } catch (error) {
        /* A table made before those columns, by someone who could not add
           them: the comment still goes, unsigned. */
        if ((error as { statusCode?: number } | null)?.statusCode !== 422 && (error as { statusCode?: number } | null)?.statusCode !== 400) throw error;
        made = await lemma(podId).records.create(COMMENTS_TABLE, data);
    }
    const row = readComment((made as { data?: unknown }).data ?? made);
    if (!row) throw new Error("The comment was saved but came back unreadable.");
    return row;
}

export async function updateComment(podId: string, id: string, patch: Record<string, unknown>): Promise<void> {
    if (source.label === "sample") {
        const at = SAMPLE.findIndex((row) => row.id === id);
        if (at >= 0) {
            SAMPLE[at] = { ...SAMPLE[at], ...(patch.resolved !== undefined ? { resolved: patch.resolved === true } : {}), ...(typeof patch.body === "string" ? { body: patch.body } : {}) };
            sampleChanged();
        }
        return;
    }
    await lemma(podId).records.update(COMMENTS_TABLE, id, patch);
}

export async function deleteComment(podId: string, id: string): Promise<void> {
    if (source.label === "sample") {
        const at = SAMPLE.findIndex((row) => row.id === id);
        if (at >= 0) { SAMPLE.splice(at, 1); sampleChanged(); }
        return;
    }
    await lemma(podId).records.delete(COMMENTS_TABLE, id);
}

/* ── hooks ─────────────────────────────────────────────────────────── */

export function useCommentsStatus(podId: string) {
    return useQuery({ queryKey: ["comments-status", podId], queryFn: () => commentsStatus(podId), staleTime: 5 * 60_000, retry: false });
}

/** A page's comments, kept live: the table's change stream refetches them
 *  the moment anyone — a person or a bot replying — writes one. */
export function useComments(podId: string, filePath: string, enabled: boolean) {
    const cache = useQueryClient();
    const key = ["comments", podId, filePath];
    const query = useQuery({ queryKey: key, queryFn: () => listComments(podId, filePath), enabled, staleTime: 30_000 });

    useEffect(() => {
        if (!enabled) return;
        const refresh = () => void cache.invalidateQueries({ queryKey: ["comments", podId, filePath] });
        if (source.label === "sample") {
            sampleListeners.add(refresh);
            return () => { sampleListeners.delete(refresh); };
        }
        const handle = lemma(podId).datastore.watchChanges({
            table: COMMENTS_TABLE,
            onChange: (frame) => {
                const path = (frame.payload as { file_path?: unknown } | null | undefined)?.file_path;
                if (path === undefined || path === filePath) refresh();
            },
        });
        return () => handle.close();
    }, [cache, enabled, filePath, podId]);

    return query;
}

/** Wake a comment's bot again: its `mentioned_agent` is cleared and set, and
 *  the second write is the change the wake-up's `to` condition fires on. */
export async function askAgain(podId: string, id: string, agentKey: string): Promise<void> {
    if (source.label === "sample") return;
    await lemma(podId).records.update(COMMENTS_TABLE, id, { mentioned_agent: null });
    await lemma(podId).records.update(COMMENTS_TABLE, id, { mentioned_agent: agentKey });
}
