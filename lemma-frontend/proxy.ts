import { NextResponse, type NextRequest } from 'next/server';
import { prefersMarkdown } from '@/lib/markdown/negotiate';
import { markdownForPath } from '@/lib/markdown/pages';

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
    ],
};

export function proxy(request: NextRequest): NextResponse {
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
