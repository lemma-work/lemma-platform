import { CheckCircle2, Upload } from "@/components/ui/icons";

import { InlineLoader, LoadingState, StepLoader, WordmarkLoader } from "@/components/brand/loader";
import { Button } from "@/components/ui/button";

export default function LoadingPreviewPage() {
    return (
        <main className="min-h-screen bg-[var(--bg-canvas)] px-6 py-10 text-[var(--text-primary)]">
            <section className="mx-auto flex w-full max-w-5xl flex-col gap-8">
                <header className="flex flex-col gap-5 border-b border-[var(--border-subtle)] pb-8 sm:flex-row sm:items-end sm:justify-between">
                    <div>
                        <p className="type-eyebrow">Lemma loading system</p>
                        <h1 className="mt-3 text-3xl font-semibold tracking-normal text-[var(--text-primary)]">
                            Fast, alive, consistent
                        </h1>
                        <p className="mt-3 max-w-xl text-sm leading-6 text-[var(--text-secondary)]">
                            Route loading wakes up, actions metabolize, and background work breathes instead of spinning.
                        </p>
                    </div>
                    <div className="surface-panel-muted flex items-center gap-4 px-4 py-3">
                        <WordmarkLoader size="sm" />
                        <StepLoader size="xs" />
                    </div>
                </header>

                <div className="grid gap-5 lg:grid-cols-[1.2fr_0.8fr]">
                    <LoadingState
                        title="Gathering pods"
                        description="Your workspaces are taking shape."
                        shape="cards"
                    />

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
                            <Button loading loadingLabel="Creating pod">
                                Create pod
                            </Button>
                            <Button variant="secondary" loading loadingLabel="Uploading">
                                <Upload className="h-4 w-4" />
                                Upload
                            </Button>
                            <Button variant="ghost" className="gap-2">
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
                        <p className="type-eyebrow">Section</p>
                        <div className="mt-4 flex items-center gap-3">
                            <StepLoader size="sm" />
                            <span className="text-sm text-[var(--text-secondary)]">Reading recent runs</span>
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

                <LoadingState
                    title="Preparing your workspace"
                    description="Checking profile, organization, and invitations."
                    shape="table"
                />

                <footer className="border-t border-[var(--border-subtle)] pt-5 text-xs leading-5 text-[var(--text-tertiary)]">
                    Preview route for reviewing the shared loading primitives in real browser chrome.
                </footer>
            </section>
        </main>
    );
}
