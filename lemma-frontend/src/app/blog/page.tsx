import Link from "next/link";
import { listContent } from "@/site/content/loader";
import { SitePage } from "@/site/chrome";
import { pageMetadata } from "@/site/metadata";
export const metadata = pageMetadata(
  "Blog",
  "Ideas about AI teammates and shared software.",
  "/blog",
);
export default function Page() {
  return (
    <SitePage
      title="The Lemma journal"
      description="Ideas about AI teammates and the work we do together."
    >
      {listContent("blog").map((p) => (
        <article className="site-card" key={p.slug}>
          <time>{p.frontmatter.published}</time>
          <h2>
            <Link href={"/blog/" + p.slug}>{p.frontmatter.title}</Link>
          </h2>
          <p>{p.frontmatter.description}</p>
        </article>
      ))}
    </SitePage>
  );
}
