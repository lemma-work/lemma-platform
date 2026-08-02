'use client';

import { QueryClient, QueryClientProvider, keepPreviousData } from '@tanstack/react-query';
import { ThemeProvider as NextThemesProvider } from 'next-themes';
import { useState, type ReactNode } from 'react';
import { usePathname } from 'next/navigation';
import { Toaster } from 'sonner';
import { OrganizationProvider } from '@/components/dashboard/org-context';

export function Providers({ children }: { children: ReactNode }) {
    const [queryClient] = useState(
        () =>
            new QueryClient({
                defaultOptions: {
                    queries: {
                        staleTime: 60 * 1000, // 1 minute
                        refetchOnWindowFocus: false,
                        /**
                         * A changed key is a *different question about the same
                         * screen* — switch table, open folder, pick a filter,
                         * page forward. Without this the query drops to
                         * `isLoading`, the screen unmounts what it was showing,
                         * and a full skeleton flashes for content that is
                         * usually 150ms away and laid out identically.
                         *
                         * Keeping the previous data means the region stays put
                         * and dims (`isFetching` → `isRefreshing`) instead.
                         * First loads are unaffected: there is nothing previous
                         * to keep, so they still get the skeleton.
                         */
                        placeholderData: keepPreviousData,
                    },
                },
            })
    );
    const pathname = usePathname();
    const isAuthRoute = pathname.startsWith('/auth') || pathname === '/login' || pathname === '/signup' || pathname === '/logout';
    const skipAppProviders = isAuthRoute;

    const appTree = skipAppProviders ? (
        children
    ) : (
        <OrganizationProvider>
            {children}
        </OrganizationProvider>
    );

    return (
        <NextThemesProvider
            attribute="class"
            defaultTheme="system"
            enableSystem
            disableTransitionOnChange
        >
            <QueryClientProvider client={queryClient}>
                {appTree}
                <Toaster
                    position="bottom-right"
                    closeButton
                    offset={18}
                    toastOptions={{
                        duration: 4200,
                        classNames: {
                            toast: 'lemma-toast',
                            title: 'lemma-toast-title',
                            description: 'lemma-toast-description',
                            icon: 'lemma-toast-icon',
                            closeButton: 'lemma-toast-close',
                            success: 'lemma-toast-success',
                            error: 'lemma-toast-error',
                            warning: 'lemma-toast-warning',
                            info: 'lemma-toast-info',
                        },
                    }}
                />
            </QueryClientProvider>
        </NextThemesProvider>
    );
}
