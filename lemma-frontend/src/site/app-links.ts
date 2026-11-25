/**
 * Which lemma.work links open the phone app instead of the browser.
 *
 * iOS reads `/.well-known/apple-app-site-association` and Android reads
 * `/.well-known/assetlinks.json` from this site before it lets the app claim a
 * link. Both answer from here, so the two phones open the same paths. Those
 * paths are the ones the app reads in the web's grammar
 * (`lemma-mobile/src/areas/more/links.ts` `appPathOf`); Android also lists them
 * in `lemma-mobile/app.json` `intentFilters`.
 */

/** The app, as Apple names it: the team that signs it, then its bundle ID. */
export const IOS_APP_ID = '7LUB585F76.work.lemma.mobile';

export const ANDROID_PACKAGE = 'work.lemma.mobile';

/**
 * SHA-256 fingerprints of the certificate Google Play signs the app with
 * (Play Console > Test and release > App signing), as `AA:BB:…`. Empty until
 * the app is on Play: nothing is claimed for Android until then, and links
 * open in the browser.
 */
export const ANDROID_CERT_SHA256: readonly string[] = [];

/** Paths that open the app. A bare path also opens what lies under it. */
export const APP_LINK_PATHS = [
    '/invitations/',
    '/import/github/',
    '/join/',
    '/sign-in-to-site/',
    '/t/',
    '/l/',
    '/remix',
    '/templates',
    '/home',
    '/needs',
    '/apps',
    '/chats',
    '/people',
    '/connectors',
] as const;

/** Apple's file. Its `*` matches across `/`. */
export function appleAppSiteAssociation() {
    const components = APP_LINK_PATHS.flatMap((path) =>
        path.endsWith('/') ? [{ '/': path + '*' }] : [{ '/': path }, { '/': path + '/*' }],
    );
    return {
        applinks: {
            details: [{ appIDs: [IOS_APP_ID], components }],
        },
    };
}

/** Google's file. */
export function assetLinks() {
    if (ANDROID_CERT_SHA256.length === 0) return [];
    return [
        {
            relation: ['delegate_permission/common.handle_all_urls'],
            target: {
                namespace: 'android_app',
                package_name: ANDROID_PACKAGE,
                sha256_cert_fingerprints: [...ANDROID_CERT_SHA256],
            },
        },
    ];
}
