'use client';

import { Globe2 } from 'lucide-react';

import { OrganizationJoinPolicy } from '@/lib/types';
import { Label } from '@/components/ui/label';
import { SettingsChoiceList } from '@/components/settings/settings-kit';

export const ORG_JOIN_POLICY_OPTIONS: {
    value: OrganizationJoinPolicy;
    label: string;
    description: string;
}[] = [
    {
        value: OrganizationJoinPolicy.INVITE_ONLY,
        label: 'Invite only',
        description: 'People can join only when you invite them.',
    },
    {
        value: OrganizationJoinPolicy.EMAIL_DOMAIN,
        label: 'Same email domain',
        description: 'Anyone signing in with a matching company email can join.',
    },
    {
        value: OrganizationJoinPolicy.PUBLIC,
        label: 'Anyone with the link',
        description: 'Any Lemma user can join this workspace.',
    },
];

export function orgJoinPolicyLabel(policy: OrganizationJoinPolicy | undefined | null): string {
    return ORG_JOIN_POLICY_OPTIONS.find((option) => option.value === policy)?.label ?? 'Invite only';
}

export function OrgJoinPolicyField({
    value,
    onChange,
    emailDomain,
    onEmailDomainChange,
    suggestedWorkDomain,
    disabled = false,
    label = 'Who can join',
}: {
    value: OrganizationJoinPolicy;
    onChange: (value: OrganizationJoinPolicy) => void;
    emailDomain: string;
    onEmailDomainChange: (value: string) => void;
    suggestedWorkDomain?: string | null;
    disabled?: boolean;
    label?: string;
}) {
    const emailDomainField = (
        <div className="space-y-2">
            <Label htmlFor="email-domain">Company email domain</Label>
            <div className="form-field-control flex h-12 items-center gap-3 px-4">
                <Globe2 className="h-5 w-5 shrink-0 text-[var(--text-tertiary)]" />
                <input
                    id="email-domain"
                    placeholder="company.com"
                    value={emailDomain}
                    onChange={(event) => onEmailDomainChange(event.target.value)}
                    disabled={disabled}
                    className="inline-edit-field min-w-0 flex-1 border-0 bg-transparent p-0 text-base text-[var(--text-primary)] outline-none placeholder:text-[var(--text-soft)]"
                />
            </div>
            {!suggestedWorkDomain ? (
                <p className="text-xs leading-5 text-[var(--text-tertiary)]">
                    This must match your own company email domain. Personal providers like Gmail can&apos;t use domain-based joining.
                </p>
            ) : null}
        </div>
    );

    const options = ORG_JOIN_POLICY_OPTIONS.map((option) => ({
        value: option.value,
        label: option.label,
        description: option.description,
        expanded: option.value === OrganizationJoinPolicy.EMAIL_DOMAIN ? emailDomainField : undefined,
    }));

    return (
        <div className="space-y-2">
            <Label>{label}</Label>
            <SettingsChoiceList
                ariaLabel={typeof label === 'string' ? label : 'Who can join'}
                options={options}
                value={value}
                onChange={onChange}
                disabled={disabled}
            />
        </div>
    );
}
