import { notFound } from "next/navigation";
import Link from "next/link";
import { listContent, getContent } from "@/site/content/loader";
import { compileContent } from "@/site/content/compile";
import { SitePage, JsonLd } from "@/site/chrome";
import { pageMetadata } from "@/site/metadata";
import { absoluteUrl } from "@/site/seo/site-url";
type Props = { params: Promise<{ slug: string }> };
export function generateStaticParams() {
  return listContent("blog").map((p) => ({ slug: p.slug }));
}
export async function generateMetadata({ params }: Props) {
  const p = getContent("blog", (await params).slug);
  return p
    ? pageMetadata(
        p.frontmatter.title,
        p.frontmatter.description,
        "/blog/" + p.slug,
      )
    : {};
}
export default async function Page({ params }: Props) {
  const p = getContent("blog", (await params).slug);
  if (!p) notFound();
  const compiled = await compileContent(p.body);
  return (
    <SitePage
      title={p.frontmatter.title}
      description={p.frontmatter.description}
    >
      <JsonLd
        schema={{
          "@context": "https://schema.org",
          "@type": "BlogPosting",
          headline: p.frontmatter.title,
          datePublished: p.frontmatter.published,
          dateModified: p.frontmatter.updated ?? p.frontmatter.published,
          url: absoluteUrl("/blog/" + p.slug),
        }}
      />
      <p>
        {p.frontmatter.author} · <time>{p.frontmatter.published}</time>
      </p>
      <nav aria-label="On this page">
        {compiled.headings.map((h) => (
          <p key={h.id}>
            <a href={"#" + h.id}>{h.text}</a>
          </p>
        ))}
      </nav>
      <article className="site-prose">{compiled.content}</article>
      {p.frontmatter.pod && (
        <Link className="btn" href={"/templates/" + p.frontmatter.pod}>
          Explore the template ↗
        </Link>
      )}
    </SitePage>
  );
}
