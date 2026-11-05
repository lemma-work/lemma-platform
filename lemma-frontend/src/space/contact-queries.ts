"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import { SEE_CONTACTS } from "@/data/pod-roles";
import { lemma } from "@/session/client";
import { isLandingPreview } from "@/marketing/preview-mode";
import type { WidgetAnswer, WidgetDraft } from "@/data/contacts";

/** The keys every view of a space's contacts reads under. */
export const contactsKey = (podId: string) => ["contacts", podId] as const;
export const widgetsKey = (podId: string) => ["web-widgets", podId] as const;
export const reachKey = (podId: string) => ["contact-reach", podId] as const;
export const capKey = (orgId: string) => ["contacts-cap", orgId] as const;

/** Whether the signed-in person may see this space's contacts at all.
 *
 *  Asked of `podPermissions.me`, under the key the rest of the app asks it
 *  with, rather than read off a role. Unknown counts as no, so the place is
 *  not offered while the question is in flight. The sample source and the
 *  landing's preview have no server to ask, and show everything. */
export function useMaySeeContacts(podId: string): boolean {
    const offline = isLandingPreview() || source.label === "sample";
    const permissions = useQuery({
        queryKey: ["pod-permissions", podId],
        queryFn: () => lemma(podId).podPermissions.me(podId),
        enabled: !offline,
        staleTime: 60_000,
    });
    if (offline) return true;
    const held = permissions.data?.actions ?? [];
    return SEE_CONTACTS.some((action) => held.includes(action));
}

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

/** A table's own columns, the ones a member could show contacts. */
export function useShareableColumns(podId: string, table: string | null) {
    return useQuery({
        queryKey: ["contact-columns", podId, table] as const,
        enabled: table !== null,
        queryFn: async () =>
            (await source.tableColumns(podId, table ?? "")).filter((column) => !column.system && column.name !== "contact_id").map((column) => column.name),
    });
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
        mutationFn: ({ contactId, message, handle, clientMessageId }: { contactId: string; message: string; handle: { kind: string; value: string }; clientMessageId: string }) =>
            source.followUpContact(podId, contactId, message, handle, clientMessageId),
    });
}

export const contactSettingsKey = (podId: string) => ["contact-settings", podId] as const;

/** The pod's line said once to a contact who writes while their channel's
 *  agent is off (`PodConfig.contacts.auto_acknowledge`), or null for none. */
export function useAutoAcknowledge(podId: string) {
    const offline = isLandingPreview() || source.label === "sample";
    return useQuery({
        queryKey: contactSettingsKey(podId),
        enabled: !offline,
        staleTime: 60_000,
        queryFn: async () => {
            const pod = await lemma(podId).pods.get(podId);
            const said = (pod.config as { contacts?: { auto_acknowledge?: unknown } | null } | undefined)?.contacts?.auto_acknowledge;
            return typeof said === "string" && said.trim() ? said : null;
        },
    });
}

export function useSetAutoAcknowledge(podId: string) {
    const cache = useQueryClient();
    return useMutation({
        /* Only this key goes up: the config is merged field-wise. */
        mutationFn: async (said: string | null) => {
            await lemma(podId).pods.update(podId, { config: { contacts: { auto_acknowledge: said } } } as Parameters<ReturnType<typeof lemma>["pods"]["update"]>[1]);
            return said;
        },
        onSuccess: (said) => cache.setQueryData(contactSettingsKey(podId), said),
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
        mutationFn: (change: { table: string; on: boolean; columns?: string[] } | { fn: string; on: boolean }) =>
            "table" in change
                ? source.setTableContactOwned(podId, change.table, change.on, change.columns)
                : source.setFunctionContactsInvoke(podId, change.fn, change.on),
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
