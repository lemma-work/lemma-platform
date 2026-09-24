import { NextResponse, type NextRequest } from 'next/server';
import { prefersMarkdown } from '@/site/markdown/negotiate';
import { markdownForPath } from '@/site/markdown/pages';
import { isLocalDeployment } from '@/site/config';

/**
 * `Accept: text/markdown` content negotiation, per acceptmarkdown.com — an
 * agent can ask for the same URL a browser gets and receive the machine-
 * readable representation instead of HTML, and any response from a
 * negotiated route says so via `Vary: Accept` so a cache never serves one
 * variant to a client that asked for the other.
 *
 * The markdown renderers below are plain data transforms over lib/data/ —
 * no Node-only APIs — so this runs fine in the default proxy runtime.
 */
export const config = {
    matcher: [
        '/',
        '/docs',
        '/docs/:path*',
        '/privacy',
        '/tos',
        '/about',
        '/contact',
        '/download',
    ],
};

/**
 * Pages a local installation has no use for, and where to go instead.
 *
 * A desktop installation is not selling anything and has nothing to download:
 * whoever reaches it -- the desktop webview, a phone on the same Wi-Fi, a
 * visitor holding a shared link -- came for the workspace. Decided here rather
 * than in the pages because both are prerendered at build time, and the build
 * is the same one hosted Lemma serves; only the environment this server was
 * started with knows it is a local one.
 */
const NOT_LOCAL = new Set(['/', '/download']);

export function proxy(request: NextRequest): NextResponse {
    if (NOT_LOCAL.has(request.nextUrl.pathname) && isLocalDeployment()) {
        /* Against the request's own URL, which Next writes back out as a
           relative Location when the origins match -- so behind a sharing
           gateway, where the Host this server sees is loopback, a phone is
           not sent to its own localhost. */
        return NextResponse.redirect(new URL('/t', request.url), 307);
    }

    if (request.method !== 'GET' && request.method !== 'HEAD') {
        return NextResponse.next();
    }

    if (!prefersMarkdown(request.headers.get('accept'))) {
        const response = NextResponse.next();
        response.headers.append('Vary', 'Accept');
        return response;
    }

    const markdown = markdownForPath(request.nextUrl.pathname);
    if (!markdown) {
        const response = NextResponse.next();
        response.headers.append('Vary', 'Accept');
        return response;
    }

    return new NextResponse(markdown, {
        status: 200,
        headers: {
            'Content-Type': 'text/markdown; charset=utf-8',
            Vary: 'Accept',
        },
    });
}
