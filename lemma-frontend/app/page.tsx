import type { Metadata } from 'next';
import { RootPageSwitch } from '@/components/root/root-page-switch';

/**
 * The shared preview is the first thing anyone sees — tab title, Slack unfurl,
 * social card. It carries the same thesis as the hero, not a second one.
 */
const SITE_TITLE = "The software you need doesn't exist yet.";
const SITE_DESCRIPTION =
    'Your coding agent can write it. Lemma turns it into something your team can actually use — and run anywhere.';

export const metadata: Metadata = {
    title: 'Lemma — the runtime for agent-built software',
    description: SITE_DESCRIPTION,
    robots: {
        index: true,
        follow: true,
    },
    alternates: {
        canonical: '/',
    },
    openGraph: {
        title: SITE_TITLE,
        description: SITE_DESCRIPTION,
        type: 'website',
        url: '/',
        siteName: 'Lemma',
        images: [
            {
                url: '/api/social-card?variant=site',
                width: 1200,
                height: 630,
                alt: `${SITE_TITLE} ${SITE_DESCRIPTION}`,
            },
        ],
    },
    twitter: {
        card: 'summary_large_image',
        title: SITE_TITLE,
        description: SITE_DESCRIPTION,
        images: ['/api/social-card?variant=site'],
    },
};

export default function HomePage() {
    return <RootPageSwitch />;
}
