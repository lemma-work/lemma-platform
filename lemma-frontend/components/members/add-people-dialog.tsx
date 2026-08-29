'use client';

import { useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import { ChevronDown, Copy, Mail, X } from '@/components/ui/icons';

import { Button } from '@/components/ui/button';
import {
    Command,
    CommandEmpty,
    CommandGroup,
    CommandInput,
    CommandItem,
    CommandList,
} from '@/components/ui/command';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';
import { useApps } from '@/lib/hooks/use-app';
import { useOrganizationMembers, useInviteMember } from '@/lib/hooks/use-organizations';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { useAddPodMember, usePodMembers } from '@/lib/hooks/use-pod-members';
import { usePod } from '@/lib/hooks/use-pods';
import { useProfile } from '@/lib/hooks/use-user';
import { buildShareLink } from '@/lib/share/share-link';
import { OrganizationRole, PodJoinPolicy, PodRole } from '@/lib/types';
import {
    buildPodInviteRedirectUri,
    getPodInviteRedirectOptions,
    getSiteOrigin,
} from '@/lib/utils/invite-redirects';
import { cn } from '@/lib/utils';

/**
 * Adding someone to a pod.
 *
 * One field. You type who, not what kind of record they are.
 *
 * The surface this replaces asked for the taxonomy first — a tab bar reading
 * "Invite by email" / "Existing member" — which is the membership table leaking
 * into the one moment a person is thinking about a person. Nobody knows offhand
 * whether Priya already has a row in `organization_members`; they know they
 * want Priya in the pod. So the box takes a name or an email, matches
 * colleagues as you type, and offers to invite the address when it matches
 * nobody. Which endpoint that becomes is ours to work out, not theirs.
 *
 * Everything that is genuinely about *this* addition stays on screen: who, and
 * what they can do here. The two knobs that are about the invitation *email* —
 * the organization role it grants and where it lands afterwards — sit behind a
 * disclosure that only exists once there is an email invite to send, because
 * until then they decide nothing.
 */

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** How the pod's own policy answers someone who opens the invite link. */
const JOIN_POLICY_HINT: Record<string, string> = {
    [PodJoinPolicy.INVITE_ONLY]: 'Whoever opens it can ask to join, and you approve the request.',
    [PodJoinPolicy.ORG_MEMBERS]: 'Anyone in your organization who opens it joins straight away.',
    [PodJoinPolicy.PUBLIC]: 'Anyone with a Lemma account who opens it joins straight away.',
};

const POD_ROLE_OPTIONS: Array<{ value: PodRole; label: string; hint: string }> = [
    { value: PodRole.POD_ADMIN, label: 'Admin', hint: 'Runs the pod, including who else is in it.' },
    { value: PodRole.POD_EDITOR, label: 'Editor', hint: 'Builds and changes what the pod is made of.' },
    { value: PodRole.POD_USER, label: 'User', hint: 'Uses the pod day to day.' },
    { value: PodRole.POD_VIEWER, label: 'Viewer', hint: 'Reads, changes nothing.' },
];

/** A person the composer is holding, before anything has been committed. */
type Draft =
    | { key: string; kind: 'member'; organizationMemberId: string; name: string; email: string | null }
    | { key: string; kind: 'email'; email: string };

function isEmail(value: string): boolean {
    return EMAIL_PATTERN.test(value.trim());
}

function initialOf(label: string): string {
    const trimmed = label.trim();
    return trimmed ? trimmed[0].toUpperCase() : '?';
}

function inviteErrorMessage(error: Error): string {
    const details = error as Error & { code?: string };
    if (details.code === 'IDENTITY_ACCESS_DENIED') {
        return 'Only organization owners and editors can invite people by email.';
    }
    return error.message;
}

export function AddPeopleDialog({
    podId,
    open,
    onOpenChange,
}: {
    podId: string;
    open: boolean;
    onOpenChange: (open: boolean) => void;
}) {
    const podAccess = usePodAccess(podId);

    if (!podAccess.can('pod.member.manage')) return null;

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="gap-0 sm:max-w-[560px]">
                {/* Radix unmounts this on close, which is the whole reset: a
                    half-typed name never survives into the next time you open
                    the box, and no effect has to remember to clear it. */}
                <AddPeopleComposer podId={podId} onClose={() => onOpenChange(false)} />
            </DialogContent>
        </Dialog>
    );
}

function AddPeopleComposer({ podId, onClose }: { podId: string; onClose: () => void }) {
    const podAccess = usePodAccess(podId);
    const { data: pod } = usePod(podId);
    const { data: profile } = useProfile();
    const organizationId = pod?.organization_id || '';
    const { data: membersData } = usePodMembers(podId);
    const { data: orgMembersData } = useOrganizationMembers(organizationId);
    const { data: apps = [] } = useApps(podId);

    const { mutateAsync: addMember } = useAddPodMember(podId);
    const { mutateAsync: inviteMember } = useInviteMember(organizationId);

    const [drafts, setDrafts] = useState<Draft[]>([]);
    const [query, setQuery] = useState('');
    const [podRole, setPodRole] = useState<PodRole>(PodRole.POD_USER);
    const [orgRole, setOrgRole] = useState<OrganizationRole>(OrganizationRole.ORG_MEMBER);
    const [chosenRedirectUri, setChosenRedirectUri] = useState<string | null>(null);
    const [advancedOpen, setAdvancedOpen] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const inputRef = useRef<HTMLInputElement>(null);

    const members = useMemo(() => membersData?.items || [], [membersData?.items]);
    const orgMembers = useMemo(() => orgMembersData?.items || [], [orgMembersData?.items]);

    const canManageMembers = podAccess.can('pod.member.manage');
    const currentOrgRole = orgMembers.find((member) => member.user_id === profile?.id)?.role;
    const canInviteByEmail =
        canManageMembers &&
        (currentOrgRole === OrganizationRole.ORG_OWNER || currentOrgRole === OrganizationRole.ORG_EDITOR);

    // Everyone in the organization who is not already in the pod and not
    // already sitting in the composer.
    const candidates = useMemo(() => {
        const inPod = new Set(members.map((member) => member.user_id));
        const drafted = new Set(
            drafts.flatMap((draft) => (draft.kind === 'member' ? [draft.organizationMemberId] : [])),
        );

        return orgMembers
            .filter((member) => !inPod.has(member.user_id) && !drafted.has(member.id))
            .map((member) => ({
                id: member.id,
                name: member.user?.full_name
                    || [member.user?.first_name, member.user?.last_name].filter(Boolean).join(' ').trim()
                    || member.user?.email
                    || 'Unknown person',
                email: member.user?.email || null,
            }));
    }, [drafts, members, orgMembers]);

    const trimmedQuery = query.trim();
    const matches = useMemo(() => {
        if (!trimmedQuery) return candidates.slice(0, 50);
        const needle = trimmedQuery.toLowerCase();
        return candidates
            .filter((candidate) =>
                candidate.name.toLowerCase().includes(needle)
                || (candidate.email || '').toLowerCase().includes(needle))
            .slice(0, 50);
    }, [candidates, trimmedQuery]);

    const draftedEmails = useMemo(
        () => new Set(drafts.map((draft) => (draft.kind === 'email' ? draft.email : draft.email || '').toLowerCase())),
        [drafts],
    );
    // Only offered once the address matches nobody we could simply add: a
    // colleague already in the organization is added, never re-invited.
    const offersInvite =
        isEmail(trimmedQuery)
        && !draftedEmails.has(trimmedQuery.toLowerCase())
        && !candidates.some((candidate) => candidate.email?.toLowerCase() === trimmedQuery.toLowerCase())
        && !members.some((member) => (member.user_email || '').toLowerCase() === trimmedQuery.toLowerCase());

    const emailDrafts = drafts.filter((draft): draft is Extract<Draft, { kind: 'email' }> => draft.kind === 'email');
    const hasEmailInvites = emailDrafts.length > 0;

    const redirectOptions = useMemo(
        () => getPodInviteRedirectOptions({ podId, apps }),
        [apps, podId],
    );
    const defaultRedirectUri = useMemo(
        () => buildPodInviteRedirectUri({ podId, podRole, apps }),
        [apps, podId, podRole],
    );
    const redirectUri = redirectOptions.some((option) => option.value === chosenRedirectUri)
        ? (chosenRedirectUri as string)
        : defaultRedirectUri;

    const inviteLink = useMemo(
        () => buildShareLink({
            kind: 'pod',
            canonicalUrl: `${getSiteOrigin()}/pod/${podId}`,
            name: pod?.name,
        }),
        [pod?.name, podId],
    );
    const joinPolicyHint = JOIN_POLICY_HINT[String(pod?.config?.join_policy || PodJoinPolicy.INVITE_ONLY)]
        ?? JOIN_POLICY_HINT[PodJoinPolicy.INVITE_ONLY];

    const addDraft = (draft: Draft) => {
        setDrafts((current) => [...current, draft]);
        setQuery('');
        inputRef.current?.focus();
    };

    const addEmailDraft = (email: string) => {
        if (!canInviteByEmail) {
            toast.error('Only organization owners and editors can invite people by email.');
            return;
        }
        addDraft({ key: `email:${email.toLowerCase()}`, kind: 'email', email: email.trim() });
    };

    const removeDraft = (key: string) => {
        setDrafts((current) => current.filter((draft) => draft.key !== key));
    };

    /** A list pasted from a spreadsheet or a mail client becomes chips, not one
     *  unusable line of text. */
    const handlePaste = (event: React.ClipboardEvent<HTMLInputElement>) => {
        const text = event.clipboardData.getData('text');
        if (!/[\s,;]/.test(text)) return;

        const emails = Array.from(new Set(
            text.split(/[\s,;]+/).map((part) => part.trim()).filter(isEmail),
        ));
        if (emails.length === 0) return;

        event.preventDefault();
        if (!canInviteByEmail) {
            toast.error('Only organization owners and editors can invite people by email.');
            return;
        }

        const byEmail = new Map(candidates.map((candidate) => [candidate.email?.toLowerCase(), candidate]));
        const additions: Draft[] = [];
        for (const email of emails) {
            const key = email.toLowerCase();
            if (draftedEmails.has(key) || additions.some((draft) => draft.key.endsWith(key))) continue;
            // Someone already in the organization is added outright — pasting a
            // list should not mail an invitation to the desk next to you.
            const known = byEmail.get(key);
            if (known) {
                additions.push({
                    key: `member:${known.id}`,
                    kind: 'member',
                    organizationMemberId: known.id,
                    name: known.name,
                    email: known.email,
                });
                continue;
            }
            additions.push({ key: `email:${key}`, kind: 'email', email });
        }

        if (additions.length === 0) return;
        setDrafts((current) => [...current, ...additions]);
        setQuery('');
    };

    const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
        if (event.key !== 'Backspace' || query.length > 0 || drafts.length === 0) return;
        event.preventDefault();
        setDrafts((current) => current.slice(0, -1));
    };

    const copyInviteLink = async () => {
        if (!inviteLink) return;
        try {
            await navigator.clipboard.writeText(inviteLink);
            toast.success('Invite link copied');
        } catch {
            toast.error('Could not copy to clipboard');
        }
    };

    const submitLabel = (() => {
        const count = drafts.length || 1;
        if (drafts.length > 0 && emailDrafts.length === drafts.length) {
            return count === 1 ? 'Send invite' : `Send ${count} invites`;
        }
        return count === 1 ? 'Add person' : `Add ${count} people`;
    })();

    const handleSubmit = async () => {
        if (!canManageMembers || drafts.length === 0 || submitting) return;
        setSubmitting(true);

        const failures: string[] = [];
        let added = 0;
        let invited = 0;

        for (const draft of drafts) {
            try {
                if (draft.kind === 'member') {
                    await addMember({ organization_member_id: draft.organizationMemberId, role: podRole });
                    added += 1;
                } else {
                    await inviteMember({
                        email: draft.email,
                        role: orgRole,
                        pod_id: podId,
                        pod_role: podRole,
                        redirect_uri: redirectUri.trim() || defaultRedirectUri,
                    });
                    invited += 1;
                }
            } catch (error) {
                const label = draft.kind === 'member' ? draft.name : draft.email;
                failures.push(`${label} — ${inviteErrorMessage(error as Error)}`);
            }
        }

        setSubmitting(false);

        const done: string[] = [];
        if (added) done.push(added === 1 ? '1 person added' : `${added} people added`);
        if (invited) done.push(invited === 1 ? '1 invite sent' : `${invited} invites sent`);
        if (done.length) toast.success(done.join(', '));
        // Named individually: "2 failed" leaves you re-typing the whole list to
        // work out which two.
        for (const failure of failures) toast.error(failure);

        if (failures.length === 0) {
            onClose();
            return;
        }
        // Whoever failed stays in the box, so a retry is one click.
        setDrafts((current) => current.filter((draft) => {
            const label = draft.kind === 'member' ? draft.name : draft.email;
            return failures.some((failure) => failure.startsWith(`${label} — `));
        }));
    };

    if (!canManageMembers) return null;

    return (
        <>
            <DialogHeader className="pr-8">
                <DialogTitle>Add people to {pod?.name || 'this pod'}</DialogTitle>
                <DialogDescription>
                    {canInviteByEmail
                        ? 'Type a name to add a colleague, or an email to invite someone new.'
                        : 'Type a name to add a colleague from your organization.'}
                </DialogDescription>
            </DialogHeader>

            <Command shouldFilter={false} className="mt-4 overflow-visible border-0 bg-transparent shadow-none">
                {drafts.length > 0 ? (
                    <div className="mb-2 flex flex-wrap gap-1.5">
                        {drafts.map((draft) => {
                            const label = draft.kind === 'member' ? draft.name : draft.email;
                            return (
                                <span key={draft.key} className="chip chip-sm chip-muted gap-1.5 pr-1">
                                    {draft.kind === 'email' ? <Mail className="h-3 w-3 shrink-0" /> : null}
                                    <span className="truncate">{label}</span>
                                    <button
                                        type="button"
                                        aria-label={`Remove ${label}`}
                                        onClick={() => removeDraft(draft.key)}
                                        className="resource-remove-button custom-focus-ring h-4 w-4"
                                    >
                                        <X className="h-3 w-3" />
                                    </button>
                                </span>
                            );
                        })}
                    </div>
                ) : null}

                <div className="overflow-hidden rounded-lg border border-[color:var(--border-subtle)]">
                    <CommandInput
                        ref={inputRef}
                        value={query}
                        onValueChange={setQuery}
                        onPaste={handlePaste}
                        onKeyDown={handleKeyDown}
                        placeholder={canInviteByEmail ? 'Name or email' : 'Name'}
                    />
                    <CommandList className="max-h-56">
                        {matches.length > 0 ? (
                            <CommandGroup heading="In your organization">
                                {matches.map((candidate) => (
                                    <CommandItem
                                        key={candidate.id}
                                        value={`member:${candidate.id}`}
                                        onSelect={() => addDraft({
                                            key: `member:${candidate.id}`,
                                            kind: 'member',
                                            organizationMemberId: candidate.id,
                                            name: candidate.name,
                                            email: candidate.email,
                                        })}
                                        className="gap-2"
                                    >
                                        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[var(--surface-3)] text-xs text-[var(--text-secondary)]">
                                            {initialOf(candidate.name)}
                                        </span>
                                        <span className="truncate">{candidate.name}</span>
                                        {candidate.email && candidate.email !== candidate.name ? (
                                            <span className="ml-auto truncate text-xs text-[var(--text-tertiary)]">
                                                {candidate.email}
                                            </span>
                                        ) : null}
                                    </CommandItem>
                                ))}
                            </CommandGroup>
                        ) : null}

                        {offersInvite && canInviteByEmail ? (
                            <CommandGroup heading={matches.length > 0 ? 'Not here yet' : undefined}>
                                <CommandItem
                                    value={`invite:${trimmedQuery}`}
                                    onSelect={() => addEmailDraft(trimmedQuery)}
                                    className="gap-2"
                                >
                                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[var(--surface-3)]">
                                        <Mail className="h-3 w-3 text-[var(--text-secondary)]" />
                                    </span>
                                    <span className="truncate">
                                        Invite <span className="text-[var(--text-primary)]">{trimmedQuery}</span>
                                    </span>
                                </CommandItem>
                            </CommandGroup>
                        ) : null}

                        <CommandEmpty>
                            {trimmedQuery && !canInviteByEmail
                                ? 'Nobody by that name. Only organization owners and editors can invite people by email.'
                                : trimmedQuery
                                    ? 'Nobody by that name. Type a full email address to invite them.'
                                    : 'Everyone in your organization is already in this pod.'}
                        </CommandEmpty>
                    </CommandList>
                </div>
            </Command>

            <div className="mt-4 flex items-center gap-3">
                <label htmlFor="add-people-role" className="shrink-0 text-sm font-medium">
                    Role in this pod
                </label>
                <Select value={podRole} onValueChange={(value) => setPodRole(value as PodRole)}>
                    <SelectTrigger id="add-people-role" className="flex-1">
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        {POD_ROLE_OPTIONS.map((option) => (
                            <SelectItem key={option.value} value={option.value}>
                                {option.label}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            </div>
            <p className="mt-1.5 text-xs text-[var(--text-tertiary)]">
                {POD_ROLE_OPTIONS.find((option) => option.value === podRole)?.hint}
            </p>

            {/* Only once there is an invitation to send: until then neither
                of these decides anything, and a disclosure promising
                settings that do nothing is worse than no disclosure. */}
            {hasEmailInvites ? (
                <div className="mt-3">
                    <Button
                        variant="quiet"
                        size="xs"
                        onClick={() => setAdvancedOpen((current) => !current)}
                        className="-ml-2.5 gap-1 text-[var(--text-tertiary)]"
                        aria-expanded={advancedOpen}
                    >
                        <ChevronDown className={cn('h-3.5 w-3.5 transition-transform', advancedOpen && 'rotate-180')} />
                        Invitation settings
                    </Button>
                    {advancedOpen ? (
                        <div className="mt-2 grid gap-3 rounded-lg border border-[color:var(--border-subtle)] p-3 sm:grid-cols-2">
                            <div className="space-y-1.5">
                                <label className="text-sm font-medium">Organization role</label>
                                <Select value={orgRole} onValueChange={(value) => setOrgRole(value as OrganizationRole)}>
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value={OrganizationRole.ORG_OWNER}>Owner</SelectItem>
                                        <SelectItem value={OrganizationRole.ORG_EDITOR}>Editor</SelectItem>
                                        <SelectItem value={OrganizationRole.ORG_MEMBER}>Member</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-1.5">
                                <label className="text-sm font-medium">Land them on</label>
                                <Select value={redirectUri} onValueChange={setChosenRedirectUri}>
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {redirectOptions.map((option) => (
                                            <SelectItem key={option.value} value={option.value}>
                                                {option.label}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                        </div>
                    ) : null}
                </div>
            ) : null}

            {inviteLink ? (
                <div className="mt-4 flex items-center justify-between gap-3 rounded-lg border border-dashed border-[color:var(--border-subtle)] px-3 py-2">
                    <div className="min-w-0">
                        <p className="text-sm text-[var(--text-primary)]">Invite link</p>
                        <p className="text-xs text-[var(--text-tertiary)]">{joinPolicyHint}</p>
                    </div>
                    <Button variant="quiet" size="sm" className="shrink-0 gap-1.5" onClick={copyInviteLink}>
                        <Copy className="h-3.5 w-3.5" />
                        Copy
                    </Button>
                </div>
            ) : null}

            <DialogFooter className="pt-4">
                <Button variant="quiet" onClick={onClose}>Cancel</Button>
                <Button
                    variant="primary"
                    onClick={handleSubmit}
                    disabled={drafts.length === 0 || submitting}
                    loading={submitting}
                    loadingLabel="Adding..."
                >
                    {submitLabel}
                </Button>
            </DialogFooter>
        </>
    );
}
