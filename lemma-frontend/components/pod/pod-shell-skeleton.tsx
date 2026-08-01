import { Skeleton } from '@/components/shared/loading';
import { cn } from '@/lib/utils';

/** Tab titles vary, so the label bars do — five is a typical open set. */
const TAB_PLACEHOLDER_WIDTHS = ['w-10', 'w-12', 'w-14', 'w-16', 'w-12'];

/** Conversation titles vary too; equal bars would read as a table. */
const SIDEBAR_ROW_WIDTHS = ['w-3/5', 'w-5/12', 'w-2/3', 'w-1/2', 'w-7/12', 'w-2/5'];

/**
 * The pod frame, before the pod record has loaded.
 *
 * Geometry matches `PodShell` exactly — the 17.5rem nav slot, the 3rem tab bar,
 * the 3rem context bar — so when the real shell arrives nothing moves. It
 * replaced a full-screen wordmark that collapsed sidebar, topbar, and content to
 * a centred mark and then repainted everything.
 *
 * **The content pane is deliberately empty.** The shell has no idea which page
 * it is about to hold — a card grid, a list, a form, a canvas, a transcript —
 * and a first version of this drew a three-card index skeleton here regardless.
 * On a conversation URL that meant a card grid, then a *second* card grid from
 * the access check, and only then the conversation's own shape: three loading
 * states, two of them describing a page that was never coming.
 *
 * Page shape belongs to the page. Each route owns a `loading.tsx` that fills
 * this pane correctly (see `components/pod/route-skeletons.tsx`); the shell's
 * only job is to stop the frame from moving while that happens.
 */
export function PodShellSkeleton() {
    return (
        <div
            className="flex h-screen overflow-hidden bg-[var(--pod-shell-bg)] text-[var(--text-primary)]"
            role="status"
            aria-label="Loading pod"
        >
            {/* Five bands, in the order `WorkspaceSidebar` has them: pod header,
                the primary action, the conversation list, the places nav, and the
                account footer. Row heights come from the real rules — `h-8` for
                the action and conversations, `1.75rem` for a place row — so the
                nav does not reshuffle when the real one arrives. */}
            <div className="pod-sidebar-slot hidden h-full w-[17.5rem] shrink-0 overflow-hidden md:block">
                <div className="flex h-full flex-col">
                    <div className="flex h-12 shrink-0 items-center gap-2 px-3">
                        <Skeleton shape="block" className="h-6 w-6" />
                        <Skeleton className="h-3 w-28 flex-1" />
                        <Skeleton shape="block" className="h-8 w-8 shrink-0" />
                    </div>

                    <div className="shrink-0 px-3 pt-3">
                        <Skeleton shape="block" className="h-8 w-full" />
                    </div>

                    <div className="min-h-0 flex-1 space-y-px overflow-hidden px-3 pt-3">
                        {SIDEBAR_ROW_WIDTHS.map((width, index) => (
                            <div key={index} className="flex h-8 items-center gap-3 px-2.5">
                                <Skeleton shape="circle" className="h-1.5 w-1.5 shrink-0" />
                                <Skeleton className={cn('h-3', width)} />
                            </div>
                        ))}
                    </div>

                    <div className="shrink-0 space-y-0.5 px-3 pb-3 pt-3">
                        {[0, 1, 2, 3, 4, 5].map((item) => (
                            <div key={item} className="flex h-7 items-center gap-3 px-2.5">
                                <Skeleton shape="block" className="h-3.5 w-3.5 shrink-0" />
                                <Skeleton className="h-3 w-20" />
                            </div>
                        ))}
                    </div>

                    <div className="flex shrink-0 items-center gap-1.5 border-t border-[color:color-mix(in_srgb,var(--border-subtle)_62%,transparent)] px-3 pb-3 pt-2">
                        <Skeleton shape="block" className="h-9 w-9 shrink-0 rounded-lg" />
                        <Skeleton className="h-3 w-24" />
                    </div>
                </div>
            </div>

            <main className="pod-workspace-main flex min-w-0 flex-1 flex-col overflow-hidden">
                <header className="pod-shell-topbar pod-workspace-tabbar flex h-12 shrink-0 items-center justify-between gap-4 bg-[var(--pod-main-bg)] px-3">
                    {/* The real strip: `flex h-8 gap-px`, tabs at their own
                        min-widths, each an icon plus a label, and the new-tab
                        button on the end. A single wide bar here read as one
                        control where five sit. */}
                    <nav className="no-scrollbar flex h-8 min-w-0 flex-1 items-center gap-px overflow-hidden">
                        {TAB_PLACEHOLDER_WIDTHS.map((width, index) => (
                            <span
                                key={index}
                                className={cn(
                                    'inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md border border-transparent pl-2.5 pr-3',
                                    index === 0 ? 'min-w-[6.25rem]' : 'min-w-[7.5rem] max-w-[12rem]',
                                )}
                            >
                                <Skeleton shape="block" className="h-3.5 w-3.5 shrink-0" />
                                <Skeleton className={cn('h-3', width)} />
                            </span>
                        ))}
                        <Skeleton shape="block" className="ml-0.5 h-7 w-7 shrink-0" />
                    </nav>
                    <div className="flex h-7 shrink-0 items-center gap-1.5">
                        <Skeleton shape="block" className="h-7 w-7" />
                    </div>
                </header>
                <header className="pod-shell-topbar pod-shell-contextbar flex h-12 shrink-0 items-center justify-between gap-4 bg-[var(--pod-main-bg)] px-4">
                    <Skeleton className="h-4 w-36" />
                    <Skeleton shape="block" className="h-7 w-24" />
                </header>
                <div className="pod-page-scroll min-h-0 flex-1 overflow-hidden border-l border-[color:color-mix(in_srgb,var(--border-subtle)_62%,transparent)] bg-[var(--pod-main-bg)]" />
            </main>
        </div>
    );
}
