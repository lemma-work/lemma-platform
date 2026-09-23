/**
 * The active pod id, read off the URL.
 *
 * There is no pod-id context in this app, and adding one for two consumers would
 * be more machinery than the question deserves: every pod route already carries
 * the id in its first two segments, and `usePathname()` is available anywhere.
 */
export function podIdFromPathname(pathname: string | null | undefined): string | null {
    if (!pathname) return null;
    const match = pathname.match(/^\/pod\/([^/]+)/);
    return match?.[1] ? decodeURIComponent(match[1]) : null;
}
