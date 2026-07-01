/** Fetch a public GitHub repo's README.md straight from raw.githubusercontent.com —
 * no auth, no rate-limited API call. Tries the two common default branch names
 * since the raw CDN doesn't support a "HEAD" alias the way codeload's zipball
 * endpoint does. */
export async function fetchGithubReadme(
    owner: string,
    repo: string,
): Promise<{ markdown: string; branch: string } | null> {
    for (const branch of ['main', 'master']) {
        try {
            const res = await fetch(
                `https://raw.githubusercontent.com/${owner}/${repo}/${branch}/README.md`,
                { cache: 'no-store' },
            );
            if (res.ok) return { markdown: await res.text(), branch };
        } catch {
            // try the next candidate branch
        }
    }
    return null;
}
