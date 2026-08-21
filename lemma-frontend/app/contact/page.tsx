import type { Metadata } from 'next';

import { SiteFooter, SiteHeader } from '@/components/landing/site-chrome';
import { JsonLd } from '@/components/seo/json-ld';
import { contactPage } from '@/lib/data/company-pages';
import { breadcrumbSchema } from '@/lib/seo/structured-data';
import { socialCardPath } from '@/lib/share/social-card';

const image = socialCardPath({
    variant: 'build',
    title: contactPage.title,
    detail: contactPage.description,
    label: 'lemma.work/contact',
});

export const metadata: Metadata = {
    title: 'Contact',
    description: contactPage.description,
    alternates: { canonical: '/contact' },
    openGraph: {
        title: contactPage.title,
        description: contactPage.description,
        type: 'website',
        images: [{ url: image, width: 1200, height: 630, alt: contactPage.title }],
    },
    twitter: {
        card: 'summary_large_image',
        title: contactPage.title,
        description: contactPage.description,
        images: [image],
    },
};

export default function ContactPage() {
    return (
        <>
            <JsonLd
                schema={breadcrumbSchema([
                    { name: 'Lemma', path: '/' },
                    { name: 'Contact' },
                ])}
            />
            <div className="lp-react content-page">
                <SiteHeader hashPrefix="/" showThemeToggle />
                <div className="content-grid content-grid-single content-grid-narrow">
                    <div className="content-column">
                        <header className="content-masthead">
                            <p className="content-eyebrow">Contact</p>
                            <h1 className="content-title">{contactPage.title}</h1>
                            <p className="content-dek">{contactPage.description}</p>
                        </header>

                        <div className="content-article">
                            {contactPage.sections.map((section) => (
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
