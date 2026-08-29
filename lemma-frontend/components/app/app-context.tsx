'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useState, ReactNode } from 'react';
import { useAppConfig } from '@/lib/hooks/use-app';
import type { AppPageRef } from '@/lib/types/app';

interface AppContextType {
    pages: AppPageRef[];
    isLoading: boolean;
    refresh: () => Promise<unknown>;
}

const AppContext = createContext<AppContextType | undefined>(undefined);

export function AppProvider({ children, podId }: { children: ReactNode; podId: string }) {
    const { data: config, isLoading, refetch } = useAppConfig(podId);

    const pages = useMemo(() => {
        const items = config?.pages || [];
        return [...items].sort((a, b) => a.order - b.order);
    }, [config?.pages]);

    const refresh = useCallback(() => refetch(), [refetch]);

    const value = useMemo(() => ({
        pages,
        isLoading,
        refresh,
    }), [pages, isLoading, refresh]);

    return (
        <AppContext.Provider value={value}>
            {children}
        </AppContext.Provider>
    );
}

export function useApp() {
    const context = useContext(AppContext);
    if (context === undefined) {
        throw new Error('useApp must be used within an AppProvider');
    }
    return context;
}

/**
 * The app page a slug names, and whether that answer is settled.
 *
 * A slug the index does not know is not the same thing as an app that does not
 * exist. The pod's app index is cached, and an agent that has just built an app
 * changes it from the outside — no mutation runs in this tab to invalidate it —
 * so the app the agent then presents is missing from a list fetched before it
 * existed. Asking the index once, on the first miss, is the difference between
 * showing the new app and telling its author it is unavailable.
 *
 * The refetch is per slug and happens once: a slug that is still absent
 * afterwards names an app that genuinely is not there, and asking again would
 * only spin.
 */
export function useAppPage(slug: string | null): { page: AppPageRef | null; isResolving: boolean } {
    const { pages, isLoading, refresh } = useApp();
    const page = slug ? pages.find((candidate) => candidate.slug === slug) ?? null : null;
    const [askedSlug, setAskedSlug] = useState<string | null>(null);
    const missing = !!slug && !page;

    useEffect(() => {
        if (!slug || !missing) return;

        // Once per slug: `missing` only falls when the answer arrives, so a slug
        // the index still does not know after this leaves the dependencies
        // unchanged and is not asked about again.
        let cancelled = false;
        void refresh().finally(() => {
            if (!cancelled) setAskedSlug(slug);
        });
        return () => { cancelled = true; };
    }, [missing, refresh, slug]);

    return {
        page,
        isResolving: !!slug && (isLoading || (missing && askedSlug !== slug)),
    };
}
