"use client";

import { useQuery } from "@tanstack/react-query";
import { source } from "@/data";
import { listIfThere } from "@/thread/memory-notes";

/** The pages a Lemma holds: what its Pages tab lists and its header counts.
 *  A page is a `.md` file under /pages; a page with pages under it keeps
 *  them in a folder of the same name, beside it. */
export function useLemmaPages(podId: string) {
    return useQuery({
        queryKey: ["lemma-pages", podId],
        /* A Lemma nobody has written a page in has no /pages folder, and
           listing a folder that is not there is an error, not an empty list. */
        queryFn: async () => ((await listIfThere((directory, page) => source.listLibrary(podId, "files", directory, page), "/pages", 1)) ?? [])
            .filter((item) => item.kind !== "folder" && item.name.toLowerCase().endsWith(".md")),
        staleTime: 60_000,
        retry: false,
    });
}

/** The tables a Lemma holds, under the key its home reads them by. */
export function useLemmaTables(podId: string) {
    return useQuery({
        queryKey: ["outcome-tables", podId],
        queryFn: () => source.listLibrary(podId, "tables", "/"),
        staleTime: 5 * 60_000,
        retry: false,
        select: (listed) => listed.items.filter((item) => item.kind === "table"),
    });
}
