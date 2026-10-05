"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import type { WidgetAnswer, WidgetDraft } from "@/data/contacts";

/** The keys every view of a space's contacts reads under. */
export const contactsKey = (podId: string) => ["contacts", podId] as const;
export const widgetsKey = (podId: string) => ["web-widgets", podId] as const;
export const reachKey = (podId: string) => ["contact-reach", podId] as const;
export const capKey = (orgId: string) => ["contacts-cap", orgId] as const;

export function useContacts(podId: string, enabled = true) {
    return useQuery({
        queryKey: contactsKey(podId),
        enabled,
        queryFn: () => source.listContacts(podId),
        staleTime: 30_000,
        refetchOnWindowFocus: true,
    });
}

export function useWidgets(podId: string, enabled = true) {
    return useQuery({ queryKey: widgetsKey(podId), enabled, queryFn: () => source.listWidgets(podId), staleTime: 60_000 });
}

export function useReach(podId: string, enabled = true) {
    return useQuery({ queryKey: reachKey(podId), enabled, queryFn: () => source.contactReach(podId), staleTime: 60_000 });
}

export function useContactsCap(orgId: string, enabled = true) {
    return useQuery({ queryKey: capKey(orgId), enabled, queryFn: () => source.contactsCap(orgId), staleTime: 60_000 });
}

export function useForgetContact(podId: string) {
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (contactId: string) => source.forgetContact(podId, contactId),
        onSuccess: () => void cache.invalidateQueries({ queryKey: contactsKey(podId) }),
    });
}

export function useRenameContact(podId: string) {
    const cache = useQueryClient();
    return useMutation({
        mutationFn: ({ contactId, name }: { contactId: string; name: string | null }) => source.renameContact(podId, contactId, name),
        onSuccess: () => void cache.invalidateQueries({ queryKey: contactsKey(podId) }),
    });
}

export function useFollowUp(podId: string) {
    return useMutation({
        mutationFn: ({ contactId, message }: { contactId: string; message: string }) => source.followUpContact(podId, contactId, message),
    });
}

export function useCreateWidget(podId: string) {
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (draft: WidgetDraft) => source.createWidget(podId, draft),
        onSuccess: () => void cache.invalidateQueries({ queryKey: widgetsKey(podId) }),
    });
}

export function useWidgetChange(podId: string) {
    const cache = useQueryClient();
    return useMutation({
        mutationFn: ({ widgetId, change }: { widgetId: string; change: { answer?: WidgetAnswer; origins?: string[] } }) =>
            source.updateWidget(podId, widgetId, change),
        onSuccess: () => void cache.invalidateQueries({ queryKey: widgetsKey(podId) }),
    });
}

export function useRemoveWidget(podId: string) {
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (widgetId: string) => source.deleteWidget(podId, widgetId),
        onSuccess: () => void cache.invalidateQueries({ queryKey: widgetsKey(podId) }),
    });
}

export function useReachChange(podId: string) {
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (change: { table: string; on: boolean } | { fn: string; on: boolean }) =>
            "table" in change ? source.setTableContactOwned(podId, change.table, change.on) : source.setFunctionContactsInvoke(podId, change.fn, change.on),
        onSuccess: () => void cache.invalidateQueries({ queryKey: reachKey(podId) }),
    });
}

export function useCapChange(orgId: string) {
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (limit: number | null) => source.setContactsCap(orgId, limit),
        onSuccess: (saved) => cache.setQueryData(capKey(orgId), saved),
    });
}
