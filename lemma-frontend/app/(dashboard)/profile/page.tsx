"use client";

import Link from "next/link";
import { useState } from "react";
import { Check, CheckCircle2, MessageCircle, Plus, Settings, Smartphone, User } from "@/components/ui/icons";
import { useOrganization } from "@/components/dashboard/org-context";
import { useProfile, useUpdateProfile } from "@/lib/hooks/use-user";
import { InlineLoader, StepLoader } from "@/components/brand/loader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PlainPageShell } from "@/components/dashboard/plain-page-shell";
import { ProductIcon } from "@/components/pod/product-icon";
import { QuietEmptyState } from "@/components/shared/empty-state";
import { UserSurfacesPanel } from "@/components/settings/user-surfaces-panel";
import { cn } from "@/lib/utils";

export default function ProfilePage() {
    const { data: profile, isLoading } = useProfile();
    const {
        currentOrg,
        setCurrentOrg,
        organizations,
        isLoading: isLoadingOrganizations,
    } = useOrganization();
    const updateProfile = useUpdateProfile();

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

        updateProfile.mutate({
            first_name: firstName,
            last_name: lastName,
            mobile_number: normalizedMobileNumber || null,
        });
    };

    if (isLoading) {
        return (
            <PlainPageShell
                title="Profile"
                icon={<ProductIcon kind="settings" size="sm" />}
                backHref="/"
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
            backHref="/"
            backLabel="Home"
            meta="Account"
            contentWidthClassName="max-w-5xl"
            contentClassName="pb-16 sm:pb-20"
        >
            <section className="office-room-chrome office-arrive min-h-[36rem] p-6 sm:p-8 lg:p-10">
                <div className="grid gap-8 lg:grid-cols-[minmax(220px,0.68fr)_minmax(0,1fr)] lg:items-start">
                    <aside className="space-y-6">
                        <div>
                            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-[color-mix(in_srgb,var(--delight-soft)_76%,var(--surface-1))] text-base font-semibold text-[var(--delight)] shadow-xs">
                                {(firstName || profile?.email || "U").slice(0, 1).toUpperCase()}
                            </div>
                            <h2 className="mt-4 max-w-sm text-lg font-semibold leading-tight text-[var(--text-primary)]">
                                {firstName || lastName ? `${firstName} ${lastName}`.trim() : "Your account"}
                            </h2>
                            <p className="mt-2 break-all text-sm leading-6 text-[var(--text-secondary)]">{profile?.email}</p>
                        </div>

                        <div className="space-y-3">
                            <div className="flex items-center justify-between gap-3">
                                <p className="type-eyebrow-mono text-[var(--text-tertiary)]">Workspace</p>
                                <div className="flex items-center gap-1">
                                    {currentOrg ? (
                                        <Button asChild variant="ghost" size="sm" className="h-8 px-2 text-[var(--text-secondary)]">
                                            <Link href={`/organizations/${currentOrg.id}/settings/agent-runtimes`}>
                                                <Settings className="mr-1.5 h-3.5 w-3.5" />
                                                Manage
                                            </Link>
                                        </Button>
                                    ) : null}
                                    <Button asChild variant="ghost" size="sm" className="h-8 px-2 text-[var(--text-secondary)]">
                                        <Link href="/organizations/new">
                                            <Plus className="mr-1.5 h-3.5 w-3.5" />
                                            New
                                        </Link>
                                    </Button>
                                </div>
                            </div>

                            {isLoadingOrganizations ? (
                                <div className="flex h-10 items-center gap-2 text-sm text-[var(--text-tertiary)]">
                                    <InlineLoader size="xs" label="Loading workspaces" />
                                </div>
                            ) : null}
                            {!isLoadingOrganizations && organizations.length === 0 ? (
                                <QuietEmptyState className="rounded-md px-2 py-2">No organizations yet.</QuietEmptyState>
                            ) : null}
                            {organizations.length <= 1 && currentOrg ? (
                                <div className="settings-info-row">
                                    <ProductIcon kind="pods" size="sm" />
                                    <span className="min-w-0 flex-1 truncate">{currentOrg.name}</span>
                                    <Check className="h-3.5 w-3.5 text-[var(--delight)]" />
                                </div>
                            ) : null}
                            {organizations.length > 1 ? (
                                <div className="space-y-2">
                                    {organizations.map((organization) => {
                                        const isSelected = currentOrg?.id === organization.id;

                                        return (
                                            <button
                                                key={organization.id}
                                                type="button"
                                                className={cn(
                                                    "profile-org-choice-button settings-choice-row custom-focus-ring group",
                                                    isSelected && "text-[var(--text-primary)]"
                                                )}
                                                data-selected={isSelected}
                                                onClick={() => setCurrentOrg(organization)}
                                            >
                                                <span className="min-w-0 flex-1 truncate">{organization.name}</span>
                                                <Check
                                                    className={cn(
                                                        "h-3.5 w-3.5 shrink-0 transition-opacity",
                                                        isSelected ? "opacity-100 text-[var(--delight)]" : "opacity-0 group-hover:opacity-30"
                                                    )}
                                                />
                                            </button>
                                        );
                                    })}
                                </div>
                            ) : null}
                        </div>
                    </aside>

                    <form onSubmit={handleSubmit} className="space-y-7 lg:border-l lg:border-[var(--border-subtle)] lg:pl-10">
                        <div>
                            <div className="settings-title-row">
                                <User className="h-5 w-5 text-[var(--text-tertiary)]" />
                                <h2 className="settings-title">Personal information</h2>
                            </div>
                            <p className="settings-description">
                                Update your name and the number Lemma uses for messaging channels.
                            </p>
                        </div>

                        <div className="settings-field-stack">
                            <div className="settings-field">
                                <Label htmlFor="email" className="text-[var(--text-secondary)]">Email address</Label>
                                <Input
                                    id="email"
                                    value={profile?.email || ""}
                                    disabled
                                    className="text-[var(--text-tertiary)]"
                                />
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

                            <div className="settings-inline-callout">
                                <div className="settings-panel-icon h-10 w-10">
                                    <Smartphone className="h-4 w-4" />
                                </div>
                                <div className="min-w-0 flex-1">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <h3 className="text-sm font-semibold text-[var(--text-primary)]">Messaging number</h3>
                                        <span className="chip chip-sm chip-pill chip-muted">
                                            <MessageCircle className="h-3 w-3" />
                                            WhatsApp + Telegram
                                        </span>
                                    </div>
                                    <div className="settings-field mt-4">
                                        <Label htmlFor="mobileNumber" className="text-[var(--text-secondary)]">Mobile number</Label>
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
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div className="flex flex-col gap-3 pt-2 sm:flex-row sm:items-center sm:justify-between">
                            <div className="flex min-h-6 items-center text-sm text-[var(--text-secondary)]">
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
                                className="h-10 px-4 transition-transform active:scale-95"
                            >
                                Save changes
                            </Button>
                        </div>
                    </form>
                </div>
            </section>

            <section className="office-room-chrome office-arrive mt-6 p-6 sm:p-8">
                <div className="settings-title-row">
                    <MessageCircle className="h-5 w-5 text-[var(--text-tertiary)]" />
                    <h2 className="settings-title">Your surfaces</h2>
                </div>
                <p className="settings-description">
                    Where pods can reach you. When the same channel could answer you from more than one pod, pick which one wins.
                </p>
                <div className="mt-6">
                    <UserSurfacesPanel />
                </div>
            </section>
        </PlainPageShell>
    );
}

function normalizeMobileNumber(value: string) {
    return value.replace(/\D/g, "");
}
