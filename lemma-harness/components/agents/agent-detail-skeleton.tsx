import { Skeleton } from '@/components/shared/loading';

/**
 * The agent page's own shape, before the agent arrives.
 *
 * Built from the page's real class names — `resource-card`, `agent-identity`,
 * `agent-wiring-row` — so the identity block, the wiring rows, and the prompt
 * card all occupy the boxes they are about to occupy. What replaced it was a
 * centred spinner with no page around it, which meant every visit to an agent
 * was a blank screen followed by a full-layout snap.
 */
export function AgentDetailSkeleton({ wiringRows = 4 }: { wiringRows?: number }) {
    return (
        <>
            <section className="resource-card" role="status" aria-label="Loading agent">
                <header className="agent-identity">
                    <Skeleton shape="block" className="h-8 w-8 rounded-xl" />
                    <div className="agent-identity-body">
                        <div className="agent-identity-titles">
                            <Skeleton shape="block" className="h-6 w-44" />
                            <Skeleton className="h-3 w-24" />
                        </div>
                        <Skeleton className="h-3 w-4/5" />
                    </div>
                </header>

                <div className="mt-4">
                    {Array.from({ length: wiringRows }).map((_, index) => (
                        <div key={index} className="agent-wiring-row">
                            <div className="agent-wiring-label">
                                <Skeleton className="h-3 w-20" />
                            </div>
                            <div className="agent-wiring-value">
                                <Skeleton className="h-3 w-2/3" />
                            </div>
                        </div>
                    ))}
                </div>
            </section>

            <section className="resource-card" aria-hidden="true">
                <Skeleton className="h-3 w-28" />
                <div className="mt-3 space-y-2">
                    <Skeleton className="h-3 w-full" />
                    <Skeleton className="h-3 w-full" />
                    <Skeleton className="h-3 w-11/12" />
                    <Skeleton className="h-3 w-3/5" />
                </div>
            </section>
        </>
    );
}
