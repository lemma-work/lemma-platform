"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { CheckCircle2, MessageCircle, Plus, Settings, Smartphone, User, Volume2 } from "@/components/ui/icons";
import { useOrganization } from "@/components/dashboard/org-context";
import { useProfile, useUpdateProfile } from "@/lib/hooks/use-user";
import { StepLoader } from "@/components/brand/loader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PlainPageShell } from "@/components/dashboard/plain-page-shell";
import { ProductIcon } from "@/components/pod/product-icon";
import { UserSurfacesPanel } from "@/components/settings/user-surfaces-panel";
import { SettingsChoiceList } from "@/components/settings/settings-kit";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useSoundFeedbackPreference } from "@/lib/feedback/use-sound-feedback";
import { buildApiUrl } from "@/components/auth/portal/auth/config";
import {
    playSoundFeedback,
    setSoundFeedbackPreference,
    type SoundFeedbackPreference,
} from "@/lib/feedback/sound-feedback";

export default function ProfilePage() {
    const { data: profile, isLoading } = useProfile();
    const {
        currentOrg,
        setCurrentOrg,
        organizations,
        isLoading: isLoadingOrganizations,
    } = useOrganization();
    const updateProfile = useUpdateProfile();
    const soundFeedbackPreference = useSoundFeedbackPreference();
    const [telegramLoginEnabled, setTelegramLoginEnabled] = useState(false);

    useEffect(() => {
        let active = true;
        void fetch(buildApiUrl("/auth/telegram/config"))
            .then((response) => (response.ok ? response.json() : { enabled: false }))
            .then((payload: { enabled?: boolean }) => {
                if (active) setTelegramLoginEnabled(payload.enabled === true);
            })
            .catch(() => undefined);
        return () => {
            active = false;
        };
    }, []);

    const [draft, setDraft] = useState<{ firstName: string; lastName: string; mobileNumber: string } | null>(null);
    const firstName = draft?.firstName ?? profile?.first_name ?? "";
    const lastName = draft?.lastName ?? profile?.last_name ?? "";
    const storedMobileNumber = normalizeMobileNumber(profile?.mobile_number ?? "");
    const mobileNumber = draft?.mobileNumber ?? storedMobileNumber;
    const normalizedMobileNumber = normalizeMobileNumber(mobileNumber);
    const isMobileNumberValid = !normalizedMobileNumber || /^[1-9]\d{7,14}$/.test(normalizedMobileNumber);
    const hasChanges =
        firstName !== (profile?.first_name ?? "") ||
        lastName !== (profile?.last_name ?? "") ||
        normalizedMobileNumber !== storedMobileNumber;

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!isMobileNumberValid) return;

        updateProfile.mutate(
            {
                first_name: firstName,
                last_name: lastName,
                mobile_number: normalizedMobileNumber || null,
            },
            { onSuccess: () => playSoundFeedback('action-success') },
        );
    };

    if (isLoading) {
        return (
            <PlainPageShell
                title="Profile"
                icon={<ProductIcon kind="settings" size="sm" />}
                backHref="/home"
                backLabel="Home"
                meta="Account"
                centerContent
            >
                <div className="flex min-h-[40vh] items-center justify-center">
                    <StepLoader size="md" />
                </div>
            </PlainPageShell>
        );
    }

    return (
        <PlainPageShell
            title="Profile"
            icon={<ProductIcon kind="settings" size="sm" />}
            backHref="/home"
            backLabel="Home"
            meta="Account"
            contentWidthClassName="max-w-5xl"
            contentClassName="pb-16 sm:pb-20"
        >
            <section className="office-arrive flex flex-col gap-5 border-b border-[var(--border-subtle)] pb-7 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex min-w-0 items-center gap-4">
                    <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-xl bg-[color-mix(in_srgb,var(--delight-soft)_76%,var(--surface-1))] text-lg font-semibold text-[var(--delight)]">
                        {(firstName || profile?.email || "U").slice(0, 1).toUpperCase()}
                    </div>
                    <div className="min-w-0">
                        <h2 className="truncate text-xl font-semibold text-[var(--text-primary)]">
                            {firstName || lastName ? `${firstName} ${lastName}`.trim() : "Your account"}
                        </h2>
                        <p className="mt-1 truncate text-sm text-[var(--text-secondary)]">{profile?.email}</p>
                    </div>
                </div>

                <div className="flex w-full flex-col gap-2 sm:w-auto sm:min-w-80">
                    <Label className="type-eyebrow text-[var(--text-tertiary)]">Active organization</Label>
                    <div className="flex items-center gap-2">
                        <Select
                            value={currentOrg?.id}
                            disabled={isLoadingOrganizations || organizations.length === 0}
                            onValueChange={(organizationId) => {
                                const organization = organizations.find((candidate) => candidate.id === organizationId);
                                if (organization) setCurrentOrg(organization);
                            }}
                        >
                            <SelectTrigger className="h-9 min-w-0 flex-1 sm:w-56">
                                <SelectValue placeholder={isLoadingOrganizations ? "Loading…" : "Choose organization"} />
                            </SelectTrigger>
                            <SelectContent>
                                {organizations.map((organization) => (
                                    <SelectItem key={organization.id} value={organization.id}>
                                        {organization.name}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        {currentOrg ? (
                            <Button asChild variant="outline" size="sm" className="h-9 shrink-0 px-3">
                                <Link href={`/organizations/${currentOrg.id}/settings/members`}>
                                    <Settings className="mr-1.5 h-3.5 w-3.5" />
                                    Manage
                                </Link>
                            </Button>
                        ) : null}
                        <Button asChild variant="ghost" size="icon" className="h-9 w-9 shrink-0" title="New organization">
                            <Link href="/organizations/new" aria-label="Create organization">
                                <Plus className="h-4 w-4" />
                            </Link>
                        </Button>
                    </div>
                </div>
            </section>

            <form onSubmit={handleSubmit} className="office-arrive grid gap-6 border-b border-[var(--border-subtle)] py-8 md:grid-cols-[13rem_minmax(0,1fr)] md:gap-10">
                <div>
                    <div className="settings-title-row">
                        <User className="h-4 w-4 text-[var(--text-tertiary)]" />
                        <h2 className="settings-title">Personal information</h2>
                    </div>
                    <p className="settings-description">Your identity and messaging number.</p>
                </div>

                <div className="max-w-2xl space-y-6">
                    <div className="settings-field">
                        <Label htmlFor="email" className="text-[var(--text-secondary)]">Email address</Label>
                        <Input id="email" value={profile?.email || ""} disabled className="text-[var(--text-tertiary)]" />
                    </div>

                    <div className="settings-field-grid">
                        <div className="settings-field">
                            <Label htmlFor="firstName" className="text-[var(--text-secondary)]">First name</Label>
                            <Input
                                id="firstName"
                                value={firstName}
                                onChange={(e) => setDraft({ firstName: e.target.value, lastName, mobileNumber })}
                                placeholder="Jane"
                            />
                        </div>
                        <div className="settings-field">
                            <Label htmlFor="lastName" className="text-[var(--text-secondary)]">Last name</Label>
                            <Input
                                id="lastName"
                                value={lastName}
                                onChange={(e) => setDraft({ firstName, lastName: e.target.value, mobileNumber })}
                                placeholder="Doe"
                            />
                        </div>
                    </div>

                    <div className="settings-field">
                        <div className="flex flex-wrap items-center gap-2">
                            <Label htmlFor="mobileNumber" className="flex items-center gap-2 text-[var(--text-secondary)]">
                                <Smartphone className="h-4 w-4 text-[var(--text-tertiary)]" />
                                Mobile number
                            </Label>
                            <span className="chip chip-sm chip-pill chip-muted">
                                <MessageCircle className="h-3 w-3" />
                                WhatsApp + Telegram
                            </span>
                        </div>
                        <div className="form-field-control flex h-10 overflow-hidden p-0 focus-within:border-[color:var(--field-border-focus)]">
                            <span className="settings-input-prefix type-eyebrow">digits</span>
                            <input
                                id="mobileNumber"
                                inputMode="numeric"
                                value={mobileNumber}
                                onChange={(e) => setDraft({ firstName, lastName, mobileNumber: e.target.value })}
                                placeholder="14155552671"
                                className="min-w-0 flex-1 border-0 bg-transparent px-3 text-sm text-[var(--text-primary)] outline-none placeholder:text-[var(--text-soft)]"
                                aria-invalid={!isMobileNumberValid}
                            />
                        </div>
                        <p className={isMobileNumberValid ? "settings-help-text" : "text-xs text-[var(--state-error)]"}>
                            Include country code, digits only, without +.
                        </p>
                        <div className="flex flex-wrap items-center gap-2">
                            {profile?.mobile_verified_at ? (
                                <span className="chip chip-sm chip-pill state-badge-success">
                                    <CheckCircle2 className="h-3.5 w-3.5" />
                                    Verified by Telegram
                                </span>
                            ) : telegramLoginEnabled ? (
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="sm"
                                    onClick={() => {
                                        const start = new URL(buildApiUrl("/auth/telegram/start"));
                                        start.searchParams.set("purpose", "verify_mobile");
                                        start.searchParams.set("return_to", window.location.href);
                                        window.location.assign(start.toString());
                                    }}
                                >
                                    Verify mobile with Telegram
                                </Button>
                            ) : null}
                        </div>
                    </div>

                    <div className="flex min-h-10 items-center justify-between gap-3 pt-1">
                        <div className="text-sm text-[var(--text-secondary)]">
                            {updateProfile.isSuccess ? (
                                <span className="chip chip-sm chip-pill state-badge-success">
                                    <CheckCircle2 className="h-4 w-4" />
                                    Saved.
                                </span>
                            ) : null}
                        </div>
                        <Button
                            type="submit"
                            disabled={updateProfile.isPending || !hasChanges || !isMobileNumberValid}
                            loading={updateProfile.isPending}
                            loadingLabel="Saving changes"
                            className="h-9 px-4 transition-transform active:scale-95"
                        >
                            Save changes
                        </Button>
                    </div>
                </div>
            </form>

            <section className="office-arrive grid gap-6 border-b border-[var(--border-subtle)] py-8 md:grid-cols-[13rem_minmax(0,1fr)] md:gap-10">
                <div>
                    <div className="settings-title-row">
                        <Volume2 className="h-4 w-4 text-[var(--text-tertiary)]" />
                        <h2 className="settings-title">Feedback sounds</h2>
                    </div>
                    <p className="settings-description">Choose how much audio feedback Lemma gives you.</p>
                </div>
                <SettingsChoiceList<SoundFeedbackPreference>
                    className="max-w-2xl"
                    ariaLabel="Feedback sound level"
                    value={soundFeedbackPreference}
                    onChange={(preference) => {
                        setSoundFeedbackPreference(preference);
                        if (preference !== 'off') playSoundFeedback('agent-open');
                    }}
                    options={[
                        {
                            value: 'important',
                            label: 'Important only',
                            description: 'Work status, approvals, agent opens, and load failures.',
                        },
                        {
                            value: 'all',
                            label: 'All feedback',
                            description: 'Also hear toggles, saves, copies, and shares.',
                        },
                        {
                            value: 'off',
                            label: 'Off',
                            description: 'Keep Lemma silent.',
                        },
                    ]}
                />
            </section>

            <section className="office-arrive grid gap-6 py-8 md:grid-cols-[13rem_minmax(0,1fr)] md:gap-10">
                <div>
                    <div className="settings-title-row">
                        <MessageCircle className="h-4 w-4 text-[var(--text-tertiary)]" />
                        <h2 className="settings-title">Your surfaces</h2>
                    </div>
                    <p className="settings-description">Choose which connected surface should answer you.</p>
                </div>
                <div className="max-w-2xl">
                    <UserSurfacesPanel />
                </div>
            </section>
        </PlainPageShell>
    );
}

function normalizeMobileNumber(value: string) {
    return value.replace(/\D/g, "");
}
