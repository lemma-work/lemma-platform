import {
    ListSkeleton,
    ResourceCardGridSkeleton,
    Skeleton,
    SkeletonText,
    TranscriptSkeleton,
} from '@/components/shared/loading';

/**
 * Route-level shapes, one per kind of pod page.
 *
 * There used to be a single `loading.tsx` at `app/pod/[id]/` and every nested
 * route inherited it, so clicking Functions (a list), Settings (a form), the
 * flow editor (a canvas), or the pod home (a composer) all flashed the same
 * three-card grid first. That is the shape-mismatch this whole effort is about,
 * only worse for being the *first* thing you see on every navigation.
 *
 * So each route declares its own. What is shared here is a vocabulary of page
 * *kinds*, not one skeleton pretending to fit them all — a route picks the kind
 * it settles into, and the per-route file stays a single line. Lists and card
 * indexes genuinely do repeat across routes; forms, canvases, and the composer
 * home do not, and each gets its own shape.
 */

/** The strip is chrome — its labels are known before any query is. */
function MetricStripPlaceholder({ tabs = 3 }: { tabs?: number }) {
    return (
        <div className="lemma-index-tabs flex-wrap" data-skeleton="true">
            <div className="flex flex-wrap items-center gap-2">
                {Array.from({ length: tabs }).map((_, index) => (
                    <span key={index} className="lemma-index-tab">
                        <Skeleton className="h-3 w-16" />
                    </span>
                ))}
            </div>
        </div>
    );
}

/** Agents, workflows, connectors, recipes, app pages — a grid of cards. */
export function PodIndexCardsSkeleton({ tabs = 3, cards = 3 }: { tabs?: number; cards?: number }) {
    return (
        <div className="resource-index-shell context-shell min-h-full bg-transparent" role="status" aria-label="Loading">
            <MetricStripPlaceholder tabs={tabs} />
            <ResourceCardGridSkeleton count={cards} />
        </div>
    );
}

/** Functions, triggers — dense rows, not cards. */
export function PodIndexListSkeleton({ tabs = 1, rows = 6 }: { tabs?: number; rows?: number }) {
    return (
        <div className="resource-index-shell context-shell min-h-full bg-transparent" role="status" aria-label="Loading">
            <MetricStripPlaceholder tabs={tabs} />
            <ListSkeleton rows={rows} />
        </div>
    );
}

/** A single resource read top to bottom — identity card, then its sections. */
export function PodDetailSkeleton({ sections = 2 }: { sections?: number }) {
    return (
        <div className="flex h-full min-h-0 flex-col bg-[var(--bg-canvas)]" role="status" aria-label="Loading">
            <div className="resource-page-scroll">
                <div className="resource-page-column">
                    <section className="resource-card">
                        <div className="flex items-start gap-3">
                            <Skeleton shape="block" className="h-8 w-8 rounded-xl" />
                            <div className="min-w-0 flex-1 space-y-2">
                                <Skeleton shape="block" className="h-5 w-44" />
                                <Skeleton className="h-3 w-4/5" />
                            </div>
                        </div>
                    </section>
                    {Array.from({ length: sections }).map((_, index) => (
                        <section key={index} className="resource-card">
                            <Skeleton className="h-3 w-28" />
                            <div className="mt-3">
                                <SkeletonText lines={4} />
                            </div>
                        </section>
                    ))}
                </div>
            </div>
        </div>
    );
}

/** The "new X" builders — a header bar over a wide form column. */
export function PodBuilderSkeleton() {
    return (
        <div className="agent-builder-root flex h-full min-h-0 flex-col" role="status" aria-label="Loading">
            <div className="flex h-12 shrink-0 items-center justify-between gap-3 px-4">
                <Skeleton shape="block" className="h-5 w-40" />
                <Skeleton shape="block" className="h-8 w-24" />
            </div>
            <div className="min-h-0 flex-1 overflow-hidden p-6">
                <div className="mx-auto w-full max-w-3xl space-y-5">
                    <Skeleton shape="block" className="h-9 w-2/3" />
                    <Skeleton shape="block" className="h-24 w-full" />
                    <Skeleton shape="block" className="h-9 w-full" />
                    <Skeleton shape="block" className="h-9 w-full" />
                </div>
            </div>
        </div>
    );
}

/** The flow and function editors — a working surface with a docked panel. */
export function PodEditorSkeleton() {
    return (
        <div className="flex h-full min-h-0 overflow-hidden bg-transparent" role="status" aria-label="Loading">
            <div className="flex min-w-0 flex-1 flex-col">
                <div className="flex h-12 shrink-0 items-center justify-between gap-3 px-4">
                    <div className="flex items-center gap-2">
                        <Skeleton shape="block" className="h-5 w-5" />
                        <Skeleton shape="block" className="h-5 w-40" />
                    </div>
                    <div className="flex items-center gap-2">
                        <Skeleton shape="block" className="h-8 w-16" />
                        <Skeleton shape="block" className="h-8 w-8" />
                    </div>
                </div>
                <div className="min-h-0 flex-1 space-y-6 p-8">
                    <Skeleton shape="block" className="h-10 w-64" />
                    <Skeleton shape="block" className="h-40 w-full" />
                    <Skeleton shape="block" className="h-24 w-full" />
                </div>
            </div>
            <div className="hidden w-[26rem] shrink-0 border-l border-[color:color-mix(in_srgb,var(--border-subtle)_35%,transparent)] p-4 lg:block">
                <Skeleton shape="block" className="h-8 w-32" />
                <div className="mt-4 space-y-3">
                    <Skeleton shape="block" className="h-28 w-full" />
                    <Skeleton shape="block" className="h-9 w-full" />
                </div>
            </div>
        </div>
    );
}

/** Settings pages — labelled fields in a narrow column. */
export function PodSettingsSkeleton({ rows = 5 }: { rows?: number }) {
    return (
        <div className="context-shell min-h-full bg-transparent" role="status" aria-label="Loading">
            <div className="flex w-full max-w-3xl flex-col gap-5">
                {Array.from({ length: rows }).map((_, index) => (
                    <div key={index} className="space-y-2">
                        <Skeleton className="h-3 w-24" />
                        <Skeleton shape="block" className="h-9 w-full" />
                    </div>
                ))}
            </div>
        </div>
    );
}

/**
 * Conversation surfaces — bottom-anchored, with the composer already in place.
 * Anchoring matters: a transcript that fills from the top and then jumps to the
 * bottom is the load being visible twice.
 */
export function PodConversationSkeleton() {
    return (
        <div className="flex h-full min-h-0 flex-col bg-[var(--pod-main-bg)]" role="status" aria-label="Loading conversation">
            {/* Same `TranscriptSkeleton` and the same box the conversation page
                itself uses while it selects the conversation, so the route
                boundary handing over to the page is invisible rather than a
                second screen. */}
            <div className="mx-auto flex h-full min-h-0 w-full max-w-4xl flex-col justify-end gap-6 px-6 pb-6">
                <TranscriptSkeleton turns={2} />
                <Skeleton shape="block" className="h-24 w-full rounded-2xl" />
            </div>
        </div>
    );
}

/**
 * The pod home — one composer in the middle of the page and nothing else until
 * we know whether this pod has an activity region. A card grid here was the
 * most wrong of all: the settled page has no cards at the top at all.
 */
export function PodHomeSkeleton() {
    return (
        <div className="flex min-h-full flex-col bg-[var(--pod-main-bg)]" role="status" aria-label="Loading">
            <div className="mx-auto flex min-h-full w-full max-w-6xl flex-1 flex-col items-center px-5 pb-10 pt-8 sm:px-6 md:pt-12">
                <div className="w-full max-w-4xl space-y-4">
                    <Skeleton shape="block" className="mx-auto h-8 w-64" />
                    <Skeleton shape="block" className="h-16 w-full rounded-2xl" />
                </div>
            </div>
        </div>
    );
}
