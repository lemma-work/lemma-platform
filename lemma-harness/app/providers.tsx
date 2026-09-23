'use client';

import { QueryClient, QueryClientProvider, keepPreviousData } from '@tanstack/react-query';
import { ThemeProvider as NextThemesProvider } from 'next-themes';
import { useState, type ReactNode } from 'react';
import { usePathname } from 'next/navigation';
import { isUnauthorized } from '@/lib/sdk/is-unauthorized';
import { Toaster } from 'sonner';
import { OrganizationProvider } from '@/components/dashboard/org-context';
import { AnalyticsProvider } from '@/components/analytics/analytics-provider';
import { AnalyticsIdentity } from '@/components/analytics/analytics-identity';
import { ConsentBanner } from '@/components/analytics/consent-banner';
import { useSandboxImageToasts } from '@/lib/desktop/sandbox-images';

export function Providers({ children }: { children: ReactNode }) {
    // Desktop only, and only while there is an answer that can still change.
    // A no-op in a browser, and on a warm install it never shows anything.
    useSandboxImageToasts();
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
                        /**
                         * Never retry a rejection. Retrying assumes the next
                         * attempt could be authorized, and by this point the
                         * session layer has already refreshed and retried on
                         * its own -- so a 401 here means that did not work.
                         *
                         * Retrying anyway multiplies it: react-query's default
                         * is three more attempts, each of which re-enters the
                         * refresh-and-retry path underneath. One unusable
                         * session became a sustained ~10 requests a second
                         * across every query a workspace screen makes, forever,
                         * because nothing in the stack treated "still 401 after
                         * a successful refresh" as an answer.
                         */
                        retry: (failureCount, error) =>
                            !isUnauthorized(error) && failureCount < 3,
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
            {/* Inside the org provider, unlike <AnalyticsProvider /> below:
                identity needs the active organization, and `useOrganization()`
                throws outside this tree. */}
            <AnalyticsIdentity />
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
                {/* Outside the auth-route skip: the landing and auth pages are
                    the top of the funnel, and dropping them there would lose
                    exactly the steps this measures. */}
                <AnalyticsProvider />
                <ConsentBanner />
                {appTree}
                {/* No close button: a toast dismisses itself, and a dismiss
                    control on a thing that leaves on its own is chrome for
                    nothing -- it also reserved a gutter across every toast in
                    the product to hold it. What can appear instead is one
                    action (Undo), which is the only reason to reach for a
                    toast rather than say nothing at all. */}
                <Toaster
                    position="bottom-right"
                    offset={18}
                    toastOptions={{
                        duration: 4200,
                        classNames: {
                            toast: 'lemma-toast',
                            title: 'lemma-toast-title',
                            description: 'lemma-toast-description',
                            icon: 'lemma-toast-icon',
                            actionButton: 'lemma-toast-action',
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
