import { notFound } from "next/navigation";
import { docsPages, docsPageMap } from "@/site/data/docs";
import { JsonLd } from "@/site/chrome";
import { DocsArticle } from "@/site/docs-article";
import { DocsHome } from "@/site/docs-home";
import { pageMetadata } from "@/site/metadata";
import { breadcrumbSchema } from "@/site/seo/structured-data";
type Props = { params: Promise<{ slug?: string[] }> };
export function generateStaticParams() {
    return [
        { slug: [] },
        ...docsPages.map((p) => ({ slug: p.slug.split("/") })),
    ];
}
export async function generateMetadata({ params }: Props) {
    const slug = (await params).slug?.join("/");
    const p = slug ? docsPageMap.get(slug) : null;
    return pageMetadata(
        p?.title ?? "Documentation",
        p?.description ?? "Learn the Lemma platform, SDK, and CLI.",
        slug ? "/docs/" + slug : "/docs",
    );
}
export default async function Page({ params }: Props) {
    const slug = (await params).slug?.join("/");
    const p = slug ? docsPageMap.get(slug) : null;
    if (slug && !p) notFound();
    return (
        <>
            <JsonLd
                schema={breadcrumbSchema([
                    { name: "Lemma", path: "/" },
                    { name: "Docs", path: "/docs" },
                    ...(p ? [{ name: p.title }] : []),
                ])}
            />
            {p ? <DocsArticle page={p} /> : <DocsHome />}
        </>
    );
}
