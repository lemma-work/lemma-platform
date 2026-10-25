import { useCallback, useEffect, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { useCacheKey } from "../../data/hooks";
import type { PickedFile } from "../../data/io";
import { useLiveTopic } from "../../live/pod-live";
import { useArea } from "../use-area";
import { MAX_UPLOAD_BYTES, describeSize, isMissingFolder, makePageIn, nameOf, pageFolderFor, parentOf } from "./model";
import type { CommentRow, LibraryItem, ResourcePage } from "./source";
import { COMMENTS_TABLE } from "@web/docpages/comments/model";
import { readingProblem, withItemStatus } from "@web/library/file-status";
import { nameProblem, pathIn, renamedPath, withItem, withoutItem, withRenamedItem, type Pages } from "@web/library/library-cache";

/** The files screens' reads and writes, through react-query. Every key
 *  starts with `useCacheKey()`, so sample data and each server keep their
 *  own cache entries. */
export function useFilesSource() {
    return useArea("files");
}

/** Every files key, under the cache key and the Lemma. */
export function useFilesKey(podId: string) {
    const key = useCacheKey();
    return useCallback((...rest: unknown[]) => [key, "files", podId, ...rest], [key, podId]);
}

const READING = new Set(["PENDING", "PROCESSING"]);

/** A folder's listing, a page at a time. Read again every few seconds while
 *  something in it is still being read, so "Reading…" turns into nothing (or
 *  into "Couldn’t read") without anybody pulling to refresh. */
export function useFolder(podId: string, directory: string, enabled = true) {
    const source = useFilesSource();
    const keyOf = useFilesKey(podId);
    return useInfiniteQuery({
        queryKey: keyOf("list", directory),
        initialPageParam: undefined as string | undefined,
        queryFn: ({ pageParam }) => source.list(podId, directory, pageParam),
        getNextPageParam: (page: ResourcePage<LibraryItem>) => page.next || undefined,
        staleTime: 30_000,
        enabled,
        /* A folder that is not there answers the same the second time. */
        retry: (count, error) => !isMissingFolder(error) && count < 2,
        refetchInterval: (query) => {
            const pages = query.state.data?.pages ?? [];
            return pages.some((page) => page.items.some((item) => READING.has((item.status ?? "").toUpperCase()))) ? 5_000 : false;
        },
    });
}

export function useFileContent(podId: string, path: string) {
    const source = useFilesSource();
    const keyOf = useFilesKey(podId);
    return useQuery({ queryKey: keyOf("read", path), queryFn: () => source.read(podId, path), staleTime: 5 * 60_000 });
}

/** Search inside files, once there is something to search for. */
export function useFileSearch(podId: string, query: string, within: string) {
    const source = useFilesSource();
    const keyOf = useFilesKey(podId);
    const [settled, setSettled] = useState(query);
    useEffect(() => {
        const timer = setTimeout(() => setSettled(query.trim()), 350);
        return () => clearTimeout(timer);
    }, [query]);
    return useQuery({
        queryKey: keyOf("search", within, settled),
        queryFn: () => source.search(podId, settled, within),
        enabled: settled.length >= 2,
        staleTime: 60_000,
    });
}

/** Making, renaming, moving and removing things in a folder, as the web's
 *  `library-writes.ts` does them: the listing is patched in place rather than
 *  read again, and a write the server refuses puts it back. */
export function useWrites(podId: string, directory: string) {
    const source = useFilesSource();
    const keyOf = useFilesKey(podId);
    const cache = useQueryClient();
    const [busy, setBusy] = useState<string | null>(null);
    const [problem, setProblem] = useState<string | null>(null);
    const listKey = (dir: string) => keyOf("list", dir);
    const patch = (dir: string, change: (pages: Pages | undefined) => Pages | undefined) =>
        cache.setQueryData<InfiniteData<ResourcePage<LibraryItem>>>(listKey(dir), (pages) => change(pages as Pages | undefined) as never);
    const said = (failure: unknown, fallback: string) => (failure instanceof Error && failure.message ? failure.message : fallback);

    async function upload(files: PickedFile[]): Promise<LibraryItem[]> {
        const big = files.filter((one) => (one.size ?? 0) > MAX_UPLOAD_BYTES);
        setProblem(big.length === 0 ? null : big.length === 1 ? `${big[0].name} is too large to upload (${describeSize(big[0].size ?? 0)}).` : `${big.length} files are too large to upload.`);
        const made: LibraryItem[] = [];
        for (const file of files) {
            if ((file.size ?? 0) > MAX_UPLOAD_BYTES) continue;
            setBusy(file.name);
            try {
                const item = await source.upload(podId, directory, file);
                made.push(item);
                patch(directory, (pages) => withItem(pages, item));
            } catch (failure) {
                setProblem(said(failure, `${file.name} did not upload.`));
            } finally {
                setBusy(null);
            }
        }
        return made;
    }

    async function createFolder(name: string): Promise<boolean> {
        const bad = nameProblem(name);
        if (bad) { setProblem(bad); return false; }
        setBusy(name.trim());
        setProblem(null);
        try {
            const item = await source.makeFolder(podId, directory, name.trim());
            patch(directory, (pages) => withItem(pages, item));
            return true;
        } catch (failure) {
            setProblem(said(failure, "That folder was not made."));
            return false;
        } finally {
            setBusy(null);
        }
    }

    async function rename(item: LibraryItem, name: string): Promise<string | null> {
        const bad = nameProblem(name);
        if (bad) { setProblem(bad); return null; }
        const clean = name.trim();
        if (clean === item.name) return item.path;
        const next = renamedPath(item.path, clean);
        const dir = parentOf(item.path);
        const before = cache.getQueryData(listKey(dir));
        setBusy(item.path);
        setProblem(null);
        patch(dir, (pages) => withRenamedItem(pages, item.path, clean, next));
        try {
            await source.move(podId, item.path, next);
            void cache.invalidateQueries({ queryKey: keyOf("read", item.path) });
            return next;
        } catch (failure) {
            cache.setQueryData(listKey(dir), before);
            setProblem(said(failure, "That was not renamed."));
            return null;
        } finally {
            setBusy(null);
        }
    }

    async function move(item: LibraryItem, folder: string): Promise<string | null> {
        const next = pathIn(folder, item.name);
        const dir = parentOf(item.path);
        const before = cache.getQueryData(listKey(dir));
        setBusy(item.path);
        setProblem(null);
        patch(dir, (pages) => withoutItem(pages, item.path));
        try {
            await source.move(podId, item.path, next);
            void cache.invalidateQueries({ queryKey: listKey(folder) });
            return next;
        } catch (failure) {
            cache.setQueryData(listKey(dir), before);
            setProblem((failure as { statusCode?: number } | null)?.statusCode === 409 ? `${folder === "/" ? "That folder" : nameOf(folder)} already holds something called ${item.name}.` : said(failure, "That was not moved."));
            return null;
        } finally {
            setBusy(null);
        }
    }

    async function remove(item: LibraryItem): Promise<boolean> {
        const dir = parentOf(item.path);
        const before = cache.getQueryData(listKey(dir));
        setBusy(item.path);
        setProblem(null);
        patch(dir, (pages) => withoutItem(pages, item.path));
        try {
            await source.remove(podId, item.path);
            return true;
        } catch (failure) {
            cache.setQueryData(listKey(dir), before);
            setProblem(said(failure, "That was not deleted."));
            return false;
        } finally {
            setBusy(null);
        }
    }

    /** Read a failed file again: the row says "Reading…" at once. */
    async function retry(item: LibraryItem) {
        const dir = parentOf(item.path);
        const before = cache.getQueryData(listKey(dir));
        setBusy(item.path);
        setProblem(null);
        patch(dir, (pages) => withItemStatus(pages, item.path, "PENDING"));
        try {
            const settled = await source.retry(podId, item.path);
            patch(dir, (pages) => withItemStatus(pages, item.path, settled));
        } catch (failure) {
            cache.setQueryData(listKey(dir), before);
            setProblem(said(failure, "That file was not queued again."));
        } finally {
            setBusy(null);
        }
    }

    async function explain(item: LibraryItem): Promise<string> {
        try {
            return await source.why(podId, item.path);
        } catch {
            return readingProblem(null);
        }
    }

    return { busy, problem, clearProblem: () => setProblem(null), upload, createFolder, rename, move, remove, retry, explain };
}

/** A new page where `pageFolderFor` puts it, under a name not yet taken:
 *  from a template, or "Untitled". Answers its path. */
export function useNewPage(podId: string, directory: string) {
    const source = useFilesSource();
    const keyOf = useFilesKey(podId);
    const cache = useQueryClient();
    return useMutation({
        mutationFn: async ({ base, text }: { base: string; text: string }) => {
            const folder = pageFolderFor(directory);
            let taken = new Set<string>();
            try {
                taken = new Set((await source.list(podId, folder)).items.map((item) => item.name.toLowerCase()));
            } catch {
                /* No listing: names are found by trying. */
            }
            return makePageIn((at, body) => source.createText(podId, at, body), folder, base, text, taken);
        },
        onSuccess: () => {
            void cache.invalidateQueries({ queryKey: keyOf("list") });
        },
    });
}

/* ── comments ────────────────────────────────────────────────────────── */

export function useCommentsStatus(podId: string) {
    const source = useFilesSource();
    const keyOf = useFilesKey(podId);
    return useQuery({ queryKey: keyOf("comments-status"), queryFn: () => source.commentsStatus(podId), staleTime: 5 * 60_000, retry: false });
}

/** A page's comments, kept live: the table's rows on the Lemma's socket
 *  read them again the moment anyone, a person or an agent, writes one. */
export function useComments(podId: string, path: string, enabled: boolean) {
    const source = useFilesSource();
    const keyOf = useFilesKey(podId);
    const cache = useQueryClient();
    const query = useQuery({ queryKey: keyOf("comments", path), queryFn: () => source.comments(podId, path), enabled, staleTime: 30_000 });
    const refresh = () => void cache.invalidateQueries({ queryKey: keyOf("comments", path) });
    useLiveTopic(enabled ? podId : null, "records", { table: COMMENTS_TABLE }, {
        onEvents: (changes) => {
            for (const change of changes as { row?: { file_path?: unknown } | null }[]) {
                if (!change?.row || change.row.file_path === undefined || change.row.file_path === path) { refresh(); return; }
            }
        },
        onResync: refresh,
    });
    return query;
}

export function useMentionable(podId: string, lemmaName: string) {
    const source = useFilesSource();
    const keyOf = useFilesKey(podId);
    return useQuery({ queryKey: keyOf("mentionable", lemmaName), queryFn: () => source.mentionable(podId, lemmaName), staleTime: 5 * 60_000 });
}

export function useWakes(podId: string, enabled: boolean) {
    const source = useFilesSource();
    const keyOf = useFilesKey(podId);
    return useQuery({ queryKey: keyOf("wakes"), queryFn: () => source.wakes(podId), enabled, staleTime: 60_000 });
}

export type { CommentRow };
