/** Where an app's cover lives: `/.lemma/cover.png` on the app's own address,
 *  as the web's `appCoverUrl` reads it. Null for an app with no address of its
 *  own, such as a sample one. Built by hand: React Native's `URL` is partial. */
export function coverUrl(appUrl: string | null): string | null {
    const origin = appUrl ? /^https?:\/\/[^/?#]+/i.exec(appUrl)?.[0] : null;
    return origin ? `${origin}/.lemma/cover.png` : null;
}
