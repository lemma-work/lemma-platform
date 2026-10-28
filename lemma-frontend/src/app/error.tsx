"use client";

import Link from "next/link";
import { useEffect } from "react";

import { captureClientError } from "@/site/analytics/client";

/* A render error anywhere below the root layout — the workspace and the
   outcome frame included, since their shells live in nested layouts. React
   reports an error a boundary caught to the console, not to the window, so
   without this page it reached nobody: Next's own fallback showed "a
   client-side exception has occurred" and error tracking never heard of it. */
export default function AppError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
    useEffect(() => {
        captureClientError(error);
    }, [error]);
    return (
        <main className="screen">
            <div className="screen__inner">
                <p>Something went wrong on this page.</p>
                <button className="btn" onClick={() => retry()}>Try again</button>
                <Link href="/t">Open your teammates</Link>
            </div>
        </main>
    );
}
