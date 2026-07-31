'use client';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider as NextThemesProvider } from 'next-themes';
import { useState, type ReactNode } from 'react';
import { usePathname } from 'next/navigation';
import { Toaster } from 'sonner';
import { OrganizationProvider } from '@/components/dashboard/org-context';
import { SoundFeedbackRuntime } from '@/components/feedback/sound-feedback-runtime';

export function Providers({ children }: { children: ReactNode }) {
    const [queryClient] = useState(
        () =>
            new QueryClient({
                defaultOptions: {
                    queries: {
                        staleTime: 60 * 1000, // 1 minute
                        refetchOnWindowFocus: false,
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
                <SoundFeedbackRuntime />
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
