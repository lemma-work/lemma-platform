/** Where the API is, read in one place and never guessed.
 *
 *  `NEXT_PUBLIC_API_URL` is required and has deliberately no fallback. A
 *  default here is a single deployment's hostname compiled into everybody's
 *  browser bundle, and the failure it produces is the bad kind: a checkout
 *  that was never configured does not break, it points somebody's browser at
 *  an API that is not theirs and answers 401, which reads as an expired
 *  session rather than as a missing variable. Unset is a configuration error
 *  and is reported as one — a screen that names the variable, not a stack
 *  trace and not a wrong host.
 */

import { siteRuntime } from "@/site/runtime";

export const MISSING_API_URL =
    "NEXT_PUBLIC_API_URL is not set. Copy .env.example to .env.local and point it at your Lemma API.";

/** The environment this server was started with, handed to the browser by
 *  `/site-config.js`, and what the build was given only when neither exists
 *  -- see `site/runtime.ts`. Read at run time because the desktop app ships
 *  one build to every machine and learns its API origin only when it starts,
 *  and learns another one when sharing is turned on.
 *
 *  `site/runtime.ts` has no dependencies and no browser APIs beyond an
 *  optional `window`, so the server routes that proxy a share code still read
 *  it without pulling the SDK in. */
function fromEnvironment(): string | null {
    const trimmed = siteRuntime().apiUrl.trim();
    return trimmed ? trimmed.replace(/\/+$/, "") : null;
}

/** The configured origin, or `null` when there is none.
 *
 *  For callers that render rather than fetch. A missing origin is a screen
 *  somebody can act on; throwing inside a render turns it into a blank page
 *  and a console message nobody outside this repository can read. */
export function configuredApiUrl(): string | null {
    return fromEnvironment();
}

/** The configured origin, or a refusal that says what to set.
 *
 *  For callers that are about to make a request, where continuing means
 *  fetching from `undefined/s/abc`. */
export function requireApiUrl(): string {
    const url = fromEnvironment();
    if (!url) throw new Error(MISSING_API_URL);
    return url;
}
