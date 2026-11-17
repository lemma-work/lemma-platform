import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";

import {
    getPublicTemplateBySlug,
    PUBLIC_TEMPLATES,
    templateRunHref,
} from "@/site/templates/catalog";
import { getPlugin, installHref, PLUGINS } from "@/site/plugins/catalog";
import { PluginPage } from "@/site/plugins/plugin-page";
import { pageMetadata } from "@/site/metadata";

interface TemplatePageProps {
    params: Promise<{ slug: string }>;
}

/** A plugin's own page; a GitHub template without one goes straight to its
 *  install page, as before. A plugin wins where the two share a slug. */
export function generateStaticParams() {
    const slugs = new Set([
        ...PLUGINS.map((p) => p.slug),
        ...PUBLIC_TEMPLATES.map((t) => t.slug),
    ]);
    return [...slugs].map((slug) => ({ slug }));
}

export async function generateMetadata({
    params,
}: TemplatePageProps): Promise<Metadata> {
    const { slug } = await params;
    const plugin = getPlugin(slug);
    if (plugin) {
        return {
            ...pageMetadata(plugin.name, plugin.lede, `/templates/${slug}`),
            robots: { index: !!installHref(plugin), follow: true },
        };
    }
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
    const plugin = getPlugin(slug);
    if (plugin) return <PluginPage plugin={plugin} />;
    const template = getPublicTemplateBySlug(slug);
    if (!template) notFound();
    redirect(templateRunHref(template));
}
