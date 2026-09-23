import { DatastoreTableSkeleton } from '@/components/data/datastore-table-skeleton';

/**
 * The data hub settles into a table workbench, so that is what it waits as —
 * the pod-level card-grid skeleton would be the wrong shape entirely.
 */
export default function DataHubLoading() {
    return (
        <div className="resource-workbench-shell context-shell flex h-full min-h-0 flex-col bg-transparent">
            <DatastoreTableSkeleton />
        </div>
    );
}
