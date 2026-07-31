'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { ArrowRight } from '@/components/ui/icons';

import { Button } from '@/components/ui/button';
import { useLemmaAuth } from '@/lib/hooks/use-lemma-auth';

interface ShareLandingProps {
    /** Workspace-relative path, already validated as `/pod/…` on the server. */
    destination: string;
    name: string | null;
    /** Reads after the name: "an agent on Lemma". */
    article: string;
    detail: string;
    cardPath: string;
}

/**
 * What a shared link opens.
 *
 * The markup is server-rendered so a crawler sees the name and the card without
 * running anything. People who already have a session never dwell here — the
 * effect below sends them straight to the workspace, so a teammate clicking a
 * shared link lands where they expected to.
 */
export function ShareLanding({ destination, name, article, detail, cardPath }: ShareLandingProps) {
    const router = useRouter();
    const { isAuthenticated, isLoading } = useLemmaAuth();

    useEffect(() => {
        if (isLoading || !isAuthenticated) return;
        router.replace(destination);
    }, [isAuthenticated, isLoading, destination, router]);

    return (
        <main className="flex min-h-dvh items-center justify-center px-6 py-16">
            <div className="w-full max-w-xl">
                <div className="overflow-hidden rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface-2)] shadow-[var(--shadow-md)]">
                    {/* eslint-disable-next-line @next/next/no-img-element -- a dynamic route response, not a static asset for the image optimizer. */}
                    <img
                        src={cardPath}
                        alt={name ? `${name} on Lemma` : 'On Lemma'}
                        width={1200}
                        height={630}
                        className="block h-auto w-full"
                    />
                </div>

                <div className="mt-6 text-center">
                    <h1 className="text-xl font-medium text-[var(--text-primary)]">
                        {name || 'Shared on Lemma'}
                    </h1>
                    <p className="mt-1 text-sm text-[var(--text-secondary)]">
                        {name ? `${name} is ${article} on Lemma. ` : ''}
                        {detail}
                    </p>
                </div>

                <div className="mt-6 flex flex-col items-center gap-3">
                    <Button asChild size="lg" className="gap-2">
                        <Link href={destination} prefetch={false}>
                            {isAuthenticated ? 'Open it' : 'Sign in to open'}
                            <ArrowRight className="h-4 w-4" />
                        </Link>
                    </Button>
                    <p className="text-xs text-[var(--text-tertiary)]">
                        You need access to the pod this belongs to.
                    </p>
                </div>

                <p className="mt-10 text-center text-xs text-[var(--text-tertiary)]">
                    <Link href="/" className="hover:text-[var(--text-secondary)]">
                        Lemma — run your apps and agents, with your team
                    </Link>
                </p>
            </div>
        </main>
    );
}
