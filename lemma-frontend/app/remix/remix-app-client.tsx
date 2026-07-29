'use client';

import { useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowRight, ExternalLink, Sparkles } from '@/components/ui/icons';

import { ProtectedRoute } from '@/components/auth/protected-route';
import { LemmaMark } from '@/components/brand/logo';
import { PageLoader } from '@/components/brand/loader';
import { PlainPageShell } from '@/components/dashboard/plain-page-shell';
import { Button } from '@/components/ui/button';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';
import { useAccessiblePods } from '@/lib/hooks/use-pods';
import { readLastOpenedPodId } from '@/lib/pods/last-opened-pod';
import {
    buildAppRemixConversationHref,
    buildCreatePodForRemixHref,
    normalizeRemixSource,
    remixSourceLabel,
} from '@/lib/remix/app-remix';

export function RemixAppClient({ source: rawSource }: { source?: string }) {
    return (
        <ProtectedRoute>
            <RemixAppLanding rawSource={rawSource} />
        </ProtectedRoute>
    );
}

function RemixAppLanding({ rawSource }: { rawSource?: string }) {
    const router = useRouter();
    const { data, isLoading } = useAccessiblePods();
    const [selectedPodId, setSelectedPodId] = useState('');
    const source = normalizeRemixSource(rawSource);
    const rememberedPodId = useMemo(() => readLastOpenedPodId(), []);

    if (isLoading) {
        return <PageLoader />;
    }

    const pods = data.items;
    const rememberedPod = pods.find((pod) => pod.id === rememberedPodId);
    const effectivePodId = selectedPodId || rememberedPod?.id || pods[0]?.id || '';
    const effectivePod = pods.find((pod) => pod.id === effectivePodId);

    if (!source) {
        return (
            <PlainPageShell
                title="Remix on Lemma"
                backHref="/"
                backLabel="Home"
                contentWidthClassName="max-w-xl"
                centerContent
            >
                <section className="surface-panel p-6 text-center sm:p-8">
                    <h1 className="text-xl font-semibold text-[var(--text-primary)]">
                        This remix link is incomplete
                    </h1>
                    <p className="mx-auto mt-2 max-w-sm text-sm leading-6 text-[var(--text-secondary)]">
                        Open the Remix on Lemma badge from a hosted app so its source can be
                        handed to your assistant.
                    </p>
                    <Button className="mt-5" onClick={() => router.push('/')}>
                        Go to Lemma
                    </Button>
                </section>
            </PlainPageShell>
        );
    }

    return (
        <PlainPageShell
            title="Remix on Lemma"
            icon={<LemmaMark size="xs" />}
            backHref="/"
            backLabel="Home"
            contentWidthClassName="max-w-xl"
            centerContent
        >
            <section className="surface-panel overflow-hidden">
                <div className="border-b border-[var(--border-subtle)] p-6 sm:p-8">
                    <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-[var(--surface-2)] text-[var(--delight)]">
                        <Sparkles className="h-5 w-5" />
                    </span>
                    <h1 className="mt-5 text-2xl font-semibold tracking-tight text-[var(--text-primary)]">
                        Make it yours.
                    </h1>
                    <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">
                        Your pod assistant will inspect the app, understand what makes it
                        useful, and rebuild or adapt it with you.
                    </p>

                    <a
                        href={source}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="mt-5 inline-flex max-w-full items-center gap-2 rounded-full border border-[var(--border-subtle)] bg-[var(--surface-2)] px-3 py-1.5 text-xs font-medium text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
                    >
                        <span className="truncate">{remixSourceLabel(source)}</span>
                        <ExternalLink className="h-3.5 w-3.5 shrink-0" />
                    </a>
                </div>

                <div className="p-6 sm:p-8">
                    {pods.length ? (
                        <>
                            <label
                                htmlFor="remix-pod"
                                className="text-sm font-medium text-[var(--text-primary)]"
                            >
                                Remix into
                            </label>
                            <div className="mt-2.5 flex flex-col gap-2 sm:flex-row">
                                <Select value={effectivePodId} onValueChange={setSelectedPodId}>
                                    <SelectTrigger id="remix-pod" className="sm:flex-1">
                                        <SelectValue placeholder="Choose a pod" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {pods.map((pod) => (
                                            <SelectItem key={pod.id} value={pod.id}>
                                                {pod.name}
                                                {data.hasMultipleOrganizations && pod.organization_name
                                                    ? ` · ${pod.organization_name}`
                                                    : ''}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                                <Button
                                    className="gap-2 sm:w-auto"
                                    disabled={!effectivePod}
                                    onClick={() => {
                                        if (!effectivePod) return;
                                        router.push(
                                            buildAppRemixConversationHref(effectivePod.id, source),
                                        );
                                    }}
                                >
                                    Start remix
                                    <ArrowRight className="h-4 w-4" />
                                </Button>
                            </div>
                            <p className="mt-3 text-xs leading-5 text-[var(--text-tertiary)]">
                                The assistant can inspect what you can access. Private data,
                                integrations, and hidden workflows are rebuilt only when you
                                provide access.
                            </p>
                        </>
                    ) : (
                        <>
                            <p className="text-sm leading-6 text-[var(--text-secondary)]">
                                Create a pod for this remix. The source app will follow you
                                into its first assistant conversation.
                            </p>
                            <Button
                                className="mt-4 gap-2"
                                onClick={() => router.push(buildCreatePodForRemixHref(source))}
                            >
                                Create a pod
                                <ArrowRight className="h-4 w-4" />
                            </Button>
                        </>
                    )}
                </div>
            </section>
        </PlainPageShell>
    );
}
