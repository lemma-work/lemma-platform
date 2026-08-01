import { ListSkeleton, Skeleton } from '@/components/shared/loading';

/** A run reads as a progress header over a rail of steps. */
export default function FlowRunLoading() {
    return (
        <div className="flex h-full min-h-0 flex-col bg-transparent" role="status" aria-label="Loading run">
            <div className="flex h-12 shrink-0 items-center justify-between gap-3 px-4">
                <Skeleton shape="block" className="h-5 w-48" />
                <Skeleton shape="block" className="h-7 w-24" />
            </div>
            <div className="h-px w-full bg-[var(--progress-segment-bg)]" />
            <div className="min-h-0 flex-1 p-4">
                <ListSkeleton rows={6} />
            </div>
        </div>
    );
}
