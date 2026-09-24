import { listContent } from "@/site/content/loader";
import { compileContent } from "@/site/content/compile";
import { SitePage } from "@/site/chrome";
import { pageMetadata } from "@/site/metadata";
export const metadata = pageMetadata(
  "Changelog",
  "What is new in Lemma.",
  "/changelog",
);
export default async function Page() {
  const posts = await Promise.all(
    listContent("changelog").map(async (p) => ({
      ...p,
      compiled: await compileContent(p.body),
    })),
  );
  return (
    <SitePage title="Changelog" description="What is new in Lemma.">
      <p>Stable releases and dated development updates. Main and nightly updates are labeled separately from versioned releases.</p>
      <p>
        <a href="/feed.xml">Subscribe with RSS ↗</a>
      </p>
      {posts.map((p) => (
        <article className="site-prose" id={p.slug} key={p.slug}>
          <time>{p.frontmatter.published}</time>
          <h2>
            <a href={"#" + p.slug}>{p.frontmatter.title}</a>
          </h2>
          {p.compiled.content}
        </article>
      ))}
    </SitePage>
  );
}
