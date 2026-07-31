import type { Metadata } from 'next';
import { notFound, redirect } from 'next/navigation';

import {
    getPublicTemplateBySlug,
    PUBLIC_TEMPLATES,
    templateRunHref,
} from '@/lib/templates/catalog';

interface TemplatePageProps {
    params: Promise<{ slug: string }>;
}

export function generateStaticParams() {
    return PUBLIC_TEMPLATES.map((template) => ({ slug: template.slug }));
}

export async function generateMetadata({ params }: TemplatePageProps): Promise<Metadata> {
    const { slug } = await params;
    const template = getPublicTemplateBySlug(slug);
    if (!template) return {};

    return {
        title: `Install ${template.name}`,
        description: template.description,
        robots: { index: false, follow: true },
    };
}

export default async function TemplatePage({ params }: TemplatePageProps) {
    const { slug } = await params;
    const template = getPublicTemplateBySlug(slug);
    if (!template) notFound();
    redirect(templateRunHref(template));
}
