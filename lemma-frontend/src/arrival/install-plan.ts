import { isBundleFolder, type GitHubBundleSource } from "@/site/import/github-source";

/** A built plugin's install address, `/import/github/<owner>/<repo>?path=…`,
 *  as the source the server's install queue reads; null for any other
 *  address. Which defaults it installs with is the server's to decide now
 *  (`pod_bundle/domain/plugin_install.py`), the same rule the install page
 *  follows. */
export function sourceOfInstall(href: string): GitHubBundleSource | null {
    const url = new URL(href, "https://lemma.invalid");
    const parts = url.pathname.split("/").filter(Boolean);
    if (parts.length !== 4 || parts[0] !== "import" || parts[1] !== "github") return null;
    const path = url.searchParams.get("path");
    if (path !== null && !isBundleFolder(path)) return null;
    return { owner: parts[2], repo: parts[3], path };
}
