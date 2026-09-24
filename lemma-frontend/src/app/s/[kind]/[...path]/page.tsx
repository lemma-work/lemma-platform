import { notFound } from "next/navigation";
import QRCode from "react-qr-code";
import { SitePage } from "@/site/chrome";
import {
  isShareKind,
  resolveShareDestination,
  resolveShareName,
} from "@/site/share/share-link";
import {
  readContactCardSpec,
  contactChannels,
  contactCardDownloadPath,
} from "@/site/share/contact-card";
import { pageMetadata } from "@/site/metadata";
import { absoluteUrl } from "@/site/seo/site-url";
type Props = {
  params: Promise<{ kind: string; path: string[] }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};
export async function generateMetadata({ params, searchParams }: Props) {
  const p = await params,
    q = await searchParams;
  const name =
    resolveShareName({
      segments: p.path,
      query: q,
      name: typeof q.n === "string" ? q.n : null,
    }) || "Shared on Lemma";
  return {
    ...pageMetadata(
      name,
      "Open this shared resource on Lemma.",
      "/s/" + p.kind + "/" + p.path.map(encodeURIComponent).join("/"),
    ),
    robots: { follow: false, googleBot: { index: false, follow: false } },
  };
}
export default async function Page({ params, searchParams }: Props) {
  const p = await params,
    q = await searchParams;
  if (!isShareKind(p.kind)) notFound();
  const destination = resolveShareDestination(p.path, q);
  if (!destination) notFound();
  const name =
    resolveShareName({
      segments: p.path,
      query: q,
      name: typeof q.n === "string" ? q.n : null,
    }) || "Shared on Lemma";
  const search = new URLSearchParams();
  Object.entries(q).forEach(([k, v]) => {
    if (v) search.set(k, Array.isArray(v) ? v[0] : v);
  });
  const contact =
    p.kind === "contact" ? readContactCardSpec(search, name) : null;
  return (
    <SitePage
      title={name}
      description={contact?.note || "A shared resource on Lemma."}
    >
      {contact ? (
        <>
          <p>{contact.org}</p>
          <div className="site-actions">
            {contactChannels(contact).map((c) => (
              <a className="btn" key={c.key} href={c.href}>
                {c.label}
              </a>
            ))}
            <a
              className="btn btn--primary"
              href={contactCardDownloadPath(p.path, search)}
            >
              Save contact
            </a>
          </div>
          <QRCode
            value={
              absoluteUrl(
                "/s/contact/" + p.path.map(encodeURIComponent).join("/"),
              ) +
              "?" +
              search
            }
            size={180}
          />
        </>
      ) : (
        <p>
          Sign in with your account to open this resource. Its workspace
          permissions still apply.
        </p>
      )}
      <p>
        <a className="btn" href={destination}>
          Open in Lemma ↗
        </a>
      </p>
    </SitePage>
  );
}
