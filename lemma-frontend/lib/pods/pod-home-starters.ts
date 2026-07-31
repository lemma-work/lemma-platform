export interface PodHomeResourceSignals {
    appCount: number;
    agentCount: number;
    workflowCount: number;
    surfaceCount: number;
    activeSurfaceCount: number;
    scheduleCount: number;
    conversationCount: number;
    hasUsedWorkflow: boolean;
}

export type PodHomeStarterMode = 'fresh' | 'forming' | 'operating';

// This is deliberately derived from resources Home already loads. It must stay
// pure: deciding whether to show starters should never create another pod-home
// request or polling loop.
export function resolvePodHomeStarterMode(signals: PodHomeResourceSignals): PodHomeStarterMode {
    const durableResourceCount =
        signals.appCount
        + signals.agentCount
        + signals.workflowCount
        + signals.surfaceCount
        + signals.scheduleCount;
    const hasAnyPodWork = durableResourceCount > 0 || signals.conversationCount > 0;

    if (!hasAnyPodWork) return 'fresh';

    const hasWorkingApp = signals.appCount > 0 && (signals.agentCount > 0 || signals.workflowCount > 0);
    const hasWorkingSurface = signals.activeSurfaceCount > 0 && signals.agentCount > 0;
    const hasOperatingLoop = signals.scheduleCount > 0 || signals.hasUsedWorkflow;

    if (hasWorkingApp || hasWorkingSurface || hasOperatingLoop || durableResourceCount >= 3) {
        return 'operating';
    }

    return 'forming';
}
