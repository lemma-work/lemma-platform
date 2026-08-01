'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ArrowRight, ExternalLink, ShieldCheck, Sparkles } from '@/components/ui/icons';

import { ProtectedRoute } from '@/components/auth/protected-route';
import { Logo } from '@/components/brand/logo';
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
                    <Button variant="quiet" className="mt-5" onClick={() => router.push('/')}>
                        Go to Lemma
                    </Button>
                </section>
            </PlainPageShell>
        );
    }

    const sourceLabel = remixSourceLabel(source);

    return (
        <main className="github-import-page remix-page">
            <div className="github-import-stationery" aria-hidden="true">
                <span className="github-import-stationery-botanical" />
                <span className="github-import-stationery-upgrade" />
                <span className="github-import-stationery-riso" />
            </div>

            <header className="github-import-header">
                <div className="github-import-header-inner">
                    <div className="remix-header-identity">
                        <Link href="/" aria-label="Lemma home">
                            <Logo size="sm" variant="mark-wordmark" />
                        </Link>
                        <span>
                            <Sparkles className="h-3.5 w-3.5" />
                            Remix
                        </span>
                    </div>
                    <a
                        href={source}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="github-import-header-source"
                    >
                        <span className="remix-header-source-desktop">{sourceLabel}</span>
                        <span className="remix-header-source-mobile">Source</span>
                        <ExternalLink className="h-3.5 w-3.5 shrink-0" />
                    </a>
                </div>
            </header>

            <div className="remix-stage">
                <aside className="remix-command-wrap">
                    <div className="github-import-installer remix-command">
                        <span className="remix-command-icon" aria-hidden="true">
                            <Sparkles className="h-4 w-4" />
                        </span>
                        <div className="github-import-installer-copy">
                            <p className="github-import-installer-eyebrow">Remix on Lemma</p>
                            <h1>Make it yours.</h1>
                            <p>Choose the pod where you want to continue.</p>
                        </div>

                        <a
                            href={source}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="remix-source-link"
                        >
                            <span>
                                <small>From</small>
                                <strong>{sourceLabel}</strong>
                            </span>
                            <ExternalLink className="h-4 w-4" />
                        </a>

                        {pods.length ? (
                            <div className="remix-command-target">
                                <label htmlFor="remix-pod">Remix into</label>
                                <Select value={effectivePodId} onValueChange={setSelectedPodId}>
                                    <SelectTrigger id="remix-pod">
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
                                <Button variant="primary"
                                    className="remix-command-action"
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
                        ) : (
                            <div className="remix-command-empty">
                                <p>
                                    Create a pod for this remix. The source app will follow
                                    you into its first assistant conversation.
                                </p>
                                <Button variant="secondary"
                                    className="remix-command-action"
                                    onClick={() =>
                                        router.push(buildCreatePodForRemixHref(source))
                                    }
                                >
                                    Create a pod
                                    <ArrowRight className="h-4 w-4" />
                                </Button>
                            </div>
                        )}

                        <p className="github-import-assurance">
                            <ShieldCheck className="h-3.5 w-3.5" />
                            Private data, integrations, and hidden workflows are rebuilt
                            only when you provide access.
                        </p>
                    </div>
                </aside>
            </div>
        </main>
    );
}
