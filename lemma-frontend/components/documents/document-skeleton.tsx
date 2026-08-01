import { Skeleton, SkeletonText } from '@/components/shared/loading';

/**
 * Opening a document is three waits in a row — the viewer's JS chunk, then the
 * file record, then the rendered preview — and each one used to draw its own
 * placeholder at its own size. The reader saw the pane rebuilt twice before any
 * content arrived, with the shimmer restarting each time, which is what "two
 * loading screens" looks like from the outside.
 *
 * So all three render *this*, and the frame stops moving: chunk-load and
 * record-load use `DocumentSkeleton`, and once the record lands the real header
 * takes over while the body keeps `DocumentBodySkeleton` until the preview is
 * ready. One shape, three waits, one visible load.
 */

/** The body fill — used on its own inside the settled viewer while a preview renders. */
export function DocumentBodySkeleton() {
    return (
        <div className="min-h-[220px] space-y-3" aria-hidden="true">
            <SkeletonText lines={4} />
            <SkeletonText lines={3} />
        </div>
    );
}

/**
 * The whole viewer, before there is a document to put in it.
 *
 * Mirrors `DocumentViewer`'s own shell — `document-viewer-shell`, the
 * `context-row` header band, the `p-3` scroll body — so when the real one
 * arrives it lands in the same box rather than replacing this one.
 */
export function DocumentSkeleton({
    headerMode = 'inline',
}: {
    headerMode?: 'inline' | 'topbar';
}) {
    return (
        <div
            className="document-viewer-shell relative flex h-full min-h-0 flex-col bg-[var(--card-bg)]"
            role="status"
            aria-label="Loading document"
        >
            {/* `topbar` mode puts the name in the pod's context bar instead, so
                there is no band here to stand in for. */}
            {headerMode === 'inline' ? (
                <div className="context-row flex-wrap items-center justify-between gap-2 px-3 py-2">
                    <div className="flex min-w-0 items-center gap-2">
                        <Skeleton shape="block" className="h-8 w-20" />
                        <div className="min-w-0 space-y-1.5">
                            <Skeleton className="h-3 w-40" />
                            <div className="mt-1 flex items-center gap-1.5">
                                <Skeleton shape="block" className="h-4 w-16" />
                                <Skeleton shape="block" className="h-4 w-20" />
                            </div>
                        </div>
                    </div>
                    <Skeleton shape="block" className="h-8 w-24" />
                </div>
            ) : null}

            <div className="min-h-0 flex-1 overflow-auto p-3">
                <DocumentBodySkeleton />
            </div>
        </div>
    );
}
