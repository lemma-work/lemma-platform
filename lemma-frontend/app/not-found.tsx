import Link from "next/link";
import { Button } from "@/components/ui/button";

/**
 * A 404 that is not a dead end.
 *
 * Next.js ships a default not-found page with no navigation on it at all. In a
 * browser that is survivable — there is a back button and an address bar. In the
 * desktop webview there is neither, so a bad link (one in a pod's own markdown,
 * say) left the app on a black page with no way out. Worse, the route is
 * remembered for the next launch, so quitting from the tray and reopening
 * returned to the same dead page.
 */
export default function NotFound() {
    return (
        <div className="flex min-h-screen flex-col items-center justify-center gap-6 px-6 text-center">
            <div className="flex flex-col gap-2">
                <p className="text-sm font-medium text-[var(--text-tertiary)]">404</p>
                <h1 className="text-xl font-semibold text-[var(--text-primary)]">
                    This page could not be found.
                </h1>
                <p className="max-w-md text-sm text-[var(--text-secondary)]">
                    The link may be out of date, or point somewhere that was renamed
                    or deleted.
                </p>
            </div>
            <Button asChild variant="primary" size="sm">
                <Link href="/">Go to your workspace</Link>
            </Button>
            {/* The sitemap and llms.txt are file/route responses, not pages, so
                these are plain anchors rather than next/link. */}
            <p className="text-xs text-[var(--text-tertiary)]">
                Looking for something specific? Try the{' '}
                <a className="underline" href="/sitemap.xml">
                    sitemap
                </a>
                , the{' '}
                <Link className="underline" href="/docs">
                    docs
                </Link>
                , or{' '}
                <a className="underline" href="/llms.txt">
                    llms.txt
                </a>
                .
            </p>
        </div>
    );
}
