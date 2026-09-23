import type { Metadata } from 'next';
import { RootPageSwitch } from '@/components/root/root-page-switch';
import { hasSessionCookie } from '@/lib/auth/server-session';

export const metadata: Metadata = {
    title: 'Home | Lemma',
    robots: {
        index: false,
        follow: false,
        nocache: true,
    },
};

export default async function HomeRoutePage() {
    // Same reason as the root page: this route is `noindex`, so the marketing
    // page has nothing to earn here — it is only ever the placeholder someone
    // signed out would land on, and never what a signed-in visitor should read
    // for the length of a `/users/me` round trip.
    return <RootPageSwitch mode="home" hasSessionCookie={await hasSessionCookie()} />;
}
