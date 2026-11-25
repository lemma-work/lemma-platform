import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";

import { getPublicTemplateBySlug, PUBLIC_TEMPLATES, templateRunHref } from "@/site/templates/catalog";
import { BUILT, getPlugin, PLANNED } from "@/site/plugins/catalog";
import { DevPlannedPage, DevPluginPage } from "@/site/plugins/store/plugin-page";
import { pageMetadata } from "@/site/metadata";

interface TemplatePageProps {
    params: Promise<{ slug: string }>;
}

/** Every plugin in the catalog has a page; a GitHub template without one
 *  goes straight to its install page, as before. A plugin wins where the two
 *  share a slug. */
export function generateStaticParams() {
    const slugs = new Set([...BUILT.map((p) => p.slug), ...PLANNED.map((p) => p.slug), ...PUBLIC_TEMPLATES.map((t) => t.slug)]);
    return [...slugs].map((slug) => ({ slug }));
}

export async function generateMetadata({ params }: TemplatePageProps): Promise<Metadata> {
    const { slug } = await params;
    const plugin = getPlugin(slug);
    if (plugin?.kind === "built") {
        const said = plugin.landing ? `${plugin.landing.hero.headline}. ${plugin.landing.hero.sub}` : `${plugin.job}. ${plugin.intro}`;
        return pageMetadata(plugin.name, said, `/templates/${slug}`);
    }
    if (plugin?.kind === "planned") {
        /* A plugin not built yet is one line; it is linked, not offered to
           search engines. */
        return { ...pageMetadata(plugin.name, plugin.one, `/templates/${slug}`), robots: { index: false, follow: true } };
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
    /* Every plugin's page is in the docs' and the guide's look; one whose
       listing has moments shows them, the rest show what their listing says
       they do and their pictures. */
    if (plugin?.kind === "built") return <DevPluginPage plugin={plugin} />;
    if (plugin?.kind === "planned") return <DevPlannedPage plugin={plugin} />;
    const template = getPublicTemplateBySlug(slug);
    if (!template) notFound();
    redirect(templateRunHref(template));
}
