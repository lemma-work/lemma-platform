import type { Metadata } from 'next';
import { RootPageSwitch } from '@/components/root/root-page-switch';

export const metadata: Metadata = {
    title: 'Run It on Lemma',
    description: 'Run your apps and agents. Bring your team.',
    robots: {
        index: true,
        follow: true,
    },
    alternates: {
        canonical: '/',
    },
    openGraph: {
        title: 'Run it on Lemma.',
        description: 'Run your apps and agents. Bring your team.',
        type: 'website',
        url: '/',
        siteName: 'Lemma',
        images: [
            {
                url: '/api/social-card?variant=site',
                width: 1200,
                height: 630,
                alt: 'Run it on Lemma. Run your apps and agents. Bring your team.',
            },
        ],
    },
    twitter: {
        card: 'summary_large_image',
        title: 'Run it on Lemma.',
        description: 'Run your apps and agents. Bring your team.',
        images: ['/api/social-card?variant=site'],
    },
};

export default function HomePage() {
    return <RootPageSwitch />;
}
