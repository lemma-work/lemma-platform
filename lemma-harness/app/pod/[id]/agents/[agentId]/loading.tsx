import { AgentDetailSkeleton } from '@/components/agents/agent-detail-skeleton';

/**
 * The agent page's shape while its route chunk loads, so the generic index
 * skeleton one level up does not flash a card grid at a detail page.
 */
export default function AgentDetailLoading() {
    return (
        <div className="flex h-full min-h-0 flex-col bg-[var(--bg-canvas)]">
            <div className="resource-page-scroll">
                <div className="resource-page-column">
                    <AgentDetailSkeleton />
                </div>
            </div>
        </div>
    );
}
