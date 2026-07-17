'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ArrowRight, Building2, Sparkles } from '@/components/ui/icons';
import { useCreateOrganization } from '@/lib/hooks/use-organizations';
import { PlainPageShell } from '@/components/dashboard/plain-page-shell';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { ProtectedRoute } from '@/components/auth/protected-route';
import { useOrganization } from '@/components/dashboard/org-context';
import { ProductIcon } from '@/components/pod/product-icon';
import { OrgJoinPolicyField, orgJoinPolicyLabel } from '@/components/organizations/org-join-policy-field';
import { OrganizationJoinPolicy } from '@/lib/types';
import { useProfile } from '@/lib/hooks/use-user';
import { normalizeEmailDomain, slugifyOrganizationName, workDomainFromEmail } from '@/lib/utils/organization-slugs';
import { toast } from 'sonner';

function OrgCreatePageContent() {
    const router = useRouter();
    const { mutate: createOrg, isPending } = useCreateOrganization();
    const { setCurrentOrg } = useOrganization();
    const { data: profile } = useProfile();
    const [name, setName] = useState('');
    const [joinPolicy, setJoinPolicy] = useState<OrganizationJoinPolicy>(OrganizationJoinPolicy.INVITE_ONLY);
    const [websiteDomain, setWebsiteDomain] = useState('');
    const suggestedWorkDomain = workDomainFromEmail(profile?.email);
    const normalizedDomain = normalizeEmailDomain(websiteDomain);
    const slugPreview = useMemo(() => slugifyOrganizationName(name), [name]);
    const isEmailDomain = joinPolicy === OrganizationJoinPolicy.EMAIL_DOMAIN;
    const missingDomain = isEmailDomain && !normalizedDomain;

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();

        if (!name.trim()) return;

        createOrg(
            {
                name,
                join_policy: joinPolicy,
                email_domain: isEmailDomain ? normalizedDomain : null,
            },
            {
                onSuccess: (org) => {
                    toast.success('Organization created successfully');
                    setCurrentOrg(org);
                    router.push('/');
                },
                onError: (error) => {
                    toast.error(`Failed to create organization: ${error.message}`);
                }
            }
        );
    };

    return (
        <PlainPageShell
            title="Create organization"
            icon={<ProductIcon kind="settings" size="sm" />}
            backHref="/home"
            backLabel="Home"
            meta="Organization"
            contentWidthClassName="max-w-5xl"
            contentClassName="pb-16 sm:pb-20"
        >
            <section className="office-room-chrome office-arrive p-6 sm:p-8 lg:p-10">
                <form onSubmit={handleSubmit} className="grid gap-10 lg:grid-cols-[minmax(220px,0.72fr)_minmax(360px,1fr)] lg:items-start">
                    <div className="pt-1">
                        <p className="type-eyebrow-mono text-[var(--text-tertiary)]">New organization</p>
                        <h1 className="mt-3 max-w-sm text-2xl font-semibold leading-tight tracking-normal text-[var(--text-primary)] sm:text-3xl">
                            Who should this workspace belong to?
                        </h1>
                        <p className="mt-3 max-w-sm text-sm leading-6 text-[var(--text-secondary)]">
                            Create a workspace for a team or company. Pods created inside it can share members, settings, and access later.
                        </p>
                    </div>

                    <div className="space-y-5 lg:pt-3">
                        <div className="mb-5 flex items-center gap-3">
                            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[color-mix(in_srgb,var(--delight-soft)_72%,var(--surface-1))] text-[var(--delight)]">
                                <Sparkles className="h-5 w-5" />
                            </span>
                            <div>
                                <p className="text-sm font-medium text-[var(--text-primary)]">Workspace setup</p>
                                <p className="mt-0.5 text-xs text-[var(--text-tertiary)]">Start simple. Members and billing can be managed after creation.</p>
                            </div>
                        </div>

                        <div className="space-y-2">
                            <Label htmlFor="name">Organization name</Label>
                            <div className="form-field-control flex h-14 items-center gap-3 px-4">
                                <Building2 className="h-5 w-5 shrink-0 text-[var(--text-tertiary)]" />
                                <input
                                    id="name"
                                    placeholder="Acme Corp"
                                    value={name}
                                    onChange={(e) => setName(e.target.value)}
                                    required
                                    className="inline-edit-field min-w-0 flex-1 border-0 bg-transparent p-0 text-base text-[var(--text-primary)] outline-none placeholder:text-[var(--text-soft)]"
                                />
                            </div>
                            {slugPreview ? (
                                <p className="text-xs text-[var(--text-tertiary)]">
                                    URL slug: <span className="font-medium text-[var(--text-secondary)]">{slugPreview}</span>
                                </p>
                            ) : null}
                        </div>

                        <OrgJoinPolicyField
                            value={joinPolicy}
                            onChange={(next) => {
                                setJoinPolicy(next);
                                if (
                                    next === OrganizationJoinPolicy.EMAIL_DOMAIN &&
                                    !websiteDomain &&
                                    suggestedWorkDomain
                                ) {
                                    setWebsiteDomain(suggestedWorkDomain);
                                }
                            }}
                            emailDomain={websiteDomain}
                            onEmailDomainChange={setWebsiteDomain}
                            suggestedWorkDomain={suggestedWorkDomain}
                        />

                        <div className="px-1">
                            <p className="text-sm font-medium text-[var(--text-primary)]">Organization access</p>
                            <p className="mt-1 text-xs leading-5 text-[var(--text-tertiary)]">
                                {isEmailDomain && normalizedDomain
                                    ? `Anyone with an @${normalizedDomain} email can join.`
                                    : joinPolicy === OrganizationJoinPolicy.PUBLIC
                                        ? 'Any Lemma user can join this workspace.'
                                        : `${orgJoinPolicyLabel(joinPolicy)} — members will need to be invited manually.`}
                            </p>
                        </div>

                        <div className="flex flex-col gap-3 pt-2 sm:flex-row sm:items-center">
                            <Button
                                type="submit"
                                disabled={!name.trim() || missingDomain}
                                loading={isPending}
                                loadingLabel="Creating organization"
                                className="office-primary-action h-12 gap-2 px-5 text-sm font-medium"
                            >
                                Create organization
                                <ArrowRight className="h-4 w-4" />
                            </Button>
                            <Button variant="ghost" type="button" asChild>
                                <Link href="/home">Cancel</Link>
                            </Button>
                        </div>
                    </div>
                </form>
            </section>
        </PlainPageShell>
    );
}

export default function OrgCreatePage() {
    return (
        <ProtectedRoute>
            <OrgCreatePageContent />
        </ProtectedRoute>
    );
}
