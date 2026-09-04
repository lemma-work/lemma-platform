'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { GitHubReadmePage } from '@/components/bundle/github-readme-page';
import { ImportDialog } from '@/components/bundle/import-dialog';
import { Logo } from '@/components/brand/logo';
import { Button } from '@/components/ui/button';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';
import {
    ArrowRight,
    Check,
    Download,
    ExternalLink,
    Github,
    Plus,
    ShieldCheck,
} from '@/components/ui/icons';
import { useLemmaAuth } from '@/lib/hooks/use-lemma-auth';
import { useAccessiblePods } from '@/lib/hooks/use-pods';
import { useSuggestedOrganizations } from '@/lib/hooks/use-organizations';
import { useProfile } from '@/lib/hooks/use-user';
import { useEnsureOrganization } from '@/components/onboarding/use-ensure-organization';
import type { PublicGitHubReadme } from '@/lib/github/public-repository';
import {
    buildImportReturnUrl,
    hasResumeMarker,
    withoutResumeMarker,
    type ImportDestination,
} from '@/lib/github/import-resume';
import { cn } from '@/lib/utils';

type Destination = ImportDestination;

export function ImportGithubClient({
    owner,
    repo,
    initialDestination = 'new',
    initialReadme,
}: {
    owner: string;
    repo: string;
    initialDestination?: Destination;
    initialReadme?: PublicGitHubReadme | null;
}) {
    const { isAuthenticated, isLoading: isAuthLoading, redirectToAuth } = useLemmaAuth();
    const { data, isLoading: isLoadingPods } = useAccessiblePods({ enabled: isAuthenticated });
    const profileQuery = useProfile();
    const profile = profileQuery.data;
    const suggestedOrganizations = useSuggestedOrganizations({ enabled: isAuthenticated });
    const ensureOrganization = useEnsureOrganization();
    const organizations = data.organizations;
    const pods = data.items;
    const showOrgLabels = data.hasMultipleOrganizations;

    const [destination, setDestination] = useState<Destination>(initialDestination);
    const [orgId, setOrgId] = useState('');
    const [podId, setPodId] = useState('');
    const [isPreparingWorkspace, setIsPreparingWorkspace] = useState(false);
    const [workspaceError, setWorkspaceError] = useState('');
    const [dialog, setDialog] = useState<{ createNew?: { organizationId: string }; podId?: string } | null>(
        null,
    );
    const resumedRef = useRef(false);

    const presetGithub = useMemo(() => ({ owner, repo }), [owner, repo]);
    const effectiveOrg = orgId || organizations[0]?.id || '';
    const effectivePod = podId || pods[0]?.id || '';
    const selectedPod = pods.find((pod) => pod.id === effectivePod);
    const repoUrl = `https://github.com/${owner}/${repo}`;

    const continueToInstall = useCallback(async () => {
        if (!isAuthenticated) {
            // Signing up is a detour, not a change of mind: the return URL
            // carries both the destination and the fact that they asked.
            redirectToAuth({
                redirectUri: buildImportReturnUrl(window.location.href, destination),
            });
            return;
        }

        if (destination === 'existing') {
            if (effectivePod) setDialog({ podId: effectivePod });
            return;
        }

        if (effectiveOrg) {
            setDialog({ createNew: { organizationId: effectiveOrg } });
            return;
        }

        // No workspace at all. An account that signed up from this page never
        // passed through the root route, so nothing has made it one — and the
        // installer is the wrong place to teach someone what a workspace is.
        // Make it the way onboarding would have, and carry on to the plan.
        setIsPreparingWorkspace(true);
        setWorkspaceError('');
        try {
            const ensured = await ensureOrganization({
                email: profile?.email,
                organizationIds: organizations.map((organization) => organization.id),
                suggestedOrganizationId: suggestedOrganizations.data?.items?.[0]?.id ?? null,
            });
            if (ensured) {
                setOrgId(ensured.organizationId);
                setDialog({ createNew: { organizationId: ensured.organizationId } });
                return;
            }
            setWorkspaceError('We could not set up a workspace. Try again in a moment.');
        } catch (error) {
            setWorkspaceError(
                error instanceof Error && error.message
                    ? `We could not set up a workspace: ${error.message}`
                    : 'We could not set up a workspace. Try again in a moment.',
            );
        } finally {
            setIsPreparingWorkspace(false);
        }
    }, [
        destination,
        effectiveOrg,
        effectivePod,
        ensureOrganization,
        isAuthenticated,
        organizations,
        profile?.email,
        redirectToAuth,
        suggestedOrganizations.data,
    ]);

    // The return leg of that detour. Wait for auth and the pod list to settle —
    // resuming against a half-loaded account would create a second workspace for
    // someone who already has one.
    useEffect(() => {
        if (resumedRef.current) return;
        if (typeof window === 'undefined') return;
        if (!hasResumeMarker(window.location.href)) return;
        if (!isAuthenticated || isAuthLoading || isLoadingPods) return;
        if (suggestedOrganizations.isLoading || profileQuery.isLoading) return;

        resumedRef.current = true;
        window.history.replaceState(null, '', withoutResumeMarker(window.location.href));
        void continueToInstall();
    }, [
        continueToInstall,
        isAuthLoading,
        isAuthenticated,
        isLoadingPods,
        profileQuery.isLoading,
        suggestedOrganizations.isLoading,
    ]);

    const canContinue =
        !isAuthenticated ||
        (!isAuthLoading &&
            !isPreparingWorkspace &&
            // "New pod" no longer waits on a workspace existing: when there is
            // none, making one is part of what the button does.
            (destination === 'new' ? true : Boolean(effectivePod)));

    return (
        <main className="github-import-page">
            <div className="github-import-stationery" aria-hidden="true">
                <span className="github-import-stationery-botanical" />
                <span className="github-import-stationery-upgrade" />
                <span className="github-import-stationery-riso" />
            </div>

            <header className="github-import-header">
                <div className="github-import-header-inner">
                    <Link href="/" aria-label="Lemma home">
                        <Logo size="sm" variant="mark-wordmark" />
                    </Link>
                    <a href={repoUrl} target="_blank" rel="noreferrer" className="github-import-header-source">
                        <Github className="h-4 w-4" />
                        <span className="hidden sm:inline">{owner}/{repo}</span>
                        <span className="sm:hidden">Source</span>
                        <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                </div>
            </header>

            <div className="github-import-layout">
                <section className="github-import-content">
                    <GitHubReadmePage owner={owner} repo={repo} initialReadme={initialReadme} />
                </section>

                <aside className="github-import-aside">
                    <div className="github-import-installer">
                        <div className="github-import-installer-copy">
                            <p className="github-import-installer-eyebrow">Install pod</p>
                            <h2>Choose a destination</h2>
                            <p>
                                You’ll review the installation plan before anything changes.
                            </p>
                        </div>

                        <div className="github-import-destination" role="radiogroup" aria-label="Install destination">
                            <button
                                type="button"
                                role="radio"
                                aria-checked={destination === 'new'}
                                onClick={() => setDestination('new')}
                                className={cn('resource-option-button', destination === 'new' && 'is-selected')}
                            >
                                <span className="github-import-destination-icon">
                                    <Plus className="h-4 w-4" />
                                </span>
                                <span>
                                    <strong>New pod</strong>
                                    <small>Start with a fresh copy</small>
                                </span>
                                <span className="github-import-destination-check">
                                    {destination === 'new' ? <Check className="h-3.5 w-3.5" /> : null}
                                </span>
                            </button>
                            <button
                                type="button"
                                role="radio"
                                aria-checked={destination === 'existing'}
                                onClick={() => setDestination('existing')}
                                className={cn('resource-option-button', destination === 'existing' && 'is-selected')}
                            >
                                <span className="github-import-destination-icon">
                                    <Download className="h-4 w-4" />
                                </span>
                                <span>
                                    <strong>Existing pod</strong>
                                    <small>Add it to work already running</small>
                                </span>
                                <span className="github-import-destination-check">
                                    {destination === 'existing' ? <Check className="h-3.5 w-3.5" /> : null}
                                </span>
                            </button>
                        </div>

                        {isAuthenticated ? (
                            <div className="github-import-target">
                                {isLoadingPods ? (
                                    <div className="github-import-target-loading">Loading your pods…</div>
                                ) : destination === 'new' ? (
                                    organizations.length > 1 ? (
                                        <>
                                            <label htmlFor="github-import-workspace">Workspace</label>
                                            <Select value={effectiveOrg} onValueChange={setOrgId}>
                                                <SelectTrigger id="github-import-workspace">
                                                    <SelectValue placeholder="Choose a workspace" />
                                                </SelectTrigger>
                                                <SelectContent className="github-import-select-content">
                                                    {organizations.map((organization) => (
                                                        <SelectItem key={organization.id} value={organization.id}>
                                                            {organization.name}
                                                        </SelectItem>
                                                    ))}
                                                </SelectContent>
                                            </Select>
                                        </>
                                    ) : (
                                        <div className="github-import-target-summary">
                                            <span>Workspace</span>
                                            {/* An account with none is not blocked any more — it
                                                gets one on the way to the plan. Say that, rather
                                                than reporting an absence as a dead end. */}
                                            <strong>
                                                {organizations[0]?.name ?? 'Created for you'}
                                            </strong>
                                        </div>
                                    )
                                ) : pods.length ? (
                                    <>
                                        <label htmlFor="github-import-pod">Install into</label>
                                        <Select value={effectivePod} onValueChange={setPodId}>
                                            <SelectTrigger id="github-import-pod">
                                                <SelectValue placeholder="Choose a pod" />
                                            </SelectTrigger>
                                            <SelectContent className="github-import-select-content">
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
                                    </>
                                ) : (
                                    <div className="github-import-empty-target">
                                        You don’t have a pod yet. Choose <strong>New pod</strong> to continue.
                                    </div>
                                )}
                            </div>
                        ) : null}

                        <Button variant="primary"
                            className="github-import-continue"
                            disabled={!canContinue}
                            onClick={() => { void continueToInstall(); }}
                        >
                            {isPreparingWorkspace
                                ? 'Setting up your workspace…'
                                : isAuthenticated
                                  ? destination === 'new'
                                      ? 'Create pod & review'
                                      : 'Review installation'
                                  : 'Continue to Lemma'}
                            <ArrowRight className="h-4 w-4" />
                        </Button>

                        {workspaceError ? (
                            <p className="github-import-target-error" role="alert">
                                {workspaceError}
                            </p>
                        ) : null}

                        <div className="github-import-assurance">
                            <ShieldCheck className="h-4 w-4" />
                            <span>Nothing changes until you approve the installation plan.</span>
                        </div>
                    </div>
                </aside>
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
                openPodOnComplete
                onCompleted={() => setDialog(null)}
            />
        </main>
    );
}
