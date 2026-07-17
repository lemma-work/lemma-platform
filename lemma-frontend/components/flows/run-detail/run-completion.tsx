import { CheckCircle, Flag } from '@/components/ui/icons';
import { cn } from '@/lib/utils';
import { QuietEmptyState } from '@/components/shared/empty-state';
import {
    formatTimestamp,
    getLaterDate,
    getVisibleStepData,
    hasVisibleData,
    parseApiDate,
    type RunCardRun,
    type RunCompletionTiming,
} from '../run-format';
import { StepDataPreview } from './step-data-preview';

export function RunCompletionChamber({
    timing,
    eventCount,
    output,
}: {
    timing: RunCompletionTiming;
    eventCount: number;
    output: unknown;
}) {
    return (
        <section className="h-full min-h-0 overflow-y-auto px-6 py-6">
            <div className="mb-6 flex items-start gap-3 border-b border-[color:color-mix(in_srgb,var(--border-subtle)_52%,transparent)] pb-5">
                <div className="state-badge-success flex h-9 w-9 shrink-0 items-center justify-center rounded-md">
                    <CheckCircle className="h-5 w-5" />
                </div>
                <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                        <h3 className="text-base font-semibold text-[var(--text-primary)]">Run complete</h3>
                        <span className="chip chip-pill chip-sm chip-muted">End reached</span>
                    </div>
                    <p className="mt-1 text-sm text-[var(--text-secondary)]">
                        Finished this workflow run.
                    </p>
                    <CompletionMeta timing={timing} compact />
                    <div className="mt-2 text-xs text-[var(--text-tertiary)]">
                        {eventCount} event{eventCount === 1 ? '' : 's'} completed
                    </div>
                </div>
            </div>

            <div>
                {hasVisibleData(output) ? (
                    <StepDataPreview label="Final result" data={output} variant="flat" />
                ) : (
                    <QuietEmptyState icon={<Flag className="h-4 w-4" />}>
                        This run reached the end without a recorded final output.
                    </QuietEmptyState>
                )}
            </div>
        </section>
    );
}

export function RunCompletionCard({
    timing,
    totalSteps,
    output,
}: {
    timing: RunCompletionTiming;
    totalSteps: number;
    output: unknown;
}) {
    return (
        <div className="surface-panel px-4 py-4 shadow-none">
            <div className="flex items-start gap-3">
                <div className="state-badge-success flex h-9 w-9 shrink-0 items-center justify-center rounded-md">
                    <CheckCircle className="h-5 w-5" />
                </div>
                <div>
                    <h4 className="text-base font-semibold text-[var(--text-primary)]">End reached</h4>
                    <p className="mt-1 text-sm text-[var(--text-secondary)]">
                        Finished all {totalSteps} step{totalSteps === 1 ? '' : 's'}.
                    </p>
                    <CompletionMeta timing={timing} compact />
                    {hasVisibleData(output) ? (
                        <div className="mt-4">
                            <StepDataPreview label="Final output" data={output} variant="flat" />
                        </div>
                    ) : null}
                </div>
            </div>
        </div>
    );
}

export function CompletionMeta({
    timing,
    compact = false,
}: {
    timing: RunCompletionTiming;
    compact?: boolean;
}) {
    const items = [
        timing.startedAt ? `Started ${formatTimestamp(timing.startedAt)}` : null,
        timing.completedAt ? `Finished ${formatTimestamp(timing.completedAt)}` : null,
        timing.duration,
    ].filter(Boolean);

    if (items.length === 0) return null;

    return (
        <div className={cn('flex flex-wrap justify-center gap-2 text-xs text-[var(--text-secondary)]', compact ? 'mt-3 justify-start' : 'mt-5')}>
            {items.map((item) => (
                <span key={item} className="rounded-full bg-[var(--card-bg)] px-2.5 py-1">
                    {item}
                </span>
            ))}
        </div>
    );
}

export function formatCompletionDuration(startedAt: Date | null, completedAt: Date | null): string | null {
    if (!startedAt || !completedAt) return null;

    const diffMs = completedAt.getTime() - startedAt.getTime();
    if (!Number.isFinite(diffMs) || diffMs < 0) return null;
    if (diffMs < 1000) return 'Under 1s';

    const totalSeconds = Math.round(diffMs / 1000);
    if (totalSeconds < 60) return `${totalSeconds}s`;

    const totalMinutes = Math.round(totalSeconds / 60);
    if (totalMinutes < 60) return `${totalMinutes}m`;

    const hours = Math.floor(totalMinutes / 60);
    const minutes = totalMinutes % 60;
    return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
}

export function getRunCompletionTiming(run: RunCardRun, endStep: Record<string, unknown> | null | undefined): RunCompletionTiming {
    let startedAt = parseApiDate(run.started_at) || parseApiDate(run.created_at);
    let completedAt = parseApiDate(run.completed_at) || parseApiDate(endStep?.completed_at) || parseApiDate(run.updated_at);

    for (const step of run.step_history || []) {
        startedAt = startedAt || parseApiDate(step.started_at);
        completedAt = getLaterDate(completedAt, parseApiDate(step.completed_at));
        completedAt = getLaterDate(completedAt, parseApiDate(step.started_at));
    }

    return {
        startedAt,
        completedAt,
        duration: formatCompletionDuration(startedAt, completedAt),
    };
}

export function getLastVisibleOutputBeforeStep(run: RunCardRun, currentStep: Record<string, unknown> | null): unknown {
    const history = (run.step_history || []) as Array<Record<string, unknown>>;
    const currentIndex = currentStep ? history.indexOf(currentStep) : history.length;
    const endIndex = currentIndex >= 0 ? currentIndex : history.length;

    for (let index = endIndex - 1; index >= 0; index -= 1) {
        const output = getVisibleStepData(history[index]?.output_data);
        if (hasVisibleData(output)) return output;
    }

    return null;
}
