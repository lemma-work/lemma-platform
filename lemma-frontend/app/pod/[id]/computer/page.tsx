'use client';

/**
 * The sandbox's files, at full size.
 *
 * A route rather than only the pane beside a conversation, for the reason
 * anybody opens a file manager: looking through a machine is a thing you do
 * for a while, in a window, not in a sidebar you are also trying to read a
 * conversation next to.
 *
 * The conversation layout's comment argues against exactly this -- "a
 * workspace is keyed to the person and only means anything in the
 * conversation that has been using it, so a URL you could navigate to cold
 * would be a page that cannot say whose files it is showing". The first half
 * is true and the conclusion does not follow: the files API takes no
 * identifier at all, it resolves *the caller's own* sandbox from the session.
 * There is exactly one machine this can be showing, and it is yours.
 *
 * `path` and `file` live in the URL so a view survives a reload and can be
 * sent to yourself. They are a position, not a permission: the same session
 * check answers either way.
 */

import { Suspense, use, useCallback } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';

import { FileExplorer } from '@/components/workspace/file-explorer';
import { WORKSPACE_ROOT } from '@/lib/hooks/use-workspace-files';

function Explorer() {
    const router = useRouter();
    const params = useSearchParams();
    const root = params.get('path') || WORKSPACE_ROOT;
    const selected = params.get('file');

    const select = useCallback(
        (path: string) => {
            const next = new URLSearchParams(params.toString());
            next.set('file', path);
            // `replace`, not `push`: clicking through six files should not
            // mean six presses of Back to leave the page.
            router.replace(`?${next.toString()}`, { scroll: false });
        },
        [params, router],
    );

    return <FileExplorer root={root} selected={selected} onSelect={select} />;
}

export default function ComputerPage({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    // Unused, and read anyway: Next resolves route params as a promise, and
    // not consuming it is what leaves the page rendering before the route is
    // settled.
    use(params);

    return (
        <div className="flex h-full min-h-0 flex-col">
            <header className="flex items-center gap-2 border-b border-[var(--row-border)] px-4 py-2.5">
                <h1 className="text-sm text-[var(--text-primary)]">Your computer</h1>
                <p className="text-xs text-[var(--text-tertiary)]">
                    The files your agents work in.
                </p>
            </header>
            <div className="min-h-0 flex-1">
                {/* `useSearchParams` opts a page into client rendering, and
                    Next asks for the boundary explicitly rather than doing it
                    to the whole route. */}
                <Suspense fallback={null}>
                    <Explorer />
                </Suspense>
            </div>
        </div>
    );
}
