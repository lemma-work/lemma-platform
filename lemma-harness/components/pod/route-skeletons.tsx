import {
    ListSkeleton,
    ResourceCardGridSkeleton,
    Skeleton,
    SkeletonText,
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

/**
 * Settings bodies, without the shell around them.
 *
 * A settings page that is still fetching its pod keeps its header, its nav and
 * its width, and fills only the part it does not know yet — so these are the
 * shape the *body* settles into. `PodSettingsShell` is still mounted around
 * them. The `*Skeleton` exports below wrap the same fills for `loading.tsx`,
 * where there is no shell yet to sit inside.
 */
export function PodSettingsPanelsFill({ panels = 3 }: { panels?: number }) {
    return (
        <div className="settings-stack" role="status" aria-label="Loading">
            {Array.from({ length: panels }).map((_, index) => (
                <div key={index} className="resource-panel overflow-hidden rounded-lg border border-[var(--card-border-subtle)] bg-[var(--card-bg)] shadow-[var(--card-shadow)]">
                    <div className="border-b border-[var(--card-border-subtle)] px-4 py-3">
                        <Skeleton className="h-3.5 w-32" />
                        <Skeleton className="mt-2 h-3 w-64" />
                    </div>
                    <div className="space-y-2 px-4 py-4">
                        <Skeleton shape="block" className="h-9 w-full" />
                        <Skeleton shape="block" className="h-9 w-full" />
                    </div>
                </div>
            ))}
        </div>
    );
}

/** Access and Automation settle into a ledger under a count strip, not a form. */
export function PodSettingsLedgerFill({ tabs = 4, rows = 6 }: { tabs?: number; rows?: number }) {
    return (
        <div role="status" aria-label="Loading">
            <MetricStripPlaceholder tabs={tabs} />
            <ListSkeleton rows={rows} />
        </div>
    );
}

/**
 * Models settles into the pod default over the list it picks from — no count
 * strip, so it must not borrow the ledger fill: `.lemma-index-tabs` carries a
 * border and a background, and an empty one is a band of chrome the arriving
 * page never draws.
 */
export function PodModelsFill({ rows = 4 }: { rows?: number }) {
    return (
        <div className="space-y-5" role="status" aria-label="Loading">
            <SkeletonText lines={2} className="max-w-2xl" />
            <Skeleton shape="block" className="h-16 w-full rounded-lg" />
            <ListSkeleton rows={rows} />
        </div>
    );
}

/**
 * The same two fills at route level, for `loading.tsx`.
 *
 * They carry the width the shell would have given them (`max-w-6xl`), because a
 * skeleton at one width handing over to content at another is the load being
 * visible twice — which is what all four settings tabs used to do.
 */
export function PodSettingsSkeleton({ panels = 3 }: { panels?: number }) {
    return (
        <div className="resource-index-shell context-shell min-h-full bg-transparent">
            <div className="w-full max-w-6xl">
                <PodSettingsPanelsFill panels={panels} />
            </div>
        </div>
    );
}

export function PodModelsSkeleton({ rows = 4 }: { rows?: number }) {
    return (
        <div className="resource-index-shell context-shell min-h-full bg-transparent">
            {/* Matches the route's `width="form"` shell, so the skeleton and the
                content that replaces it share one measure. */}
            <div className="w-full max-w-3xl">
                <PodModelsFill rows={rows} />
            </div>
        </div>
    );
}

export function PodSettingsLedgerSkeleton({ tabs = 4, rows = 6 }: { tabs?: number; rows?: number }) {
    return (
        <div className="resource-index-shell context-shell min-h-full bg-transparent">
            <div className="w-full max-w-6xl">
                <PodSettingsLedgerFill tabs={tabs} rows={rows} />
            </div>
        </div>
    );
}

/**
 * A conversation — an empty transcript above a real composer.
 *
 * There is no placeholder in the transcript, and that is the point. A skeleton
 * claims that a known amount of content is on its way; a transcript can be two
 * hundred turns, one turn, or — on `/conversations/new`, the commonest way into
 * chat — none at all. `loading.tsx` takes no params, so this boundary cannot
 * even tell which of those it is in front of. Two grey turns that resolve to an
 * empty composer is a promise broken on the busiest path in the product, and in
 * a bottom-anchored scroller a guessed height is one the real messages re-flow
 * anyway — so the placeholder fails at the only job it has.
 *
 * What *is* drawn is the composer, because the composer is not content. It is
 * chrome: it takes no data, it looks the same whatever the transcript turns out
 * to hold, and it is still there afterwards. So it renders as itself, in the
 * real classes, rather than as a grey block pretending to be itself. The
 * distinction the doc draws for tables holds here too — the frame is drawn
 * because it survives the load; the fill is not.
 */
export function PodConversationSkeleton() {
    return (
        <div
            className="flex h-full min-h-0 flex-col bg-[var(--pod-main-bg)]"
            role="status"
            aria-label="Loading conversation"
            aria-busy="true"
        >
            {/* The transcript's box, held open and empty. Bottom-anchored, so
                the first real message lands at the bottom edge either way. The
                label above is what carries the wait now that nothing is drawn:
                a reader who cannot see the blank region is still told. */}
            <div className="min-h-0 flex-1" />
            <ConversationComposerRail />
        </div>
    );
}

/**
 * The composer rail, in the same class names `AssistantComposer` and
 * `assistantComposerInputClassName` use at `density="spacious"`.
 *
 * Reusing the classes rather than measuring by eye is what makes the handover
 * invisible: the box here is computed by the same CSS that computes the settled
 * box, so the real composer replaces this one without moving a pixel. The
 * helper is not imported — it lives beside `react-markdown`, and a route
 * boundary must not pull the renderer in to draw an empty box.
 */
function ConversationComposerRail() {
    return (
        <div className="lemma-assistant-composer flex shrink-0 flex-col gap-2 border-t border-transparent bg-transparent px-4 pb-3 pt-2 sm:px-6">
            <div className="min-h-0" />
            <div className="mx-auto w-full min-w-0 max-w-4xl">
                <div className="lemma-assistant-composer-input lemma-assistant-composer-input-shell pod-assistant-inputbar relative flex min-h-24 flex-col gap-2 border-0 px-5 py-4 rounded-2xl" />
            </div>
        </div>
    );
}

/**
 * The conversations index — a header over a ledger, not a transcript.
 *
 * This route used to inherit `PodConversationSkeleton`, so clicking
 * **Conversations** drew a bottom-anchored transcript and a composer in front
 * of a page that settles into a top-anchored `h-14` header and a list of rows.
 * Same shape mismatch the per-route boundaries exist to prevent, on the one
 * route named after the shape it was borrowing.
 *
 * The header and the strip's labels are known here without any query — the
 * title is a literal — so they render for real, and only the counts and the
 * rows wait. Counts print `—` rather than `0`: a number we have not fetched is
 * a fact we are inventing.
 */
export function PodConversationIndexSkeleton({ rows = 5 }: { rows?: number }) {
    return (
        <div className="flex min-h-full flex-col bg-[var(--pod-main-bg)]">
            <header className="pod-shell-topbar flex h-14 shrink-0 items-center px-4 sm:px-6 lg:px-8">
                <div className="flex h-8 w-full items-center justify-between gap-3">
                    <h1 className="min-w-0 truncate text-sm font-medium leading-none text-[var(--text-primary)]">
                        Conversations
                    </h1>
                </div>
            </header>
            <div className="px-4 pb-8 pt-5 sm:px-6 lg:px-8" role="status" aria-label="Loading conversations">
                <div className="space-y-4">
                    {/* Real labels, `—` counts. `MetricStripPlaceholder` above
                        shimmers its labels because those routes' tabs are
                        data-dependent; these three are literals, so shimmering
                        them would be waiting on something already known — and a
                        bar that becomes the word "running" is a wider box than
                        the word, so the row would resize on arrival. */}
                    <div className="lemma-index-tabs flex-wrap">
                        <div className="flex flex-wrap items-center gap-2 text-sm text-[var(--text-tertiary)]">
                            {CONVERSATION_METRIC_LABELS.map((label, index) => (
                                <span key={label} className="lemma-index-tab" data-active={index === 0 || undefined}>
                                    <strong className="font-medium text-[var(--text-primary)]" aria-hidden="true">—</strong>
                                    <span>{label}</span>
                                </span>
                            ))}
                        </div>
                    </div>
                    {/* The same two-line row `PodConversationList` draws while it
                        loads, at the same `min-h-12` — the list takes over from
                        this without the rows changing height. */}
                    <div className="lemma-index-list gap-1">
                        {CONVERSATION_ROW_WIDTHS.slice(0, rows).map((width, index) => (
                            <div key={index} className="px-1 py-1">
                                <div className="flex min-h-12 flex-col justify-center gap-1.5 px-1.5">
                                    <Skeleton className={`h-3 ${width}`} />
                                    <Skeleton className="h-2.5 w-20" />
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            </div>
        </div>
    );
}

/** Mirrors `CONVERSATION_SKELETON_WIDTHS` in `PodConversationList`. */
const CONVERSATION_ROW_WIDTHS = ['w-3/5', 'w-5/12', 'w-2/3', 'w-1/2', 'w-7/12'];

/** Mirrors the strip `PodConversationList` renders at `variant="page"`. */
const CONVERSATION_METRIC_LABELS = ['conversations', 'running', 'recent'];

/**
 * The pod home — the composer, and nothing above it.
 *
 * Two things were wrong with the version that shimmered a title bar over a
 * shimmered composer. The composer is chrome: it is static markup that reads no
 * data, so a grey block standing in for it is a placeholder for something we
 * could simply draw. And the region above it is not one shape but two — a fresh
 * pod opens with a left-aligned `text-4xl` starter hero and a theme picker, an
 * established one opens with nothing there at all — decided by a query this
 * boundary has not run. A centred `w-64` bar was neither.
 *
 * So: draw the composer, because we know it; draw nothing above it, because we
 * do not know it yet. Only the thing that knows the shape may draw the shape.
 */
export function PodHomeSkeleton() {
    return (
        <div className="flex min-h-full flex-col bg-[var(--pod-main-bg)]" role="status" aria-label="Loading" aria-busy="true">
            <div className="mx-auto flex min-h-full w-full max-w-6xl flex-1 flex-col items-center px-5 pb-10 pt-8 sm:px-6 md:pt-12">
                <div className="w-full max-w-4xl">
                    <div className="form-field-control flex min-h-16 items-center gap-2 px-3" />
                </div>
            </div>
        </div>
    );
}
