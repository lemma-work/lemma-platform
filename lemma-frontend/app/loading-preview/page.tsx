import { CheckCircle2, Upload } from "@/components/ui/icons";

import { InlineLoader, StepLoader, WordmarkLoader } from "@/components/brand/loader";
import { Button } from "@/components/ui/button";
import {
    ListSkeleton,
    ResourceCardGridSkeleton,
    Skeleton,
    TranscriptSkeleton,
} from "@/components/shared/loading";
import { EmptyState } from "@/components/shared/empty-state";

export default function LoadingPreviewPage() {
    return (
        <main className="min-h-screen bg-[var(--bg-canvas)] px-6 py-10 text-[var(--text-primary)]">
            <section className="mx-auto flex w-full max-w-5xl flex-col gap-8">
                <header className="flex flex-col gap-5 border-b border-[var(--border-subtle)] pb-8 sm:flex-row sm:items-end sm:justify-between">
                    <div>
                        <p className="type-eyebrow">Lemma loading system</p>
                        <h1 className="mt-3 text-3xl font-semibold tracking-normal text-[var(--text-primary)]">
                            One shape, three fills
                        </h1>
                        <p className="mt-3 max-w-xl text-sm leading-6 text-[var(--text-secondary)]">
                            A region&apos;s box belongs to the settled layout. Loading and empty change
                            what is inside it, never the box itself.
                        </p>
                    </div>
                    <div className="surface-panel-muted flex items-center gap-4 px-4 py-3">
                        <WordmarkLoader size="sm" />
                        <StepLoader size="xs" />
                    </div>
                </header>

                {/* The two unsettled fills of one index region. Both occupy the
                    box the settled grid will occupy — that is the whole point. */}
                <section className="flex flex-col gap-3">
                    <p className="type-eyebrow">Index region — loading</p>
                    <ResourceCardGridSkeleton count={3} />
                </section>

                <section className="flex flex-col gap-3">
                    <p className="type-eyebrow">Index region — empty</p>
                    <EmptyState
                        variant="region"
                        title="No agents yet"
                        description="Add the first agent this pod can run."
                    />
                </section>

                <div className="grid gap-5 lg:grid-cols-[1.2fr_0.8fr]">
                    <section className="surface-panel flex flex-col gap-4 p-5">
                        <p className="type-eyebrow">List</p>
                        <ListSkeleton rows={4} />
                    </section>

                    <section className="surface-panel flex flex-col justify-between gap-6 p-5">
                        <div>
                            <p className="type-eyebrow">Buttons</p>
                            <h2 className="mt-2 text-lg font-semibold tracking-normal text-[var(--text-primary)]">
                                Loading belongs to the control
                            </h2>
                            <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">
                                The button owns width, disabled state, aria-busy, icon swap, and label tone.
                            </p>
                        </div>
                        <div className="flex flex-wrap items-center gap-3">
                            <Button variant="primary" loading loadingLabel="Creating pod">
                                Create pod
                            </Button>
                            <Button variant="secondary" loading loadingLabel="Uploading">
                                <Upload className="h-4 w-4" />
                                Upload
                            </Button>
                            <Button variant="quiet" className="gap-2">
                                Saved
                                <CheckCircle2 className="h-4 w-4 text-[var(--state-success)]" />
                            </Button>
                        </div>
                    </section>
                </div>

                <div className="grid gap-5 md:grid-cols-3">
                    <section className="surface-panel p-5">
                        <p className="type-eyebrow">Inline</p>
                        <div className="mt-4">
                            <InlineLoader size="xs" label="Checking access" />
                        </div>
                    </section>
                    <section className="surface-panel p-5">
                        <p className="type-eyebrow">Atom</p>
                        <div className="mt-4 space-y-2">
                            <Skeleton shape="block" className="h-8 w-full" />
                            <Skeleton className="h-3 w-3/5" />
                        </div>
                    </section>
                    <section className="surface-panel p-5">
                        <p className="type-eyebrow">Settle</p>
                        <div className="mt-4 flex items-center gap-3 text-sm text-[var(--state-success)]">
                            <CheckCircle2 className="h-4 w-4" />
                            Saved, then return to rest.
                        </div>
                    </section>
                </div>

                <section className="surface-panel flex flex-col gap-4 p-5">
                    <p className="type-eyebrow">Transcript</p>
                    <TranscriptSkeleton turns={2} />
                </section>

                <footer className="border-t border-[var(--border-subtle)] pt-5 text-xs leading-5 text-[var(--text-tertiary)]">
                    Preview route for reviewing the shared loading primitives in real browser chrome.
                </footer>
            </section>
        </main>
    );
}
