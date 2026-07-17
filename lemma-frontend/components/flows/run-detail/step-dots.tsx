import { CheckCircle, Circle, Loader2, XCircle } from '@/components/ui/icons';
import { cn } from '@/lib/utils';
import { getNodeIconElement, type ProcedureStepState } from '../run-format';

export function InlineStepDot({ state, active }: { state: ProcedureStepState; active: boolean }) {
    if (state === 'completed') return <CheckCircle className="h-3.5 w-3.5 shrink-0 text-[var(--state-success)]" />;
    if (state === 'running') return <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-[var(--text-primary)]" />;
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
            {state === 'running' ? <Loader2 className="h-4 w-4 animate-spin" /> : getNodeIconElement(type)}
        </span>
    );
}
