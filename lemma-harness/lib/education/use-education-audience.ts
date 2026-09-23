'use client';

import { usePathname } from 'next/navigation';

import { usePodAccess } from '@/lib/hooks/use-pod-access';

/** Pull the pod id out of a `/pod/<id>/...` path, if we're inside a pod. */
export function usePodIdFromPath(): string | undefined {
    const pathname = usePathname();
    const match = pathname?.match(/\/pod\/([^/?#]+)/);
    return match?.[1];
}

/**
 * Education (primers, tours, inline hints, the first-win checklist) is for the
 * people who build a pod: admins and editors. Operators — teammates who only
 * work in an app — don't need "hire your first agent" or "scope what this
 * agent can touch". This gates all of that on builder access.
 *
 * Defaults to hidden while permissions are still loading so an operator never
 * sees a flash of builder education.
 */
export function useEducationEnabled(): boolean {
    const podId = usePodIdFromPath();
    const access = usePodAccess(podId);
    if (!podId || access.isLoading) return false;
    return access.isBuilder;
}
