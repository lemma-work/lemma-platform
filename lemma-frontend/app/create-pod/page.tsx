'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { ArrowRight, Building2, Sparkles } from '@/components/ui/icons';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { useOrganizations } from '@/lib/hooks/use-organizations';
import { ResourceIconUploader } from '@/components/shared/resource-icon-uploader';
import { PlainPageShell } from '@/components/dashboard/plain-page-shell';
import { ProductIcon } from '@/components/pod/product-icon';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
import { ProtectedRoute } from '@/components/auth/protected-route';
import {
    ResourceFeedbackBanner,
    showResourceCreatedToast,
    showResourceErrorToast,
} from '@/components/shared/resource-feedback';

const POD_IDEAS = [
    {
        name: 'Support Ops',
        description: 'Handle customer email, route requests, and prepare reply drafts for review.',
    },
    {
        name: 'Hiring Pipeline',
        description: 'Track candidates, follow-ups, interviews, and outreach across the team.',
    },
    {
        name: 'Finance Close',
        description: 'Coordinate invoices, approvals, reminders, and month-end reporting.',
    },
];

export default function CreatePodPage() {
    return <CreatePodPageContent />;
}

function CreatePodPageContent() {
    const router = useRouter();
    const { data: orgsData, isLoading: orgsLoading } = useOrganizations();
    const organizations = orgsData?.items || [];

    const [selectedOrgId, setSelectedOrgId] = useState<string>('');
    const [isCreating, setIsCreating] = useState(false);
    const [createError, setCreateError] = useState<string | null>(null);
    const [iconUrl, setIconUrl] = useState<string | null>(null);
    const [formData, setFormData] = useState({
        name: '',
        description: '',
    });

    const effectiveOrgId = selectedOrgId || organizations[0]?.id || '';
    const showWorkspacePicker = organizations.length > 1;

    const handleCreate = async (e: React.FormEvent) => {
        e.preventDefault();

        if (!effectiveOrgId || isCreating) return;

        setIsCreating(true);
        setCreateError(null);
        try {
            const podName = formData.name.trim();
            const podDescription = formData.description.trim();

            const pod = await getLemmaClient().pods.create({
                name: podName,
                description: podDescription || undefined,
                organization_id: effectiveOrgId,
                icon_url: iconUrl || undefined,
            });

            showResourceCreatedToast('Pod', podName);
            const params = new URLSearchParams({
                assistantMessage: 'Hi',
                conversationInstructions: [
                    `The user has just created this pod${podName ? ` named "${podName}"` : ''}.${podDescription ? ` They described it as: "${podDescription}".` : ''}`,
                    'Help them set this pod up for what they need. If the name or description hint at a purpose, propose concrete things you could build — agents, workflows, data tables, or apps — and offer to start. If there is nothing to infer yet, warmly ask what they want this pod to do. Keep it brief, friendly, and concrete.',
                ].join('\n\n'),
                conversationMetadata: JSON.stringify({
                    source: 'pod_created',
                    first_run: true,
                }),
            });
            router.push(`/pod/${pod.id}/conversations/new?${params.toString()}`);
        } catch (error) {
            console.error('Failed to create pod:', error);
            const message = error instanceof Error && error.message ? error.message : 'Failed to create pod';
            setCreateError(message);
            showResourceErrorToast(error, 'Failed to create pod');
        } finally {
            setIsCreating(false);
        }
    };

    return (
        <ProtectedRoute>
            <PlainPageShell
                title="Create pod"
                icon={<ProductIcon kind="pods" size="sm" />}
                backHref="/home"
                backLabel="Pods"
                meta="New AI office"
                contentWidthClassName="max-w-5xl"
                contentClassName="pb-16 sm:pb-20"
            >
                <section className="office-room-chrome office-arrive p-6 sm:p-8 lg:p-10">
                    <form onSubmit={handleCreate} className="grid gap-10 lg:grid-cols-[minmax(220px,0.72fr)_minmax(360px,1fr)] lg:items-start">
                        <div className="pt-1">
                            <p className="type-eyebrow-mono text-[var(--text-tertiary)]">New pod</p>
                            <h1 className="mt-3 max-w-sm text-2xl font-semibold leading-tight tracking-normal text-[var(--text-primary)] sm:text-3xl">
                                What should Lemma set up?
                            </h1>
                            <p className="mt-3 max-w-sm text-sm leading-6 text-[var(--text-secondary)]">
                                Name the operating space and give it a brief. You can add agents, workflows, data, and apps once it opens.
                            </p>

                            <div className="mt-6 flex flex-wrap gap-2">
                                {POD_IDEAS.map((idea) => (
                                    <button
                                        key={idea.name}
                                        type="button"
                                        className="office-chip px-3 py-1.5 text-sm"
                                        onClick={() => {
                                            setFormData({
                                                name: idea.name,
                                                description: idea.description,
                                            });
                                        }}
                                    >
                                        {idea.name}
                                    </button>
                                ))}
                            </div>
                        </div>

                        <div className="space-y-5 lg:pt-3">
                            <div className="mb-5 flex items-center gap-3">
                                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[color-mix(in_srgb,var(--delight-soft)_72%,var(--surface-1))] text-[var(--delight)]">
                                    <Sparkles className="h-5 w-5" />
                                </span>
                                <div>
                                    <p className="text-sm font-medium text-[var(--text-primary)]">Pod brief</p>
                                    <p className="mt-0.5 text-xs text-[var(--text-tertiary)]">Keep it loose. The pod can evolve after creation.</p>
                                </div>
                            </div>

                            {createError ? (
                                <ResourceFeedbackBanner
                                    tone="error"
                                    title="Pod was not created"
                                    description={createError}
                                    onDismiss={() => setCreateError(null)}
                                />
                            ) : null}

                            {showWorkspacePicker ? (
                                <div className="space-y-2">
                                    <Label htmlFor="organization">Workspace *</Label>
                                    <select
                                        id="organization"
                                        required
                                        value={effectiveOrgId}
                                        onChange={(e) => setSelectedOrgId(e.target.value)}
                                        disabled={orgsLoading}
                                        className="form-field-control flex h-11 w-full items-center px-3 py-2 text-sm text-[var(--text-primary)] outline-none"
                                    >
                                        <option value="" disabled>Select a workspace</option>
                                        {organizations.map((org) => (
                                            <option key={org.id} value={org.id}>
                                                {org.name}
                                            </option>
                                        ))}
                                    </select>
                                </div>
                            ) : null}

                            <div className="space-y-2">
                                <Label htmlFor="name">Pod name *</Label>
                                <div className="form-field-control flex h-14 items-center gap-3 px-4">
                                    <Building2 className="h-5 w-5 shrink-0 text-[var(--text-tertiary)]" />
                                    <input
                                        id="name"
                                        required
                                        placeholder="Support ops, Hiring pipeline, Finance close..."
                                        value={formData.name}
                                        onChange={(e) => setFormData((prev) => ({ ...prev, name: e.target.value }))}
                                        className="inline-edit-field min-w-0 flex-1 border-0 bg-transparent p-0 text-base text-[var(--text-primary)] outline-none placeholder:text-[var(--text-soft)]"
                                    />
                                </div>
                            </div>

                            <div className="space-y-2">
                                <Label>Pod image</Label>
                                <ResourceIconUploader
                                    kind="pod"
                                    name={formData.name || 'Pod'}
                                    value={iconUrl}
                                    onChange={setIconUrl}
                                />
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="description">Description</Label>
                                <Textarea
                                    id="description"
                                    rows={5}
                                    placeholder="What is this pod for?"
                                    value={formData.description}
                                    onChange={(e) => setFormData((prev) => ({ ...prev, description: e.target.value }))}
                                />
                            </div>

                            <div className="flex flex-col gap-3 pt-2 sm:flex-row sm:items-center">
                                <Button
                                    type="submit"
                                    disabled={!formData.name.trim() || !effectiveOrgId}
                                    loading={isCreating}
                                    loadingLabel="Creating pod"
                                    className="office-primary-action h-12 gap-2 px-5 text-sm font-medium"
                                >
                                    Create pod
                                    <ArrowRight className="h-4 w-4" />
                                </Button>
                                <Button asChild type="button" variant="ghost">
                                    <Link href="/home">Cancel</Link>
                                </Button>
                            </div>
                        </div>
                    </form>
                </section>
            </PlainPageShell>
        </ProtectedRoute>
    );
}
