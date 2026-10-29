import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { lemma } from "@/session/client";
import { live } from "@/usage/queries";
import type { MachineConsent } from "./consent-model";

/** Letting an app run commands on your own machine.
 *
 *  An app acts as whoever is looking at it, so without this any page somebody
 *  opened could run commands as them. The first time one asks it is refused
 *  with `MACHINE_CONSENT_REQUIRED`, and the SDK hands it this app's
 *  `/consent/machine?app=<id>` to open. The answer is given here, in Lemma —
 *  the API refuses a grant whose origin is an app's own host, or the app could
 *  simply say yes for the person. */

export function useMachineConsents(enabled = true) {
    return useQuery({
        queryKey: ["machine-consents"],
        queryFn: () => lemma().machines.consents.list() as Promise<MachineConsent[]>,
        enabled: live() && enabled,
        staleTime: 30_000,
        retry: false,
    });
}

export function useGrantMachineConsent() {
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (appId: string) => lemma().machines.consents.grant(appId) as Promise<MachineConsent>,
        onSuccess: () => { void cache.invalidateQueries({ queryKey: ["machine-consents"] }); },
    });
}

export function useRevokeMachineConsent() {
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (appId: string) => lemma().machines.consents.revoke(appId),
        /* Read back rather than dropped locally: a row removed here and still
           granted on the server would come back on the next visit. */
        onSettled: () => { void cache.invalidateQueries({ queryKey: ["machine-consents"] }); },
    });
}
