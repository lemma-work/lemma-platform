import { useQuery } from "@tanstack/react-query";
import { useCacheKey } from "../../../data/hooks";
import { adminKey, useAdminSource } from "../hooks";
import type { AboutLine } from "../types";
import { memoryLine, readNotes } from "./model";

/** This part's reads, through react-query. */
export function useMemorySource() {
    return useAdminSource().memory;
}

/** A Lemma's notes, shared by the screen and its line on the Settings tab,
 *  so drawing both costs one walk of the folders. */
export function useMemoryNotes(podId: string) {
    const source = useMemorySource();
    const key = useCacheKey();
    return useQuery({
        queryKey: adminKey(key, "memory", podId),
        queryFn: () => readNotes((directory, page) => source.list(podId, directory, page)),
        staleTime: 60_000,
    });
}

/** The row's live line on a Lemma's Settings tab: "12 notes · 3 this week". */
export function useMemoryLine(podId: string): AboutLine {
    const notes = useMemoryNotes(podId);
    return memoryLine(notes.isSuccess ? notes.data : null);
}
