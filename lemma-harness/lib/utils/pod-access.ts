import type { AppPageRef } from '@/lib/types/app';
import { getPodAccessMode } from '@/lib/authz/pod-permissions';

export type PodExperienceMode = 'builder' | 'consumer';

export function canManagePod(actions: string[] | null | undefined): boolean {
    return getPodAccessMode(actions) !== 'operator';
}

export function getPodExperienceMode(actions: string[] | null | undefined): PodExperienceMode {
    return canManagePod(actions) ? 'builder' : 'consumer';
}

export function getPodAppHref(podId: string, pages: AppPageRef[]): string {
    if (pages.length === 1) {
        return `/pod/${podId}/app/view?page=${encodeURIComponent(pages[0].slug)}`;
    }

    return `/pod/${podId}`;
}

export function shouldAutoOpenSingleApp(accessMode: PodExperienceMode, pages: AppPageRef[]): boolean {
    return accessMode === 'consumer' && pages.length === 1;
}
