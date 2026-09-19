'use client';

/**
 * The sandbox, at full size: its files, and its browser.
 *
 * A route rather than only the pane beside a conversation, for the reason
 * anybody opens a file manager or a second browser window: looking through a
 * machine, or watching one work, is a thing you do for a while, in a window,
 * not in a sidebar you are also trying to read a conversation next to.
 *
 * Both live here rather than on two routes because they are one thing --
 * `ComputerPanel` is already "the agent's computer, in one panel" with these
 * same two tabs, and the full-screen version should be that panel at full
 * size rather than a different arrangement somebody has to relearn.
 *
 * The conversation layout's comment argues against exactly this -- "a
 * workspace is keyed to the person and only means anything in the
 * conversation that has been using it, so a URL you could navigate to cold
 * would be a page that cannot say whose files it is showing". The first half
 * is true and the conclusion does not follow: the files and browser APIs take
 * no identifier at all, they resolve *the caller's own* sandbox from the
 * session. There is exactly one machine this can be showing, and it is yours.
 *
 * `view`, `path` and `file` live in the URL so a view survives a reload and
 * can be sent to yourself. They are a position, not a permission: the same
 * session check answers either way.
 */

import { Suspense, use, useCallback } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';

import { BrowserPane } from '@/components/workspace/browser-pane';
import { Button } from '@/components/ui/button';
import { FileExplorer } from '@/components/workspace/file-explorer';
import { WORKSPACE_ROOT } from '@/lib/hooks/use-workspace-files';
import { cn } from '@/lib/utils';

type View = 'files' | 'browser';

function Computer() {
    const router = useRouter();
    const params = useSearchParams();
    const view: View = params.get('view') === 'browser' ? 'browser' : 'files';
    const root = params.get('path') || WORKSPACE_ROOT;
    const selected = params.get('file');
    const conversationId = params.get('conversation') ?? undefined;

    const setParam = useCallback(
        (key: string, value: string) => {
            const next = new URLSearchParams(params.toString());
            next.set(key, value);
            // `replace`, not `push`: clicking through six files should not
            // mean six presses of Back to leave the page.
            router.replace(`?${next.toString()}`, { scroll: false });
        },
        [params, router],
    );

    return (
        <div className="flex h-full min-h-0 flex-col">
            <header className="flex items-center gap-2 border-b border-[var(--row-border)] px-4 py-2.5">
                <h1 className="text-sm text-[var(--text-primary)]">Your computer</h1>
                <div className="ml-2 flex items-center gap-1">
                    {(['files', 'browser'] as const).map((name) => (
                        <Button
                            key={name}
                            variant="quiet"
                            size="xs"
                            onClick={() => setParam('view', name)}
                            aria-pressed={view === name}
                            className={cn(
                                view === name
                                    ? 'text-[var(--text-primary)]'
                                    : 'text-[var(--text-tertiary)]',
                            )}
                        >
                            {name === 'files' ? 'Files' : 'Browser'}
                        </Button>
                    ))}
                </div>
                <p className="text-xs text-[var(--text-tertiary)]">
                    {view === 'files'
                        ? 'The files your agents work in.'
                        : 'The browser your agents drive. You can take over at any time.'}
                </p>
            </header>
            <div className="min-h-0 flex-1">
                {view === 'files' ? (
                    <FileExplorer
                        root={root}
                        selected={selected}
                        onSelect={(path) => setParam('file', path)}
                    />
                ) : (
                    // No `autoResize={false}` here, unlike the floating
                    // window: this *is* the view worth fitting the display
                    // to, and a full-screen page asking for a full-screen
                    // shape is the request the clamp was written for.
                    <BrowserPane conversationId={conversationId} />
                )}
            </div>
        </div>
    );
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
        // `useSearchParams` opts a page into client rendering, and Next asks
        // for the boundary explicitly rather than doing it to the whole route.
        <Suspense fallback={null}>
            <Computer />
        </Suspense>
    );
}
