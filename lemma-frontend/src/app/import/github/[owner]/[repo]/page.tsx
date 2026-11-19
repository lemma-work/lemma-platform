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
    fetchPublicBundleManifest,
    fetchPublicGitHubReadme,
    resolveReadmeAssetUrl,
    resolveReadmeLinkUrl,
} from "@/site/github/public-repository";
import { findPublicTemplateBySource } from "@/site/templates/catalog";
import { pageMetadata } from "@/site/metadata";
import {
    folderFromQuery,
    folderName,
    installPath,
    installTitle,
    sourceHref,
    sourceLabel,
    type GitHubBundleSource,
} from "@/site/import/github-source";
import s from "@/site/import/import.module.css";

type Props = {
    params: Promise<{ owner: string; repo: string }>;
    searchParams: Promise<Record<string, string | string[] | undefined>>;
};

/** The repository, and the folder in it when `?path=` names one. A folder
 *  the server would not take is no page. */
async function sourceOf({
    params,
    searchParams,
}: Props): Promise<GitHubBundleSource> {
    const { owner, repo } = await params;
    const path = folderFromQuery((await searchParams).path);
    if (
        !/^[a-zA-Z0-9_.-]+$/.test(owner) ||
        !/^[a-zA-Z0-9_.-]+$/.test(repo) ||
        path === undefined
    )
        notFound();
    return { owner, repo, path };
}

/** A curated template is a whole repository; one folder of it is not. */
function templateOf(source: GitHubBundleSource) {
    return source.path
        ? null
        : findPublicTemplateBySource(source.owner, source.repo);
}

export async function generateMetadata(props: Props) {
    const source = await sourceOf(props);
    const template = templateOf(source);
    const manifest = source.path
        ? await fetchPublicBundleManifest(source.owner, source.repo, source.path)
        : null;
    return {
        ...pageMetadata(
            "Install " +
                installTitle(source, {
                    template: template?.name,
                    manifest: manifest?.name,
                }),
            template?.description ??
                manifest?.description ??
                "Install " + sourceLabel(source) + " on a Lemma.",
            installPath(source),
        ),
        robots: { index: !!template, follow: true },
    };
}

export default async function Page(props: Props) {
    const source = await sourceOf(props);
    const { owner, repo, path } = source;
    const template = templateOf(source);
    const [readme, manifest] = await Promise.all([
        fetchPublicGitHubReadme(owner, repo, path ?? ""),
        path ? fetchPublicBundleManifest(owner, repo, path) : null,
    ]);
    const shown = readme
        ? extractReadmePresentation(
              readme.markdown,
              path ? folderName(path) : repo,
          )
        : null;
    const title = installTitle(source, {
        template: template?.name,
        manifest: manifest?.name,
        readme: shown?.title,
    });
    const lede =
        template?.description ?? manifest?.description ?? shown?.intro ?? "";
    const githubHref = sourceHref(source);
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
                                {sourceLabel(source)}
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
                                                        readme.dir,
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
                                                        readme.dir,
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
                        <InstallHost
                            owner={owner}
                            repo={repo}
                            path={path}
                            title={title}
                        />
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
