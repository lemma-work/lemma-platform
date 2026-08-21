import type { Metadata } from 'next';

import { SiteFooter, SiteHeader } from '@/components/landing/site-chrome';
import { JsonLd } from '@/components/seo/json-ld';
import { aboutPage } from '@/lib/data/company-pages';
import { breadcrumbSchema } from '@/lib/seo/structured-data';
import { socialCardPath } from '@/lib/share/social-card';

const image = socialCardPath({
    variant: 'build',
    title: aboutPage.title,
    detail: aboutPage.description,
    label: 'lemma.work/about',
});

export const metadata: Metadata = {
    title: 'About',
    description: aboutPage.description,
    alternates: { canonical: '/about' },
    openGraph: {
        title: aboutPage.title,
        description: aboutPage.description,
        type: 'website',
        images: [{ url: image, width: 1200, height: 630, alt: aboutPage.title }],
    },
    twitter: {
        card: 'summary_large_image',
        title: aboutPage.title,
        description: aboutPage.description,
        images: [image],
    },
};

export default function AboutPage() {
    return (
        <>
            <JsonLd
                schema={breadcrumbSchema([
                    { name: 'Lemma', path: '/' },
                    { name: 'About' },
                ])}
            />
            <div className="lp-react content-page">
                <SiteHeader hashPrefix="/" showThemeToggle />
                <div className="content-grid content-grid-single content-grid-narrow">
                    <div className="content-column">
                        <header className="content-masthead">
                            <p className="content-eyebrow">About</p>
                            <h1 className="content-title">{aboutPage.title}</h1>
                            <p className="content-dek">{aboutPage.description}</p>
                        </header>

                        <div className="content-article">
                            {aboutPage.sections.map((section) => (
                                <section key={section.title}>
                                    <h2>{section.title}</h2>
                                    {section.body ? <p>{section.body}</p> : null}
                                </section>
                            ))}
                        </div>
                    </div>
                </div>
                <SiteFooter hashPrefix="/" />
            </div>
        </>
    );
}
