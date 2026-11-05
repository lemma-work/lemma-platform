"use client";

import { useQuery } from "@tanstack/react-query";
import { source } from "@/data";
import { isLandingPreview } from "@/marketing/preview-mode";
import { lemma } from "@/session/client";
import { EVERYTHING, readAbilities, type Abilities } from "./abilities";

/** The reader's channel actions in this pod, asked of `podPermissions.me`
 *  under the key the rest of the app asks it with. The sample and the tour
 *  have no server to ask, and may do everything. */
export function useAbilities(podId: string): Abilities {
    const offline = isLandingPreview() || source.label === "sample";
    const permissions = useQuery({
        queryKey: ["pod-permissions", podId],
        queryFn: () => lemma(podId).podPermissions.me(podId),
        enabled: !offline,
        staleTime: 60_000,
    });
    if (offline) return EVERYTHING;
    return readAbilities(permissions.data?.actions);
}
