import { teammateName } from "./plan";

/** What the install page installs: a public repository, or one folder of it.
 *
 *  A repository can hold several bundles, one per folder (`install/mail`,
 *  `install/crm`), and `/import/github/<owner>/<repo>?path=install/mail`
 *  installs one of them. Everything the page says about the source -- what it
 *  is called, where it links, what it asks the server to import -- comes from
 *  here, so a page with no folder reads exactly as it did before there could
 *  be one. */

export interface GitHubBundleSource {
    owner: string;
    repo: string;
    /** The folder inside the repository that holds the bundle, or `null` for
     *  the repository itself. */
    path: string | null;
}

/* The shape the server accepts (`BUNDLE_PATH_PATTERN`): relative, ordinary
   characters, and no segment of dots alone, so `.` and `..` cannot name
   anything outside the repository. Checked here too, so a malformed address
   is a missing page rather than a failed install. */
const SEGMENT = "[A-Za-z0-9._-]*[A-Za-z0-9_-][A-Za-z0-9._-]*";
const FOLDER = new RegExp("^" + SEGMENT + "(?:/" + SEGMENT + ")*$");
const FOLDER_MAX_LENGTH = 255;

export function isBundleFolder(value: string): boolean {
    return value.length <= FOLDER_MAX_LENGTH && FOLDER.test(value);
}

/** The page's `?path=`, as a folder; `undefined` when it names none the
 *  server would take, which the page answers as not found. */
export function folderFromQuery(
    value: string | string[] | undefined,
): string | null | undefined {
    if (value === undefined) return null;
    return typeof value === "string" && isBundleFolder(value)
        ? value
        : undefined;
}

/** The folder's own name: `install/mail` → `mail`. */
export function folderName(path: string): string {
    return path.slice(path.lastIndexOf("/") + 1);
}

/** What the page calls what it installs, which also names the new Lemma.
 *
 *  A folder's bundle is named by its own `pod.json`, else by the folder --
 *  never by the repository, which holds others besides. A whole repository
 *  keeps its order: the curated template, its README's title, its name. */
export function installTitle(
    source: GitHubBundleSource,
    named: {
        template?: string | null;
        manifest?: string | null;
        readme?: string | null;
    },
): string {
    if (source.path)
        return named.manifest || teammateName(folderName(source.path));
    return named.template || named.readme || teammateName(source.repo);
}

/** The install page's own address, folder included. */
export function installPath(source: GitHubBundleSource): string {
    const base = "/import/github/" + source.owner + "/" + source.repo;
    return source.path ? base + "?path=" + source.path : base;
}

/** Where the source lives on GitHub: the repository, or its folder. */
export function sourceHref(source: GitHubBundleSource): string {
    const repository = "https://github.com/" + source.owner + "/" + source.repo;
    return source.path ? repository + "/tree/HEAD/" + source.path : repository;
}

export function sourceLabel(source: GitHubBundleSource): string {
    const repository = source.owner + "/" + source.repo;
    return source.path ? repository + "/" + source.path : repository;
}

/** The body that starts the import. A folder is sent only when there is one,
 *  so an install of a whole repository asks exactly what it always asked. */
export function importRequest(source: GitHubBundleSource): {
    kind: "GITHUB";
    owner: string;
    repo: string;
    path?: string;
} {
    const body = { kind: "GITHUB" as const, owner: source.owner, repo: source.repo };
    return source.path ? { ...body, path: source.path } : body;
}
