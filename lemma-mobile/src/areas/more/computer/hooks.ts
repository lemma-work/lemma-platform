import { useEffect, useRef } from "react";
import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useIsFocused } from "expo-router";
import * as WebBrowser from "expo-web-browser";
import { useCacheKey } from "../../../data/hooks";
import { shareFile } from "../../../data/io";
import { moreKey, useMoreSource } from "../hooks";
import { readLimit, SAVE_LIMIT, saving, statusEvery, TOO_LARGE_TO_SAVE, tooLargeToShow, viewerOf } from "./model";
import type { ComputerSource } from "./source";

/** The computer's reads and writes, through react-query (the web's
 *  `computer/queries.ts`, read for read).
 *
 *  Every read is asked only while its screen is focused: a screen left
 *  behind another does not go on asking a computer about itself. And none
 *  of them starts it: listing and the logins wait for `wake`, the browser's
 *  status never provisions. */

export function useComputerSource(): ComputerSource {
    return useMoreSource().computer;
}

function useKey() {
    const cacheKey = useCacheKey();
    return (...rest: readonly unknown[]) => moreKey(cacheKey, "computer", ...rest);
}

/** Ready, downloading, starting or asleep: often while it starts, else now
 *  and then. When it becomes ready, what it said while starting is read
 *  again. */
export function useStatus() {
    const source = useComputerSource();
    const cacheKey = useCacheKey();
    const key = useKey();
    const focused = useIsFocused();
    const cache = useQueryClient();
    const result = useQuery({
        queryKey: key("status"),
        queryFn: () => source.status(),
        enabled: focused,
        refetchInterval: (query) => (focused ? statusEvery(query.state.data) : false),
        retry: false,
    });
    const previous = useRef<string | undefined>(undefined);
    const state = result.data?.state;
    useEffect(() => {
        if (state === "ready" && previous.current && previous.current !== "ready") {
            for (const part of ["files", "browser", "logins"]) void cache.invalidateQueries({ queryKey: moreKey(cacheKey, "computer", part) });
        }
        previous.current = state;
    }, [state, cache, cacheKey]);
    return result;
}

/** One folder, paged by the server's own cursor, ambient until `wake`. */
export function useFiles(path: string | null, wake: boolean, enabled: boolean) {
    const source = useComputerSource();
    const key = useKey();
    const focused = useIsFocused();
    return useInfiniteQuery({
        queryKey: key("files", path ?? "", wake),
        initialPageParam: undefined as string | undefined,
        queryFn: ({ pageParam }) => source.files(path, wake, pageParam),
        /* The server's order. The screen sorts folders first for reading and
           must not sort this. */
        getNextPageParam: (page) => page.next_after || undefined,
        /* The last folder's answer stands while the next is read, so
           walking somewhere is not "checking" the computer all over again.
           The rows know it is a placeholder and do not show it. */
        placeholderData: keepPreviousData,
        enabled: focused && enabled,
        staleTime: 5_000,
        retry: false,
    });
}

/** Whether there is a browser to watch. Never starts anything. */
export function useBrowserState(enabled: boolean) {
    const source = useComputerSource();
    const key = useKey();
    const focused = useIsFocused();
    return useQuery({
        queryKey: key("browser"),
        queryFn: () => source.browser(),
        enabled: focused && enabled,
        staleTime: 15_000,
        refetchInterval: focused && enabled ? 15_000 : false,
        retry: false,
    });
}

/** The folder a chat works in, when the computer was opened from one. */
export function useChatFolder(podId: string | null, conversationId: string | null) {
    const source = useComputerSource();
    const key = useKey();
    return useQuery({
        queryKey: key("chat-folder", podId ?? "", conversationId ?? ""),
        queryFn: () => source.chatFolder(podId!, conversationId!),
        enabled: Boolean(podId && conversationId),
        staleTime: 5 * 60_000,
        retry: false,
    });
}

/** A file's real size: what is read of it is capped, so only a stat knows. */
export function useFileStat(path: string) {
    const source = useComputerSource();
    const key = useKey();
    return useQuery({
        queryKey: key("stat", path),
        queryFn: () => source.stat(path),
        staleTime: 30_000,
        retry: false,
    });
}

export type FileBody =
    | { kind: "text"; text: string; bytes: number }
    | { kind: "image"; uri: string; bytes: number }
    | { kind: "too-large"; bytes: number }
    | { kind: "other" };

/** What an opened file shows: its text, its picture (downloaded into the
 *  cache with the token in a native header), or that it is too large. Only
 *  while it is open; reading is what wakes the computer. */
export function useFileBody(path: string, name: string) {
    const source = useComputerSource();
    const key = useKey();
    const viewer = viewerOf(name);
    return useQuery({
        queryKey: key("file", path),
        queryFn: async (): Promise<FileBody> => {
            if (viewer === "other") return { kind: "other" };
            const limit = readLimit(viewer);
            if (viewer === "image") {
                const got = await source.download(path, limit);
                return tooLargeToShow(viewer, got.bytes) ? { kind: "too-large", bytes: got.bytes } : { kind: "image", uri: got.uri, bytes: got.bytes };
            }
            const got = await source.text(path, limit);
            return tooLargeToShow(viewer, got.bytes) ? { kind: "too-large", bytes: got.bytes } : { kind: "text", text: got.text, bytes: got.bytes };
        },
        /* Not kept once the file is closed: a picture is a file in the
           cache, deleted with it. */
        gcTime: 0,
        retry: false,
    });
}

/** Save: the whole file (the server's one read) into the cache, then the
 *  share sheet. A file past that is too large to save on a phone. */
export function useSaveFile() {
    const source = useComputerSource();
    return useMutation({
        mutationFn: async ({ path, sizeBytes }: { path: string; sizeBytes: number | null }) => {
            if (saving(sizeBytes) === "too-large") throw new Error(TOO_LARGE_TO_SAVE);
            const got = await source.download(path, SAVE_LIMIT);
            /* A full read may be a cut one: the file grew past it. */
            if (got.bytes >= SAVE_LIMIT && got.bytes !== sizeBytes) throw new Error(TOO_LARGE_TO_SAVE);
            await shareFile(got.uri);
        },
    });
}

/** The same browser in the phone's own browser, through a grant made for
 *  this: a signed address that expires in half an hour, never the app's own
 *  tokens. Asking for it starts the computer. */
export function useOpenInBrowser() {
    const source = useComputerSource();
    return useMutation({
        mutationFn: async () => {
            const grant = await source.browserAccess();
            await WebBrowser.openBrowserAsync(grant.url);
        },
    });
}

/** The sites the browser holds cookies for, ambient until `wake`. */
export function useLogins(wake: boolean, enabled: boolean) {
    const source = useComputerSource();
    const key = useKey();
    const focused = useIsFocused();
    return useQuery({
        queryKey: key("logins", wake),
        queryFn: () => source.logins(wake),
        enabled: focused && enabled,
        staleTime: 10_000,
        retry: false,
    });
}

export function useSignOutOf() {
    const source = useComputerSource();
    const key = useKey();
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (site: string) => source.signOutOf(site),
        onSuccess: () => void cache.invalidateQueries({ queryKey: key("logins") }),
    });
}

/** What a sign-in is asking for. */
export function useSignIn(conversationId: string, toolCallId: string) {
    const source = useComputerSource();
    const key = useKey();
    return useQuery({
        queryKey: key("sign-in", conversationId, toolCallId),
        queryFn: () => source.signIn(conversationId, toolCallId),
        retry: false,
        staleTime: 60_000,
    });
}

/** The one way back to the paused run: nothing notices a login by itself. */
export function useAnswerSignIn(conversationId: string, toolCallId: string) {
    const source = useComputerSource();
    const key = useKey();
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (signedIn: boolean) => source.answerSignIn(conversationId, toolCallId, signedIn),
        /* The sites have just gained one, and what needs the person has one
           fewer. The chat itself carries on over its own socket. */
        onSuccess: () => {
            void cache.invalidateQueries({ queryKey: key("logins") });
            void cache.invalidateQueries({ predicate: (query) => query.queryKey.includes("pulse") });
        },
    });
}

/** Where a sign-in's browser really is, every 1.5 s while it is asked. */
export function useCurrentPage(origin: string | null, enabled: boolean) {
    const source = useComputerSource();
    const key = useKey();
    const focused = useIsFocused();
    return useQuery({
        queryKey: key("page", origin ?? ""),
        queryFn: () => source.currentPage(origin!),
        enabled: focused && enabled && Boolean(origin),
        refetchInterval: focused && enabled ? 1_500 : false,
        staleTime: 0,
        retry: false,
    });
}

/** Ask the display to match the box the picture is drawn in. A failure is
 *  not the person's: they keep the picture they had. */
export function useFitDisplay() {
    const source = useComputerSource();
    return useMutation({ mutationFn: (size: { width: number; height: number }) => source.fitDisplay(size) });
}
