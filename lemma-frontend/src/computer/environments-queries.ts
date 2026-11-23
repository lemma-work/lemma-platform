import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { lemma } from "@/session/client";
import { live } from "@/usage/queries";
import type { Listing } from "./machine";
import type { WebLogin } from "./queries";
import {
    pathRef, type Machine, type MachinePolicy, type MachineProcess, type MachineTemplate, type Preview,
    type ResetResult, type ResetScope,
} from "./environments";

/** The `/machines` calls the Environments screen makes.
 *
 *  Paths written out once here, as `queries.ts` does for the calls the SDK
 *  has no namespace for. The same cost rule holds: a list is ambient and never
 *  wakes a machine; anything that does is a click.
 */

const ROOT = ["machines"] as const;

function machinePath(ref: string, rest = ""): string {
    return `/machines/${pathRef(ref)}${rest}`;
}

export function useMachines(podId: string, enabled: boolean) {
    return useQuery({
        queryKey: [...ROOT, "list", podId],
        queryFn: async () => (await lemma().request<{ machines?: Machine[] }>(
            "GET", "/machines", { params: { pod_id: podId } },
        ))?.machines ?? [],
        enabled: live() && enabled,
        staleTime: 10_000,
        refetchInterval: enabled ? 30_000 : false,
        refetchOnWindowFocus: true,
        retry: false,
    });
}

export function useMachinePolicy(podId: string, enabled: boolean) {
    return useQuery({
        queryKey: [...ROOT, "policy", podId],
        queryFn: () => lemma().request<MachinePolicy>("GET", "/machines:policy", { params: { pod_id: podId } }),
        enabled: live() && enabled,
        staleTime: 5 * 60_000,
        retry: false,
    });
}

export interface NewPodMachine {
    name: string;
    template: MachineTemplate;
    size: string;
    visibility: "pod" | "restricted";
}

export function useCreatePodMachine(podId: string) {
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (body: NewPodMachine) => lemma().request<Machine>(
            "POST", `/pods/${encodeURIComponent(podId)}/machines`, { body },
        ),
        onSuccess: () => { void cache.invalidateQueries({ queryKey: [...ROOT, "list", podId] }); },
    });
}

export function useDeletePodMachine(podId: string) {
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (name: string) => lemma().request<unknown>(
            "DELETE", `/pods/${encodeURIComponent(podId)}/machines/${encodeURIComponent(name)}`,
        ),
        onSuccess: () => { void cache.invalidateQueries({ queryKey: [...ROOT, "list", podId] }); },
    });
}

export function useProcesses(ref: string, enabled: boolean) {
    return useQuery({
        queryKey: [...ROOT, ref, "processes"],
        queryFn: async () => (await lemma().request<{ processes?: MachineProcess[] }>(
            "GET", machinePath(ref, "/processes"),
        ))?.processes ?? [],
        enabled: live() && enabled,
        refetchInterval: enabled ? 5_000 : false,
        retry: false,
    });
}

export function killProcess(ref: string, processId: string): Promise<unknown> {
    return lemma().request<unknown>("DELETE", machinePath(ref, `/processes/${encodeURIComponent(processId)}`));
}

export function useKillProcess(ref: string) {
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (processId: string) => killProcess(ref, processId),
        onSettled: () => { void cache.invalidateQueries({ queryKey: [...ROOT, ref, "processes"] }); },
    });
}

export function usePreviews(ref: string, enabled: boolean) {
    return useQuery({
        queryKey: [...ROOT, ref, "previews"],
        queryFn: async () => (await lemma().request<{ previews?: Preview[] }>(
            "GET", machinePath(ref, "/previews"),
        ))?.previews ?? [],
        enabled: live() && enabled,
        staleTime: 10_000,
        retry: false,
    });
}

export function useCreatePreview(ref: string) {
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (body: { port: number; visibility: "private" | "link"; ttl_seconds?: number }) =>
            lemma().request<Preview>("POST", machinePath(ref, "/previews"), { body }),
        onSuccess: () => { void cache.invalidateQueries({ queryKey: [...ROOT, ref, "previews"] }); },
    });
}

export function useRevokePreview(ref: string) {
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (previewId: string) => lemma().request<Preview>(
            "DELETE", machinePath(ref, `/previews/${encodeURIComponent(previewId)}`),
        ),
        onSuccess: () => { void cache.invalidateQueries({ queryKey: [...ROOT, ref, "previews"] }); },
    });
}

/** One directory of any machine. Ambient unless `wake` — see `useFiles`. */
export function useMachineFiles(ref: string, path: string | null, wake: boolean, enabled: boolean) {
    return useQuery({
        queryKey: [...ROOT, ref, "files", path ?? "", wake],
        queryFn: () => lemma().request<Listing>("GET", machinePath(ref, "/files"), {
            params: { path: path ?? undefined, wake: wake || undefined },
        }),
        enabled: live() && enabled,
        staleTime: 5_000,
        retry: false,
    });
}

export function useMachineLogins(ref: string, wake: boolean, enabled: boolean) {
    return useQuery({
        queryKey: [...ROOT, ref, "logins", wake],
        queryFn: () => lemma().request<{ items?: WebLogin[]; sleeping?: boolean }>(
            "GET", machinePath(ref, "/browser/logins"), { params: { wake: wake || undefined } },
        ),
        enabled: live() && enabled,
        staleTime: 10_000,
        retry: false,
    });
}

export function useForgetMachineLogin(ref: string) {
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (site: string) => lemma().request<{ site: string; forgotten: boolean }>(
            "DELETE", machinePath(ref, `/browser/logins/${encodeURIComponent(site)}`),
        ),
        onSuccess: () => { void cache.invalidateQueries({ queryKey: [...ROOT, ref, "logins"] }); },
    });
}

export function useResetMachine(ref: string, podId: string) {
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (body: { scopes: ResetScope[]; confirm?: boolean; forget_logins?: boolean }) =>
            lemma().request<{ results?: ResetResult[] }>("POST", machinePath(ref, ":reset"), { body }),
        onSettled: () => {
            void cache.invalidateQueries({ queryKey: [...ROOT, ref] });
            void cache.invalidateQueries({ queryKey: [...ROOT, "list", podId] });
        },
    });
}
