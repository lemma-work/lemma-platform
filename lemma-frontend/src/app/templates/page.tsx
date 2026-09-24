import Link from "next/link";
import { PUBLIC_TEMPLATES, templateRunHref } from "@/site/templates/catalog";
import { SitePage } from "@/site/chrome";
import { pageMetadata } from "@/site/metadata";
export const metadata = pageMetadata(
  "Templates",
  "Start with a working example and make it your own.",
  "/templates",
);
export default function Page() {
  return (
    <SitePage
      wide
      title="A place to start"
      description="Working examples you can install and make your own."
    >
      <div className="site-grid">
        {PUBLIC_TEMPLATES.map((t) => (
          <Link className="site-card" key={t.slug} href={templateRunHref(t)}>
            <img
              src={"/templates/" + t.slug + "/social-preview.jpg"}
              alt=""
              loading="lazy"
            />
            <h2>{t.name}</h2>
            <p>{t.description}</p>
            <span>Explore and install ↗</span>
          </Link>
        ))}
      </div>
    </SitePage>
  );
}
