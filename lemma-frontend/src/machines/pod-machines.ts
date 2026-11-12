import { useQuery } from "@tanstack/react-query";
import { lemma } from "@/session/client";
import { live } from "@/usage/queries";
import type { PodMachine } from "./machine-choices";

/** The machines a pod keeps. Listing never starts one. */
export function usePodMachines(podId: string, enabled = true) {
    return useQuery({
        queryKey: ["machines", podId],
        queryFn: () => lemma().machines.list({ podId }) as Promise<PodMachine[]>,
        enabled: live() && enabled && Boolean(podId),
        staleTime: 60_000,
        retry: false,
    });
}
