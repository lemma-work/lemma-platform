"use client";

import { useEffect } from "react";
import { Button } from "@/components/ui/button";
import { captureEvent } from "@/lib/analytics/client";

/**
 * The last stop for an error that escaped every boundary below it.
 *
 * There were none. Next.js has its own fallback, which in a browser is
 * survivable and in the desktop webview is a dead page with no back button —
 * the same trap `not-found.tsx` exists to close, reached a different way.
 *
 * It is also the only place that can tell us a client-side error happened at
 * all. `client.error` has been in the analytics catalog since it was written,
 * listed under `KNOWN_UNEMITTED` because nothing raised it; production's
 * client-side errors were visible only as framework text on stderr, at whatever
 * severity the log pipeline chose, with no owner and no way to count them.
 *
 * Only the constructor name is reported. `catalog.ts` is explicit that messages
 * carry user content — a stack or a message from this app can contain table
 * rows, file contents or an agent transcript — so the message is deliberately
 * not sent anywhere.
 */
export default function GlobalError({
    error,
    reset,
}: {
    error: Error & { digest?: string };
    reset: () => void;
}) {
    useEffect(() => {
        captureEvent("client.error", { error_class: error.name || "Error" });
    }, [error]);

    return (
        <html lang="en">
            <body>
                <div className="flex min-h-screen flex-col items-center justify-center gap-6 px-6 text-center">
                    <div className="flex flex-col gap-2">
                        <h1 className="text-xl font-semibold text-[var(--text-primary)]">
                            Something went wrong.
                        </h1>
                        <p className="text-sm text-[var(--text-secondary)]">
                            The page failed to load. Trying again usually works.
                        </p>
                    </div>
                    <div className="flex items-center gap-2">
                        <Button onClick={reset}>Try again</Button>
                        <Button variant="quiet" onClick={() => window.location.reload()}>
                            Reload
                        </Button>
                    </div>
                </div>
            </body>
        </html>
    );
}
