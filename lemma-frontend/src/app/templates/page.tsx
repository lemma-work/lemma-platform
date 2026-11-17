import Link from "next/link";
import {
    PUBLIC_TEMPLATES,
    templateGitHubSource,
    templateRunHref,
} from "@/site/templates/catalog";
import { PLUGINS, templatesUsedByPlugins } from "@/site/plugins/catalog";
import { PluginShelf } from "@/site/plugins/shelf";
import { SitePage } from "@/site/chrome";
import { pageMetadata } from "@/site/metadata";
import s from "@/site/plugins/plugins.module.css";

export const metadata = pageMetadata(
    "Plugins",
    "Start a Lemma with apps that already work, then change them or add your own.",
    "/templates",
);

/** The GitHub templates no plugin installs or starts from: one repo, one
 *  card, and a template a plugin starts from is linked on that plugin's page. */
const USED = templatesUsedByPlugins();
const MORE = PUBLIC_TEMPLATES.filter((t) => {
    if (USED.has(t.slug)) return false;
    const { owner, repo } = templateGitHubSource(t);
    return !PLUGINS.some(
        (p) =>
            p.install?.owner.toLowerCase() === owner.toLowerCase() &&
            p.install.repo.toLowerCase() === repo.toLowerCase(),
    );
});

export default function Page() {
    return (
        <SitePage
            wide
            title="Plugins"
            description="Start a Lemma with apps that already work, then change them or add your own. Plugins share their tables, so each one you add knows what the others know. Most here are still being built; each page says how far along it is."
        >
            <PluginShelf />
            <h2 className={s.moreHead}>More to start from</h2>
            <p className={s.moreLede}>
                Working examples on GitHub you can install and make your own.
            </p>
            <div className="site-grid">
                {MORE.map((t) => (
                    <Link
                        className="site-card"
                        key={t.slug}
                        href={templateRunHref(t)}
                    >
                        <img
                            src={"/templates/" + t.slug + "/social-preview.jpg"}
                            alt=""
                            loading="lazy"
                        />
                        <h3>{t.name}</h3>
                        <p>{t.description}</p>
                        <span>Explore and install ↗</span>
                    </Link>
                ))}
            </div>
        </SitePage>
    );
}
