'use client';

import { use, useMemo, useState } from 'react';
import { Download, Github, Plus } from '@/components/ui/icons';

import { ProtectedRoute } from '@/components/auth/protected-route';
import { PlainPageShell } from '@/components/dashboard/plain-page-shell';
import { Button } from '@/components/ui/button';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';
import { ImportDialog } from '@/components/bundle/import-dialog';
import { useAccessiblePods } from '@/lib/hooks/use-pods';

export default function ImportGithubPage({
    params,
}: {
    params: Promise<{ owner: string; repo: string }>;
}) {
    const raw = use(params);
    const owner = decodeURIComponent(raw.owner);
    const repo = decodeURIComponent(raw.repo);

    return (
        <ProtectedRoute>
            <ImportGithubLanding owner={owner} repo={repo} />
        </ProtectedRoute>
    );
}

function ImportGithubLanding({ owner, repo }: { owner: string; repo: string }) {
    const { data } = useAccessiblePods();
    const organizations = data.organizations;
    const pods = data.items;
    const showOrgLabels = data.hasMultipleOrganizations;

    const [orgId, setOrgId] = useState('');
    const [podId, setPodId] = useState('');
    const [dialog, setDialog] = useState<{ createNew?: { organizationId: string }; podId?: string } | null>(
        null,
    );

    const presetGithub = useMemo(() => ({ owner, repo }), [owner, repo]);
    const effectiveOrg = orgId || organizations[0]?.id || '';
    const selectedPod = pods.find((p) => p.id === podId);

    return (
        <PlainPageShell
            title="Import from GitHub"
            backHref="/"
            backLabel="Home"
            contentWidthClassName="max-w-xl"
            centerContent
        >
            <div className="space-y-5">
                {/* Source card */}
                <div className="surface-panel flex flex-col items-center gap-3 p-8 text-center">
                    <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-[var(--surface-2)]">
                        <Github className="h-6 w-6 text-[var(--text-primary)]" />
                    </div>
                    <div>
                        <h1 className="text-lg font-medium text-[var(--text-primary)]">{repo}</h1>
                        <p className="text-sm text-[var(--text-tertiary)]">
                            github.com/{owner}/{repo}
                        </p>
                    </div>
                    <p className="max-w-sm text-sm text-[var(--text-secondary)]">
                        Import this pod into your Lemma workspace — its tables, agents, workflows, apps and
                        surfaces.
                    </p>
                </div>

                {/* Create a new pod */}
                <section className="surface-panel p-4">
                    <div className="flex items-start gap-2.5">
                        <Plus className="mt-0.5 h-4 w-4 shrink-0 text-[var(--action-primary)]" />
                        <div className="min-w-0 flex-1">
                            <div className="text-sm font-medium text-[var(--text-primary)]">Create a new pod</div>
                            <p className="text-xs text-[var(--text-tertiary)]">
                                A fresh copy you fully own — recommended.
                            </p>
                        </div>
                    </div>
                    <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                        {organizations.length > 1 ? (
                            <Select value={effectiveOrg} onValueChange={setOrgId}>
                                <SelectTrigger className="sm:flex-1">
                                    <SelectValue placeholder="Choose a workspace" />
                                </SelectTrigger>
                                <SelectContent>
                                    {organizations.map((org) => (
                                        <SelectItem key={org.id} value={org.id}>
                                            {org.name}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        ) : null}
                        <Button
                            className="sm:w-auto"
                            disabled={!effectiveOrg}
                            onClick={() => setDialog({ createNew: { organizationId: effectiveOrg } })}
                        >
                            Create &amp; import
                        </Button>
                    </div>
                </section>

                {/* Install into an existing pod */}
                <section className="surface-panel p-4">
                    <div className="flex items-start gap-2.5">
                        <Download className="mt-0.5 h-4 w-4 shrink-0 text-[var(--text-tertiary)]" />
                        <div className="min-w-0 flex-1">
                            <div className="text-sm font-medium text-[var(--text-primary)]">
                                Install into an existing pod
                            </div>
                            <p className="text-xs text-[var(--text-tertiary)]">
                                Add these resources to a pod you already have.
                            </p>
                        </div>
                    </div>
                    <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                        <Select value={podId} onValueChange={setPodId} disabled={pods.length === 0}>
                            <SelectTrigger className="sm:flex-1">
                                <SelectValue placeholder={pods.length ? 'Choose a pod' : 'No pods yet'} />
                            </SelectTrigger>
                            <SelectContent>
                                {pods.map((pod) => (
                                    <SelectItem key={pod.id} value={pod.id}>
                                        {pod.name}
                                        {showOrgLabels && pod.organization_name
                                            ? ` · ${pod.organization_name}`
                                            : ''}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <Button
                            variant="secondary"
                            className="sm:w-auto"
                            disabled={!podId}
                            onClick={() => setDialog({ podId })}
                        >
                            Install
                        </Button>
                    </div>
                </section>
            </div>

            <ImportDialog
                open={Boolean(dialog)}
                onOpenChange={(nextOpen) => {
                    if (!nextOpen) setDialog(null);
                }}
                presetGithub={presetGithub}
                createNew={dialog?.createNew}
                podId={dialog?.podId}
                podName={selectedPod?.name}
                onCompleted={() => setDialog(null)}
            />
        </PlainPageShell>
    );
}
