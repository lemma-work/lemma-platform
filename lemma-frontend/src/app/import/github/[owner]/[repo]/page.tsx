import { notFound } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import { GithubLogo, ArrowUpRight } from "@phosphor-icons/react/dist/ssr";
import { SiteFooter, SiteHeader } from "@/site/chrome";
import { InstallHost } from "@/site/import/install-host";
import {
    extractReadmePresentation,
    fetchPublicGitHubReadme,
    resolveReadmeAssetUrl,
    resolveReadmeLinkUrl,
} from "@/site/github/public-repository";
import { findPublicTemplateBySource } from "@/site/templates/catalog";
import { pageMetadata } from "@/site/metadata";
import { teammateName } from "@/site/import/plan";
import s from "@/site/import/import.module.css";

type Props = { params: Promise<{ owner: string; repo: string }> };

export async function generateMetadata({ params }: Props) {
    const { owner, repo } = await params;
    const template = findPublicTemplateBySource(owner, repo);
    return {
        ...pageMetadata(
            "Install " + (template?.name ?? teammateName(repo)),
            template?.description ??
                "Install " + owner + "/" + repo + " on a Lemma teammate.",
            "/import/github/" + owner + "/" + repo,
        ),
        robots: { index: !!template, follow: true },
    };
}

export default async function Page({ params }: Props) {
    const { owner, repo } = await params;
    if (!/^[a-zA-Z0-9_.-]+$/.test(owner) || !/^[a-zA-Z0-9_.-]+$/.test(repo))
        notFound();
    const template = findPublicTemplateBySource(owner, repo);
    const readme = await fetchPublicGitHubReadme(owner, repo);
    const shown = readme
        ? extractReadmePresentation(readme.markdown, repo)
        : null;
    const title = template?.name ?? shown?.title ?? teammateName(repo);
    const lede = template?.description ?? shown?.intro ?? "";
    const githubHref = "https://github.com/" + owner + "/" + repo;
    /* The hero already carries the README's opening line; printing it again
       as the first paragraph below reads as a mistake. */
    const at = shown && lede ? shown.body.indexOf(lede) : -1;
    const body =
        shown && at >= 0 && at < 600
            ? (
                  shown.body.slice(0, at) + shown.body.slice(at + lede.length)
              ).trim()
            : (shown?.body ?? "");

    return (
        <div className="site">
            <a className="site-skip" href="#content">
                Skip to content
            </a>
            <SiteHeader />
            <main id="content" className={s.page}>
                <div className={s.layout}>
                    <div className={s.body}>
                        <header className={s.hero}>
                            <a className={s.source} href={githubHref}>
                                <GithubLogo size={16} aria-hidden />
                                {owner}/{repo}
                                <ArrowUpRight size={12} aria-hidden />
                            </a>
                            <h1>{title}</h1>
                            {lede && <p>{lede}</p>}
                        </header>
                        {template && (
                            <>
                                <section className={s.section}>
                                    <h2 className={s.label}>What it does</h2>
                                    <ul className={s.outcomes}>
                                        {template.outcomes.map((o) => (
                                            <li key={o}>{o}</li>
                                        ))}
                                    </ul>
                                </section>
                                <section className={s.section}>
                                    <h2 className={s.label}>
                                        What comes with it
                                    </h2>
                                    <dl className={s.includes}>
                                        {template.includes.map((i) => (
                                            <div key={i.label}>
                                                <dt>{i.label}</dt>
                                                <dd>{i.detail}</dd>
                                            </div>
                                        ))}
                                    </dl>
                                </section>
                            </>
                        )}
                        <section className={s.section}>
                            <h2 className={s.label}>From the README</h2>
                            {readme && shown ? (
                                <article className={"site-prose " + s.readme}>
                                    <ReactMarkdown
                                        remarkPlugins={[remarkGfm]}
                                        rehypePlugins={[
                                            rehypeRaw,
                                            rehypeSanitize,
                                        ]}
                                        components={{
                                            img: ({ src, alt }) => (
                                                <img
                                                    src={resolveReadmeAssetUrl(
                                                        typeof src === "string"
                                                            ? src
                                                            : "",
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
                                        {body}
                                    </ReactMarkdown>
                                </article>
                            ) : (
                                <p className={s.muted}>
                                    The README could not be loaded.{" "}
                                    <a href={githubHref}>Read it on GitHub</a> —
                                    you can still install and review what it
                                    adds first.
                                </p>
                            )}
                        </section>
                    </div>
                    <aside
                        id="install"
                        className={s.aside}
                        aria-label="Install"
                    >
                        <InstallHost owner={owner} repo={repo} title={title} />
                    </aside>
                </div>
            </main>
            <div className={s.mobileBar}>
                <span>{title}</span>
                <a href="#install">Install</a>
            </div>
            <SiteFooter />
        </div>
    );
}
