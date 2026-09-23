import { ListSkeleton, Skeleton } from '@/components/shared/loading';

/**
 * Files settle into a list, not a card grid — without this the route fell back
 * to the pod-level skeleton one directory up and waited as three cards before
 * becoming rows.
 */
export default function FilesLoading() {
    return (
        <div className="resource-index-shell context-shell min-h-full bg-transparent">
            <div className="mb-3 flex items-center gap-2">
                <Skeleton shape="block" className="h-8 w-64" />
            </div>
            <div className="surface-panel-quiet overflow-hidden">
                <ListSkeleton rows={6} />
            </div>
        </div>
    );
}
