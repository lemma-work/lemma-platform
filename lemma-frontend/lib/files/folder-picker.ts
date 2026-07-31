/**
 * Folder-picker data access, walked one directory at a time.
 *
 * The picker used to crawl the whole pod tree before it could render anything,
 * which made "Link folder" wait on every directory in the pod. Nothing here
 * looks past the directory it was asked about — depth costs a request only when
 * the reader expands into it.
 */

export const ROOT_DIRECTORY = '/';

/** Directory listings interleave files and folders, so one level can span pages. */
export const FOLDER_PAGE_LIMIT = 200;

/**
 * Ceiling on pages read for a single directory. One crowded directory must not
 * be able to stall the picker; the level reports `truncated` instead.
 */
export const MAX_DIRECTORY_PAGES = 8;

export type FolderOption = {
    id: string;
    name: string;
    path: string;
};

/** Direct subfolders of one directory, plus whether the scan hit its page cap. */
export type FolderLevel = {
    folders: FolderOption[];
    truncated: boolean;
};

type FileListItem = {
    id: string;
    name?: string | null;
    path?: string | null;
    kind?: string | null;
};

export type FileListPage = {
    items?: FileListItem[] | null;
    next_page_token?: string | null;
};

export type ListDirectory = (args: {
    directoryPath?: string;
    limit: number;
    pageToken?: string;
}) => Promise<FileListPage>;

export function normalizeFolderPath(path?: string | null): string | undefined {
    if (path === undefined || path === null) return undefined;

    const trimmed = path.trim();
    if (!trimmed || trimmed === ROOT_DIRECTORY) return ROOT_DIRECTORY;

    const normalized = `/${trimmed.replace(/^\/+/, '').replace(/\/+/g, '/')}`;
    return normalized.length > 1 && normalized.endsWith('/')
        ? normalized.slice(0, -1)
        : normalized;
}

/** How a folder path reads in the UI: `/reports/q3` → `reports/q3`. */
export function folderDisplayPath(path: string): string {
    return path.replace(/^\//, '');
}

/** Every directory on the way to `path`, the folder itself excluded: `/a/b/c` → `/a`, `/a/b`. */
export function ancestorFolderPaths(path: string): string[] {
    const segments = path.split('/').filter(Boolean).slice(0, -1);
    const ancestors: string[] = [];
    segments.reduce((prefix, segment) => {
        const next = `${prefix}/${segment}`;
        ancestors.push(next);
        return next;
    }, '');
    return ancestors;
}

/** Subfolders of `directoryPath` — that directory only, never its descendants. */
export async function fetchChildFolders(
    listDirectory: ListDirectory,
    directoryPath: string,
): Promise<FolderLevel> {
    const isRoot = directoryPath === ROOT_DIRECTORY;
    const folders: FolderOption[] = [];

    let pageToken: string | undefined = undefined;
    let pagesFetched = 0;

    do {
        const page: FileListPage = await listDirectory({
            directoryPath: isRoot ? undefined : directoryPath,
            limit: FOLDER_PAGE_LIMIT,
            pageToken,
        });
        pagesFetched += 1;

        for (const item of page.items || []) {
            if (item.kind !== 'FOLDER') continue;

            const name = item.name || item.id;
            const fallbackPath = isRoot ? `/${name}` : `${directoryPath}/${name}`;
            const fullPath = normalizeFolderPath(item.path || fallbackPath) || fallbackPath;

            // `/me` is the reader's personal root; it is never a pod-wide grant.
            if (folderDisplayPath(fullPath) === 'me') continue;

            folders.push({ id: item.id, name, path: fullPath });
        }

        pageToken = page.next_page_token || undefined;
    } while (pageToken && pagesFetched < MAX_DIRECTORY_PAGES);

    return {
        folders: folders.sort(
            (a, b) => a.name.localeCompare(b.name) || a.path.localeCompare(b.path),
        ),
        truncated: Boolean(pageToken),
    };
}
