import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCacheKey, useOrg } from "../../data/hooks";
import { modelChoices, type Choice } from "../../thread/runtimes";
import { useArea } from "../use-area";

/** The chat screens' reads and writes, through react-query. Every query is
 *  keyed with `useCacheKey()` first, so sample data and each server keep
 *  their own cache entries. */
export function useChatSource() {
    return useArea("chat");
}

/** A table's first page, or a query's answer, for a card in a chat. */
export function useRows(podId: string, name: string | undefined, sql: string | undefined) {
    const source = useChatSource();
    const key = useCacheKey();
    return useQuery({
        queryKey: sql ? [key, "chat-query", podId, sql] : [key, "chat-table", podId, name],
        queryFn: () => (sql ? source.runQuery(podId, sql) : source.tableRows(podId, name as string)),
        enabled: Boolean(sql || name),
        staleTime: 60_000,
        retry: false,
    });
}

export function useShownFile(podId: string, path: string) {
    const source = useChatSource();
    const key = useCacheKey();
    return useQuery({
        queryKey: [key, "chat-file", podId, path],
        queryFn: () => source.file(podId, path),
        /* A signed address lasts a while, not for ever. */
        staleTime: 5 * 60_000,
        retry: false,
    });
}

/** Several files at once, as a photo grid shows them: each read as
 *  `useShownFile` reads it, so a photo opened elsewhere is not read twice. */
export function useShownFiles(podId: string, paths: readonly string[]) {
    const source = useChatSource();
    const key = useCacheKey();
    return useQueries({
        queries: paths.map((path) => ({
            queryKey: [key, "chat-file", podId, path],
            queryFn: () => source.file(podId, path),
            staleTime: 5 * 60_000,
            retry: false,
        })),
    });
}

/** A document's first page, as a picture on this phone. */
export function useFirstPage(podId: string, path: string, enabled: boolean) {
    const source = useChatSource();
    const key = useCacheKey();
    return useQuery({
        queryKey: [key, "chat-first-page", podId, path],
        queryFn: () => source.firstPage(podId, path),
        enabled,
        staleTime: 30 * 60_000,
        retry: false,
    });
}

/** What the server read a document as, for a spreadsheet's rows. */
export function useConverted(podId: string, path: string, enabled: boolean) {
    const source = useChatSource();
    const key = useCacheKey();
    return useQuery({
        queryKey: [key, "chat-converted", podId, path],
        queryFn: () => source.converted(podId, path),
        enabled,
        staleTime: 5 * 60_000,
        retry: false,
    });
}

const SAVING_MODEL = ["chat-runtime", "save"];

/** The model a chat answers on, and the choice offered: the web's
 *  `ModelPicker` rule (`runtimes.ts`). Before the chat exists the choice is
 *  held by the screen (`picked`) and goes up with the conversation the
 *  first send creates. */
export function useModelPicker(podId: string, conversationId: string | null, agentId: string | null, picked: Choice | null) {
    const source = useChatSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const { org } = useOrg();
    const orgId = org?.id ?? null;
    const runtimes = useQuery({ queryKey: [key, "runtimes", orgId], queryFn: () => source.runtimes(orgId as string), enabled: Boolean(orgId), staleTime: 5 * 60_000 });
    const inherited = useQuery({ queryKey: [key, "default-runtime", orgId], queryFn: () => source.inheritedRuntime(orgId as string), enabled: Boolean(orgId), staleTime: 5 * 60_000 });
    const pod = useQuery({ queryKey: [key, "pod-runtime", podId], queryFn: () => source.podRuntime(podId), staleTime: 5 * 60_000 });
    const pinned = useQuery({ queryKey: [key, "agent-pins-runtime", podId, agentId], queryFn: () => source.agentPinsRuntime(podId, agentId as string), enabled: Boolean(agentId), staleTime: 5 * 60_000 });
    const pin = useQuery({ queryKey: [key, "chat-runtime", podId, conversationId], queryFn: () => source.conversationRuntime(podId, conversationId as string), enabled: Boolean(conversationId) });
    const save = useMutation({
        mutationKey: SAVING_MODEL,
        mutationFn: (next: Choice | null) => source.setConversationRuntime(podId, conversationId as string, next),
        onSuccess: (_answer, next) => {
            cache.setQueryData([key, "chat-runtime", podId, conversationId], next);
            void cache.invalidateQueries({ queryKey: [key, "chat-runtime", podId, conversationId] });
        },
    });
    const chosen = conversationId ? pin.data ?? null : picked;
    const offer = modelChoices(runtimes.data ?? [], pod.data ?? null, inherited.data ?? null, chosen);
    return {
        ...offer,
        /* An agent that names its own model: the picker would offer to change
           what that agent's configuration decides. */
        hidden: pinned.data === true,
        chosen,
        loading: runtimes.isPending,
        saving: save.isPending,
        failed: save.isError,
        save: save.mutate,
    };
}
