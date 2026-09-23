import { CheckCircle, Circle, XCircle } from '@/components/ui/icons';
import { cn } from '@/lib/utils';
import { getNodeIconElement, type ProcedureStepState } from '../run-format';
import { StepLoader } from '@/components/brand/loader';

export function InlineStepDot({ state, active }: { state: ProcedureStepState; active: boolean }) {
    if (state === 'completed') return <CheckCircle className="h-3.5 w-3.5 shrink-0 text-[var(--state-success)]" />;
    if (state === 'running') return <StepLoader size="xs" className="h-3.5 shrink-0 text-[var(--text-primary)]" />;
    if (state === 'failed') return <XCircle className="h-3.5 w-3.5 shrink-0 text-[var(--state-error)]" />;
    if (active || state === 'waiting') return <span className="h-3.5 w-3.5 shrink-0 rounded-full border border-[var(--text-primary)]" />;
    return <Circle className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" />;
}

export function StepDot({ state, type }: { state: ProcedureStepState; type: string | undefined }) {
    return (
        <span
            className={cn(
                'relative z-10 flex h-10 w-10 shrink-0 items-center justify-center rounded-full border bg-[var(--card-bg)]',
                state === 'completed' && 'state-badge-success',
                state === 'running' && 'state-badge-info',
                state === 'waiting' && 'state-badge-warning',
                state === 'failed' && 'state-badge-error',
                (state === 'next' || state === 'pending') && 'border-[var(--border-subtle)] text-[var(--text-tertiary)]'
            )}
        >
            {state === 'running' ? <StepLoader size="sm" /> : getNodeIconElement(type)}
        </span>
    );
}
