'use client';

import type { Dispatch, ReactNode, SetStateAction } from 'react';
import { createContext, useContext } from 'react';

import type { PodTopbarTitleOwner } from '@/lib/pods/topbar-title';

export type { PodTopbarTitleOwner };

export type PodTopbarState = {
    title?: ReactNode;
    /**
     * Label for this route's workspace tab. Defaults to `title`.
     *
     * Kept separate so the tab stays stable while the bar title fades in and out
     * under `titleOwner: 'page'` — and so a route can give the tab a shorter
     * label than the one the bar shows.
     */
    tabTitle?: string;
    /** Which band prints the name — see `barOwnsTitle`. Defaults to `'bar'`. */
    titleOwner?: PodTopbarTitleOwner;
    /** Rendered before the title in the context bar. */
    icon?: ReactNode;
    backHref?: string;
    backLabel?: string;
    eyebrow?: ReactNode;
    meta?: ReactNode;
    switcher?: ReactNode;
    tabs?: ReactNode;
    actions?: ReactNode;
    fullscreen?: boolean;
};

type PodTopbarContextValue = {
    setTopbar: Dispatch<SetStateAction<PodTopbarState>>;
    /**
     * Reported by `ResourceHeroTitle` as its heading enters and leaves the
     * viewport, and set back to `false` when that heading unmounts — which is
     * what hands the title back to the bar on a route or tab with no hero.
     */
    setHeroTitleVisible: (visible: boolean) => void;
};

const PodTopbarContext = createContext<PodTopbarContextValue | null>(null);

export function PodTopbarProvider({
    value,
    children,
}: {
    value: PodTopbarContextValue;
    children: ReactNode;
}) {
    return (
        <PodTopbarContext.Provider value={value}>
            {children}
        </PodTopbarContext.Provider>
    );
}

export function usePodTopbar() {
    return useContext(PodTopbarContext);
}
