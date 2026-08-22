/**
 * The pod's Models tab: the pod default and the organization-wide catalog it
 * picks from, on one page inside the pod shell.
 *
 * A helper rather than a literal because every model picker in the pod links
 * here — the composer, an agent's card, a conversation — and when this lived
 * under the organization each of those built its own URL and had to carry a
 * `returnTo` back. One function is what keeps them agreeing.
 */
export function podModelsHref(podId: string): string {
    return `/pod/${encodeURIComponent(podId)}/settings/models`;
}
