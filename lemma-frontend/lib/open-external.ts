/**
 * Open a URL outside the workspace, in a window that cannot reach back.
 *
 * Without `noopener` the opened page keeps a `window.opener` handle and can
 * navigate the workspace tab to anywhere it likes. That matters most for the
 * links this is used for: connector authorization and channel setup URLs, which
 * come from a provider's API response rather than from Lemma. It also lets the
 * browser drop the new page into its own process.
 *
 * `noreferrer` implies `noopener` and additionally withholds the referrer, which
 * would otherwise leak workspace and pod ids in the URL path to the provider.
 *
 * On desktop the shell decides whether this becomes a system-browser window or
 * an owned one; it inspects the URL and needs nothing from the caller.
 */
export function openExternal(url: string): Window | null {
  return window.open(url, "_blank", "noopener,noreferrer");
}
