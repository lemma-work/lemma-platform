"use client";

import { useCallback, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { lemma } from "@/session/client";
import {
    findQuery,
    RESOURCE_KEY,
    resourceInstructions,
    resourceKey,
    resourceTitle,
    type ResourceKind,
} from "./resource-conversation";

/** Open the conversation a resource carries, making it the first time.
 *
 *  Find-or-create, and the find is a real server filter rather than a scan:
 *  `?metadata.lemma_resource=table:invoices` is parsed straight off the query
 *  string by the list endpoint, so this is one request whether the pod holds
 *  three conversations or three thousand.
 *
 *  Created lazily on purpose. A pod with forty tables should not have forty
 *  empty conversations in it because somebody once opened the library — the
 *  conversation begins existing when there is something to say.
 */
export function useResourceConversation(podId: string) {
    const cache = useQueryClient();
    const [opening, setOpening] = useState<string | null>(null);
    const [problem, setProblem] = useState<string | null>(null);

    const open = useCallback(
        async (kind: ResourceKind, name: string): Promise<string | null> => {
            const client = lemma(podId);
            setOpening(resourceKey(kind, name));
            setProblem(null);
            try {
                const found = await client.request<{ items?: { id: string }[] }>(
                    "GET",
                    `/pods/${podId}/conversations`,
                    { params: findQuery(kind, name) },
                );
                const existing = found?.items?.[0]?.id;
                if (existing) return existing;

                const made = await client.conversations.create({
                    pod_id: podId,
                    title: resourceTitle(kind, name),
                    type: "PROJECT",
                    /* Told once, at creation. The alternative is prefixing every
                       message with what it is about, which the person would
                       have to read back in their own transcript forever. */
                    instructions: resourceInstructions(kind, name),
                    metadata: { [RESOURCE_KEY]: resourceKey(kind, name) },
                } as Parameters<typeof client.conversations.create>[0]);

                /* The history panel lists CHAT conversations, so a new PROJECT
                   one does not belong in that cache — but the panel also shows
                   whichever conversation is open, and it reads that list to
                   name it. Refreshing keeps the two from disagreeing. */
                void cache.invalidateQueries({ queryKey: ["conversations", podId] });
                return made.id;
            } catch (failure) {
                setProblem(
                    failure instanceof Error
                        ? failure.message
                        : "That conversation could not be opened.",
                );
                return null;
            } finally {
                setOpening(null);
            }
        },
        [podId, cache],
    );

    return { open, opening, problem, clearProblem: () => setProblem(null) };
}
