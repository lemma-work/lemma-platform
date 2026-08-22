import type { Metadata } from 'next';
import { RootPageSwitch } from '@/components/root/root-page-switch';
import { JsonLd } from '@/components/seo/json-ld';
import { SITE_DESCRIPTION, SITE_TITLE } from '@/lib/seo/site-copy';
import {
    organizationSchema,
    softwareApplicationSchema,
    webSiteSchema,
} from '@/lib/seo/structured-data';

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
    return (
        <>
            {/*
              The site's root entities are declared once, here, rather than in
              the root layout — the layout also wraps every signed-in pod screen,
              and those pages are `disallow`ed in robots.ts. Publisher and
              website identity belong on the page a crawler is actually allowed
              to read.
            */}
            <JsonLd schema={[organizationSchema(), webSiteSchema(), softwareApplicationSchema()]} />
            <RootPageSwitch />
        </>
    );
}
