import { cn } from '@/lib/utils';
import { StepLoader } from '@/components/brand/loader';

/**
 * A whole screen whose only job is to wait — setup preflight, accepting an
 * invitation, resolving which organization you are creating in.
 *
 * This is the one case where a caption is the honest answer, because there is
 * no settled layout underneath to imitate: the page's entire content *is* the
 * wait, and what comes next is a different page. Everywhere else, a region that
 * settles into cards or rows or a table should render a skeleton of that shape
 * instead — see `components/shared/loading/skeletons.tsx`.
 *
 * Deliberately no placeholder bars. The version this replaced drew three grey
 * rectangles under the caption, which promised a shape the next screen never
 * had.
 */
export function WaitingScreen({
    title = 'Loading',
    description,
    className,
}: {
    title?: string;
    description?: string;
    className?: string;
}) {
    return (
        <div
            className={cn(
                'flex min-h-[12rem] flex-col items-center justify-center gap-4 px-6 py-10 text-center',
                className
            )}
            role="status"
            aria-live="polite"
        >
            <StepLoader size="sm" />
            <div className="min-w-0">
                <p className="text-base font-medium text-[var(--text-primary)]">{title}</p>
                {description ? (
                    <p className="mt-1 text-sm text-[var(--text-tertiary)]">{description}</p>
                ) : null}
            </div>
        </div>
    );
}
