import { notFound } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import { SitePage } from "@/site/chrome";
import { ActionHost } from "@/site/action-host";
import {
    fetchPublicGitHubReadme,
    resolveReadmeAssetUrl,
    resolveReadmeLinkUrl,
} from "@/site/github/public-repository";
import { findPublicTemplateBySource } from "@/site/templates/catalog";
import { pageMetadata } from "@/site/metadata";
type Props = { params: Promise<{ owner: string; repo: string }> };
export async function generateMetadata({ params }: Props) {
    const { owner, repo } = await params;
    return {
        ...pageMetadata(
            repo,
            "Explore and install " + owner + "/" + repo + " on Lemma.",
            "/import/github/" + owner + "/" + repo,
        ),
        robots: {
            index: !!findPublicTemplateBySource(owner, repo),
            follow: true,
        },
    };
}
export default async function Page({ params }: Props) {
    const { owner, repo } = await params;
    if (!/^[a-zA-Z0-9_.-]+$/.test(owner) || !/^[a-zA-Z0-9_.-]+$/.test(repo))
        notFound();
    const readme = await fetchPublicGitHubReadme(owner, repo);
    return (
        <SitePage title={repo} description={"From " + owner + " on GitHub"}>
            <ActionHost action="import" owner={owner} repo={repo} />
            <p>
                <a href={"https://github.com/" + owner + "/" + repo}>
                    View repository ↗
                </a>
            </p>
            {readme ? (
                <article className="site-prose">
                    <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        rehypePlugins={[rehypeRaw, rehypeSanitize]}
                        components={{
                            img: ({ src, alt }) => (
                                <img
                                    src={resolveReadmeAssetUrl(
                                        typeof src === "string" ? src : "",
                                        owner,
                                        repo,
                                        readme.branch,
                                    )}
                                    alt={alt || ""}
                                />
                            ),
                            a: ({ href, children }) => (
                                <a
                                    href={resolveReadmeLinkUrl(
                                        href || "",
                                        owner,
                                        repo,
                                        readme.branch,
                                    )}
                                >
                                    {children}
                                </a>
                            ),
                        }}
                    >
                        {readme.markdown}
                    </ReactMarkdown>
                </article>
            ) : (
                <p>
                    The README could not be loaded. You can still view the
                    repository or prepare an installation.
                </p>
            )}
        </SitePage>
    );
}
