"use client";

import { useEffect } from "react";

import { captureClientError } from "@/site/analytics/client";

/* An error in the root layout itself, which `error.tsx` cannot catch because
   it sits inside that layout. This replaces the whole document, without the
   app's stylesheets, so it is plain on purpose. */
export default function GlobalError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
    useEffect(() => {
        captureClientError(error);
    }, [error]);
    return (
        <html lang="en">
            <body>
                <title>Lemma</title>
                <main>
                    <p>Something went wrong loading Lemma.</p>
                    <button onClick={() => retry()}>Try again</button>
                </main>
            </body>
        </html>
    );
}
