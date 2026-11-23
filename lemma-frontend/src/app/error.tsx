"use client";

import { useEffect } from "react";

import { captureClientError } from "@/site/analytics/client";
import { PageBroke } from "@/outcomes/page-boundary";

/* A render error anywhere below the root layout that nothing nearer caught.
   The frame catches its own pages' (`outcomes/page-boundary.tsx`), and says
   it the same way; this is for everything else, and for the frame's sidebar.
   React reports an error a boundary caught to the console, not to the window,
   so without this page it reached nobody: Next's own fallback showed "a
   client-side exception has occurred" and error tracking never heard of it. */
export default function AppError({ error }: { error: Error & { digest?: string }; retry: () => void }) {
    useEffect(() => {
        captureClientError(error);
    }, [error]);
    return (
        <main className="kstate">
            <PageBroke error={error} digest={error?.digest} />
        </main>
    );
}
