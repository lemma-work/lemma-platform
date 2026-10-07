"use client";

import { useQuery } from "@tanstack/react-query";
import { source } from "@/data";

/** The file's link in Lemma, for whatever offers to copy or share it.
 *
 *  `readFile` carries it for media and downloads, which fetch the signed link
 *  anyway; text read inline comes back without one, and only the few places
 *  that hand the link out pay to ask. */
export function useFileAppUrl(podId: string, path: string, known: string | undefined, ready = true): string | undefined {
    const asked = useQuery({
        queryKey: ["file-url", podId, path],
        queryFn: () => source.fileAppUrl(podId, path),
        enabled: Boolean(ready && path && !known),
        staleTime: 5 * 60_000,
    });
    return known ?? asked.data ?? undefined;
}
