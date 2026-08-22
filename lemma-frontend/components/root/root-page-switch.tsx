'use client';

import { useEffect, useSyncExternalStore } from 'react';
import dynamic from 'next/dynamic';
import { useRouter } from 'next/navigation';
import { PageLoader } from '@/components/brand/loader';
import { isLocalDeployment } from '@/lib/config';
import { useLemmaAuth } from '@/lib/hooks/use-lemma-auth';
import { useAccessiblePods } from '@/lib/hooks/use-pods';
import {
    readLastOpenedPodId,
    subscribeToLastOpenedPodId,
} from '@/lib/pods/last-opened-pod';

const DashboardHomePage = dynamic(() => import('@/components/home/dashboard-home-page'), {
    loading: () => <PageLoader />,
});
const LandingPage = dynamic(() => import('@/components/landing/landing-page'), {
    loading: () => <PageLoader />,
});
const AccountOnboarding = dynamic(
    () => import('@/components/onboarding/account-onboarding').then((module) => module.AccountOnboarding),
    { loading: () => <PageLoader /> }
);

type RootPageMode = 'redirect' | 'home';

export function RootPageSwitch({ mode = 'redirect' }: { mode?: RootPageMode }) {
    const { isAuthenticated, isLoading } = useLemmaAuth();

    // A local installation is not selling anything. The marketing page never
    // renders there, in any auth state, for any visitor — not the desktop
    // webview, not a phone on the same Wi-Fi, not someone holding a public
    // link. Everywhere else, it is also what a hosted visitor sees the moment
    // this resolves to "not signed in" — so it renders here too, while that
    // is still in flight. The auth check is a network round trip, which makes
    // this both the server render and what a crawler with no JavaScript sees:
    // real marketing copy, never a blank loading shell.
    if (!isLocalDeployment() && (isLoading || !isAuthenticated)) {
        return <LandingPage />;
    }

    if (isLoading) {
        return <PageLoader />;
    }

    if (!isAuthenticated) {
        return <LocalAuthRedirect />;
    }

    return (
        <AccountOnboarding
            preflightFallback={<PageLoader />}
            requireFirstPod={mode !== 'home'}
        >
            {mode === 'home' ? <DashboardHomePage /> : <AuthenticatedRootRedirect />}
        </AccountOnboarding>
    );
}

/**
 * The safety net, not the main path.
 *
 * The desktop shell already opens `/auth?show=signup` directly when no local
 * account exists, so this only catches the ways a local visitor lands on the
 * bare root instead: signing out, an expired session, or typing the LAN URL.
 * Signup is the right default for all three — an installation with an account
 * to sign into would not have sent them here.
 */
function LocalAuthRedirect() {
    const router = useRouter();

    useEffect(() => {
        router.replace('/auth?show=signup');
    }, [router]);

    return <PageLoader />;
}

function AuthenticatedRootRedirect() {
    const router = useRouter();
    const storedPodId = useSyncExternalStore(
        subscribeToLastOpenedPodId,
        readLastOpenedPodId,
        () => null,
    );
    const shouldFetchPods = !storedPodId;
    const { data: podsData, isLoading } = useAccessiblePods({ enabled: shouldFetchPods });

    useEffect(() => {
        if (storedPodId) {
            router.replace(`/pod/${storedPodId}?fromRoot=1`);
        }
    }, [router, storedPodId]);

    useEffect(() => {
        if (!shouldFetchPods || isLoading) return;

        const firstPod = podsData?.items?.[0];
        if (firstPod) {
            router.replace(`/pod/${firstPod.id}`);
            return;
        }

        // No pod this account can open. Someone who joined an organization as a
        // plain member has exactly this shape — they can read the org and
        // nothing in it — and this used to return the loader forever, which is
        // a dead end wearing a spinner. Home can at least say where they are.
        router.replace('/home');
    }, [isLoading, podsData?.items, router, shouldFetchPods]);

    return <PageLoader />;
}
